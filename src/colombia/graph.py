"""Grafo del agente Colombia: guardia de entrada, agente ReAct y respuesta bloqueada."""

import os
import warnings
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from src.colombia.prompts import (
    GUARDIA_PROMPT,
    RESPUESTA_BLOQUEADA_POR_DEFECTO,
    RESPUESTAS_BLOQUEADAS,
    SYSTEM_PROMPT,
)
from src.colombia.tools import get_tools

load_dotenv()
# Gemini ignora `temperature` en algunos modelos recientes; el nodo guardia la
# declara igualmente (temperatura 0) y este filtro evita ruido en la CLI.
warnings.filterwarnings("ignore", message=r".*fixed sampling defaults.*")
memory = MemorySaver()


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


class Estado(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    veredicto: NotRequired[dict[str, Any]]


def _texto(message: AnyMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return str(message.content)


async def _construir():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Falta GOOGLE_API_KEY en .env; configura tu clave para ejecutar el agente.")
    model_name = os.getenv("MODELO", "gemini-3.5-flash-lite")
    model = ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key)
    gate_model = ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key, temperature=0)
    clasificador = gate_model.with_structured_output(Veredicto)

    async def guardia_entrada(state: Estado) -> dict[str, Any]:
        verdict = await clasificador.ainvoke(
            [SystemMessage(content=GUARDIA_PROMPT), HumanMessage(content=_texto(state["messages"][-1]))]
        )
        if verdict is None:
            verdict = Veredicto(permitido=True, categoria="ok", motivo="No se pudo clasificar el mensaje.")
        return {"veredicto": verdict.model_dump()}

    def decidir(state: Estado) -> str:
        veredicto = state.get("veredicto") or {}
        return "agente" if veredicto.get("permitido") else "respuesta_bloqueada"

    async def respuesta_bloqueada(state: Estado) -> dict[str, Any]:
        veredicto = state.get("veredicto") or {}
        texto = RESPUESTAS_BLOQUEADAS.get(str(veredicto.get("categoria")), RESPUESTA_BLOQUEADA_POR_DEFECTO)
        return {"messages": [AIMessage(content=texto)]}

    tools = await get_tools()
    agent = create_react_agent(model=model, tools=tools, prompt=SYSTEM_PROMPT, name="agente")

    builder = StateGraph(Estado)
    builder.add_node("guardia_entrada", guardia_entrada)
    builder.add_node("agente", agent)
    builder.add_node("respuesta_bloqueada", respuesta_bloqueada)
    builder.add_edge(START, "guardia_entrada")
    builder.add_conditional_edges(
        "guardia_entrada",
        decidir,
        {"agente": "agente", "respuesta_bloqueada": "respuesta_bloqueada"},
    )
    builder.add_edge("agente", END)
    builder.add_edge("respuesta_bloqueada", END)
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

