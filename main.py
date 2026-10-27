"""CLI para ejecutar agentes definidos en src/{nombre_agente}/graph.py."""

import argparse
import asyncio
import importlib
import sys
from typing import Any

from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler


class TraceHandler(BaseCallbackHandler):
    """Registra nodos y llamadas a herramientas durante la ejecución."""

    def __init__(self) -> None:
        self.nodes: list[str] = []
        self.tools: list[tuple[str, str, str]] = []

    def on_chain_start(self, serialized: dict[str, Any] | None, inputs: dict[str, Any], **kwargs: Any) -> None:
        name = (kwargs.get("metadata") or {}).get("langgraph_node")
        if name and (not self.nodes or self.nodes[-1] != str(name)):
            self.nodes.append(str(name))

    def on_tool_start(self, serialized: dict[str, Any] | None, input_str: str, **kwargs: Any) -> None:
        self.tools.append((str(kwargs.get("name") or (serialized or {}).get("name") or "tool"), input_str, ""))

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        if self.tools:
            name, arguments, _ = self.tools[-1]
            self.tools[-1] = (name, arguments, str(output)[:500])


async def run(args: argparse.Namespace) -> int:
    load_dotenv()
    try:
        module = importlib.import_module(f"src.{args.agente}.graph")
    except ModuleNotFoundError as exc:
        if exc.name == f"src.{args.agente}" or exc.name == "src":
            raise ValueError(f"No existe el agente '{args.agente}' en src/{args.agente}/") from exc
        raise

    if args.diagrama:
        diagrama = getattr(module, "get_diagrama", None)
        print((await diagrama()).rstrip() if diagrama else graph.get_graph().draw_mermaid().rstrip())
        return 0

    graph_factory = getattr(module, "get_graph", None)
    graph = await graph_factory() if graph_factory else getattr(module, "graph", None)
    if graph is None:
        raise ValueError(f"src/{args.agente}/graph.py debe exponer 'graph' o 'get_graph()'")

    if args.mensaje is None:
        raise ValueError("mensaje requerido para ejecutar el agente")

    trace = TraceHandler() if args.saltos else None
    config = {"callbacks": [trace], "configurable": {"thread_id": args.thread_id}} if trace else {
        "configurable": {"thread_id": args.thread_id}
    }
    result = await graph.ainvoke({"messages": [{"role": "user", "content": args.mensaje}]}, config=config)
    if args.saltos and trace:
        print("Ruta: " + (" -> ".join(trace.nodes) or "(sin eventos de nodo detectados)"))
        veredicto = result.get("veredicto")
        if isinstance(veredicto, dict):
            print(
                f"Veredicto: permitido={veredicto.get('permitido')} "
                f"categoria={veredicto.get('categoria')} motivo={veredicto.get('motivo')}"
            )
        for name, arguments, summary in trace.tools:
            print(f"Tool: {name}\n  Argumentos: {arguments}\n  Resultado: {summary}")
    messages = result.get("messages", [])
    if messages:
        print(_message_text(messages[-1]))
    else:
        print(result)
    return 0


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return str(content)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ejecuta un agente del proyecto.")
    parser.add_argument("agente", help="Nombre del agente dentro de src/")
    parser.add_argument("mensaje", nargs="?", help="Mensaje para el agente")
    parser.add_argument("--saltos", action="store_true", help="Muestra nodos y herramientas ejecutadas")
    parser.add_argument("--diagrama", action="store_true", help="Muestra Mermaid sin ejecutar el grafo")
    parser.add_argument("--thread-id", default="default", help="Identificador de conversación con memoria")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except ValueError as exc:
        parser.error(str(exc))
    except Exception as exc:
        message = str(exc)
        if "API_KEY_INVALID" in message or "API key not valid" in message:
            print("Error: GOOGLE_API_KEY no es válida. Reemplázala por una clave válida en .env.", file=sys.stderr)
        else:
            print(f"Error al ejecutar el agente: {message}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
