"""Provider de promptfoo para el agente Colombia completo.

Promptfoo llama a ``call_api`` una vez por caso (y por repetición). Cada llamada
crea un ``thread_id`` nuevo para que la memoria del agente no mezcle casos.
"""

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from main import TraceHandler  # noqa: E402
from src.colombia.graph import get_graph  # noqa: E402


def _texto(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


async def _ejecutar(pregunta: str) -> dict[str, Any]:
    graph = await get_graph()
    trace = TraceHandler()
    resultado = await graph.ainvoke(
        {"messages": [{"role": "user", "content": pregunta}]},
        config={
            "callbacks": [trace],
            "configurable": {"thread_id": f"promptfoo-{uuid.uuid4()}"},
        },
    )
    messages = resultado.get("messages", [])
    return {
        "output": _texto(messages[-1]) if messages else "",
        "metadata": {
            "ruta": " -> ".join(trace.nodes),
            "tools": list(dict.fromkeys(item[0] for item in trace.tools)),
            "veredicto": resultado.get("veredicto"),
            "revision": resultado.get("revision"),
        },
    }


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    try:
        return asyncio.run(_ejecutar(str(prompt)))
    except Exception as exc:  # promptfoo muestra el error en la fila del caso
        return {"error": f"{type(exc).__name__}: {exc}"}
