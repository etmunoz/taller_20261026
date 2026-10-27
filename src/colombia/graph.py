"""Grafo del agente Colombia: guardia de entrada, agente ReAct y guardia de salida."""

import json
import os
import re
import warnings
from collections.abc import Mapping
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from src.colombia.prompts import (
    CORRECCION_PROMPT,
    GUARDIA_PROMPT,
    RESPUESTA_BLOQUEADA_POR_DEFECTO,
    RESPUESTAS_BLOQUEADAS,
    REVISION_PROMPT,
    SIN_RESPALDO,
    SYSTEM_PROMPT,
)
from src.colombia.tools import LimiteHerramientas, get_tools

load_dotenv()
# Gemini ignora `temperature` en algunos modelos recientes; los nodos guardia la
# declaran igualmente (temperatura 0) y este filtro evita ruido en la CLI.
warnings.filterwarnings("ignore", message=r".*fixed sampling defaults.*")
memory = MemorySaver()

MAX_CARACTERES_RESULTADO = 6000
MAX_CARACTERES_REVISION = 30000
MAX_PALABRAS = 150


class Veredicto(BaseModel):
    permitido: bool
    categoria: Literal[
        "ok",
        "fuera_de_tema",
        "inyeccion",
        "datos_personales",
        "accion_no_soportada",
    ]
    motivo: str


class Revision(BaseModel):
    aprobada: bool
    problemas: list[Literal["dato_no_respaldado", "formato", "lenguaje", "datos_personales"]]
    detalle: str


class Estado(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    veredicto: NotRequired[dict[str, Any]]
    resultados_tools: NotRequired[list[dict[str, str]]]
    revision: NotRequired[dict[str, Any] | None]
    rechazos: NotRequired[int]


def _texto(message: AnyMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        partes = []
        for block in content:
            if isinstance(block, Mapping):
                partes.append(str(block.get("text", "")))
            else:
                partes.append(str(block))
        return "".join(partes)
    return str(content)


def _resultados_de_la_vuelta(messages: list[AnyMessage]) -> list[dict[str, str]]:
    """Recoge los resultados de tools posteriores al último mensaje del usuario."""
    resultados: list[dict[str, str]] = []
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, ToolMessage):
            resultados.append(
                {"tool": message.name or "herramienta", "resultado": _texto(message)[:MAX_CARACTERES_RESULTADO]}
            )
    return list(reversed(resultados))


def _fusionar(previos: list[dict[str, str]], nuevos: list[dict[str, str]]) -> list[dict[str, str]]:
    vistos = {(item["tool"], item["resultado"]) for item in previos}
    return previos + [item for item in nuevos if (item["tool"], item["resultado"]) not in vistos]


def _cumple_formato(texto: str) -> bool:
    tiene_fuente = any(line.strip().lower().startswith("fuente:") for line in texto.splitlines())
    return tiene_fuente and len(texto.split()) <= MAX_PALABRAS


def _fuente_invalida(texto: str, resultados: list[dict[str, str]]) -> bool:
    """La línea Fuente solo puede citar herramientas usadas en esta vuelta."""
    nombres = {item["tool"] for item in resultados}
    for line in texto.splitlines():
        if not line.strip().lower().startswith("fuente:"):
            continue
        citadas = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", line)
        return any(citada not in nombres for citada in citadas if citada.lower() != "fuente")
    return False


def _datos(texto: str) -> Any:
    try:
        return json.loads(texto)
    except (json.JSONDecodeError, TypeError):
        return None


def _nombres(texto: str) -> list[str]:
    """Extrae nombres de los resultados para el mensaje de respaldo."""
    datos = _datos(texto)
    encontrados: list[str] = []

    def recorrer(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                recorrer(item)
        elif isinstance(value, Mapping):
            if isinstance(value.get("name"), str):
                encontrados.append(value["name"])
            for item in value.values():
                recorrer(item)

    recorrer(datos)
    if not encontrados:
        # El resultado pudo quedar truncado y no ser JSON válido.
        encontrados = re.findall(r'"name"\s*:\s*"([^"]+)"', texto)
    return list(dict.fromkeys(encontrados))


def _mensaje_respaldo(resultados: list[dict[str, str]]) -> str:
    lineas = [SIN_RESPALDO, "", "Lo que sí está respaldado por las consultas:"]
    for item in resultados:
        nombres = _nombres(item["resultado"])
        detalle = ", ".join(nombres[:5]) if nombres else "datos disponibles en el resultado"
        lineas.append(f"- {item['tool']}: {detalle}")
    if len(lineas) == 3:
        lineas.append("- No hubo resultados de herramientas en esta pregunta.")
    return "\n".join(lineas)


async def _construir():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Falta GOOGLE_API_KEY en .env; configura tu clave para ejecutar el agente.")
    model_name = os.getenv("MODELO", "gemini-3.5-flash-lite")
    model = ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key)
    gate_model = ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key, temperature=0)
    clasificador = gate_model.with_structured_output(Veredicto)
    revisor = gate_model.with_structured_output(Revision)
    limite = LimiteHerramientas()

    async def guardia_entrada(state: Estado) -> dict[str, Any]:
        limite.reiniciar()
        verdict = await clasificador.ainvoke(
            [SystemMessage(content=GUARDIA_PROMPT), HumanMessage(content=_texto(state["messages"][-1]))]
        )
        if verdict is None:
            verdict = Veredicto(permitido=True, categoria="ok", motivo="No se pudo clasificar el mensaje.")
        return {
            "veredicto": verdict.model_dump(),
            "resultados_tools": [],
            "revision": None,
            "rechazos": 0,
        }

    def decidir_entrada(state: Estado) -> str:
        veredicto = state.get("veredicto") or {}
        return "agente" if veredicto.get("permitido") else "respuesta_bloqueada"

    async def respuesta_bloqueada(state: Estado) -> dict[str, Any]:
        veredicto = state.get("veredicto") or {}
        texto = RESPUESTAS_BLOQUEADAS.get(str(veredicto.get("categoria")), RESPUESTA_BLOQUEADA_POR_DEFECTO)
        return {"messages": [AIMessage(content=texto)]}

    async def guardia_salida(state: Estado) -> dict[str, Any]:
        borrador = _texto(state["messages"][-1])
        resultados = _fusionar(
            state.get("resultados_tools", []),
            _resultados_de_la_vuelta(state["messages"]),
        )
        contexto = json.dumps(resultados, ensure_ascii=False)[:MAX_CARACTERES_REVISION]
        revision = await revisor.ainvoke(
            [
                SystemMessage(content=REVISION_PROMPT),
                HumanMessage(content=f"Resultados de herramientas:\n{contexto}\n\nBorrador:\n{borrador}"),
            ]
        )
        problemas = list(dict.fromkeys(revision.problemas)) if revision else []
        detalle = revision.detalle if revision else ""
        if not _cumple_formato(borrador) and "formato" not in problemas:
            problemas.append("formato")
        if _fuente_invalida(borrador, resultados) and "dato_no_respaldado" not in problemas:
            problemas.append("dato_no_respaldado")
        aprobada = bool(revision and revision.aprobada) and not problemas
        resultado: dict[str, Any] = {
            "revision": {"aprobada": aprobada, "problemas": problemas, "detalle": detalle},
            "resultados_tools": resultados,
        }
        if aprobada:
            return resultado
        rechazos = state.get("rechazos", 0) + 1
        resultado["rechazos"] = rechazos
        if rechazos == 1:
            resultado["messages"] = [
                HumanMessage(
                    content=CORRECCION_PROMPT.format(problemas=", ".join(problemas), detalle=detalle)
                )
            ]
        return resultado

    def decidir_salida(state: Estado) -> str:
        revision = state.get("revision") or {}
        if revision.get("aprobada"):
            return "fin"
        return "agente" if state.get("rechazos", 0) < 2 else "respuesta_no_confirmada"

    async def respuesta_no_confirmada(state: Estado) -> dict[str, Any]:
        return {"messages": [AIMessage(content=_mensaje_respaldo(state.get("resultados_tools", [])))]}

    tools = await get_tools(limite)
    agent = create_react_agent(model=model, tools=tools, prompt=SYSTEM_PROMPT, name="agente")

    builder = StateGraph(Estado)
    builder.add_node("guardia_entrada", guardia_entrada)
    builder.add_node("agente", agent)
    builder.add_node("guardia_salida", guardia_salida)
    builder.add_node("respuesta_bloqueada", respuesta_bloqueada)
    builder.add_node("respuesta_no_confirmada", respuesta_no_confirmada)
    builder.add_edge(START, "guardia_entrada")
    builder.add_conditional_edges(
        "guardia_entrada",
        decidir_entrada,
        {"agente": "agente", "respuesta_bloqueada": "respuesta_bloqueada"},
    )
    builder.add_edge("agente", "guardia_salida")
    builder.add_conditional_edges(
        "guardia_salida",
        decidir_salida,
        {
            "fin": END,
            "agente": "agente",
            "respuesta_no_confirmada": "respuesta_no_confirmada",
        },
    )
    builder.add_edge("respuesta_bloqueada", END)
    builder.add_edge("respuesta_no_confirmada", END)
    return builder.compile(checkpointer=memory), tools


async def get_graph():
    graph, _ = await _construir()
    return graph


def _mermaid_con_tools(mermaid: str, tools: list[Any]) -> str:
    """Añade al Mermaid del grafo un subgrafo con las herramientas MCP disponibles."""
    nombres = sorted(tool.name for tool in tools)
    lineas = [mermaid.rstrip(), "", '\tsubgraph herramientas_mcp["Herramientas MCP"]']
    for name in nombres:
        lineas.append(f'\t\t{name}["{name}"]')
    lineas.append("\tend")
    for name in nombres:
        lineas.append(f"\tagente --> {name}")
    return "\n".join(lineas) + "\n"


async def get_diagrama() -> str:
    graph, tools = await _construir()
    return _mermaid_con_tools(graph.get_graph().draw_mermaid(), tools)
