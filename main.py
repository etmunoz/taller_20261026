"""CLI para ejecutar agentes definidos en src/{nombre_agente}/graph.py."""

import argparse
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
        name = kwargs.get("name") or (serialized or {}).get("name")
        if name and name not in {"LangGraph", "RunnableSequence"}:
            self.nodes.append(str(name))

    def on_tool_start(self, serialized: dict[str, Any] | None, input_str: str, **kwargs: Any) -> None:
        self.tools.append((str(kwargs.get("name") or (serialized or {}).get("name") or "tool"), input_str, ""))

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        if self.tools:
            name, arguments, _ = self.tools[-1]
            self.tools[-1] = (name, arguments, str(output)[:500])


def main() -> int:
    parser = argparse.ArgumentParser(description="Ejecuta un agente del proyecto.")
    parser.add_argument("agente", help="Nombre del agente dentro de src/")
    parser.add_argument("mensaje", nargs="?", help="Mensaje para el agente")
    parser.add_argument("--saltos", action="store_true", help="Muestra nodos y herramientas ejecutadas")
    parser.add_argument("--diagrama", action="store_true", help="Muestra Mermaid sin ejecutar el grafo")
    args = parser.parse_args()

    load_dotenv()
    try:
        module = importlib.import_module(f"src.{args.agente}.graph")
    except ModuleNotFoundError as exc:
        if exc.name == f"src.{args.agente}" or exc.name == "src":
            parser.error(f"No existe el agente '{args.agente}' en src/{args.agente}/")
        raise

    graph = getattr(module, "graph", None)
    if graph is None:
        parser.error(f"src/{args.agente}/graph.py debe exponer una variable 'graph'")

    if args.diagrama:
        print(graph.get_graph().draw_mermaid())
        return 0
    if args.mensaje is None:
        parser.error("mensaje requerido para ejecutar el agente")

    trace = TraceHandler() if args.saltos else None
    config = {"callbacks": [trace]} if trace else None
    result = graph.invoke({"messages": [{"role": "user", "content": args.mensaje}]}, config=config)
    if args.saltos and trace:
        print("Nodos: " + (" -> ".join(trace.nodes) or "(sin eventos de nodo detectados)"))
        for name, arguments, summary in trace.tools:
            print(f"Tool: {name}\n  Argumentos: {arguments}\n  Resultado: {summary}")
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
