"""Descubre y prueba las herramientas del servidor MCP de API Colombia."""

import asyncio
import json
import os
import time
from collections.abc import Mapping
from typing import Any

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient


MAX_RESPONSE_CHARS = 1200


def describe(value: Any) -> str:
    """Convierte la respuesta a texto y la recorta para mostrarla en consola."""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > MAX_RESPONSE_CHARS:
        return text[:MAX_RESPONSE_CHARS] + "... (recortado)"
    return text


def arguments_for(tool: Any) -> list[tuple[str, str]]:
    schema = getattr(tool, "args", None) or {}
    if not schema and hasattr(tool, "args_schema") and tool.args_schema:
        args_schema = tool.args_schema
        if isinstance(args_schema, Mapping):
            schema = args_schema.get("properties", args_schema)
        elif callable(getattr(args_schema, "model_json_schema", None)):
            schema = args_schema.model_json_schema().get("properties", {})
    if isinstance(schema, Mapping) and "properties" in schema:
        schema = schema["properties"]
    return [
        (name, str(details.get("type", "object")) if isinstance(details, Mapping) else "desconocido")
        for name, details in schema.items()
    ]


def find_department_id(value: Any) -> Any:
    """Busca recursivamente un id de departamento en la respuesta MCP."""
    if isinstance(value, str):
        try:
            return find_department_id(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(value, list):
        for item in value:
            found = find_department_id(item)
            if found is not None:
                return found
    elif isinstance(value, Mapping):
        # MCP wraps text results with its own correlation `id`; inspect the
        # embedded text instead so that ID is never mistaken for a department.
        if "text" in value:
            return find_department_id(value["text"])
        if "error" in value:
            return None
        for key in ("departmentId", "department_id", "Id", "id"):
            if value.get(key) is not None:
                return value[key]
        for item in value.values():
            found = find_department_id(item)
            if found is not None:
                return found
    return None


async def main() -> None:
    load_dotenv()
    url = os.getenv("API_COLOMBIA_MCP_URL")
    if not url:
        print("Error: falta API_COLOMBIA_MCP_URL. Configúrala en .env.")
        return

    try:
        client = MultiServerMCPClient(
            {"api_colombia": {"transport": "streamable_http", "url": url}}
        )
        tools = await client.get_tools(server_name="api_colombia")
        print("Herramientas disponibles (argumentos: nombre:tipo)")
        print("| Nombre | Descripción | Argumentos |")
        print("|---|---|---|")
        for tool in sorted(tools, key=lambda item: item.name):
            first_line = (tool.description or "").splitlines()[0] if tool.description else ""
            args = ", ".join(f"{name}:{kind}" for name, kind in arguments_for(tool)) or "—"
            print(f"| {tool.name} | {first_line} | {args} |")

        by_name = {tool.name: tool for tool in tools}

        async def call_tool(name: str, payload: dict[str, Any]) -> Any:
            tool = by_name.get(name)
            if tool is None:
                raise RuntimeError(f"El servidor no expone la herramienta requerida '{name}'.")
            started = time.perf_counter()
            result = await tool.ainvoke(payload)
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(f"\n{name}({json.dumps(payload, ensure_ascii=False)}) — {elapsed_ms:.1f} ms")
            print(describe(result))
            return result

        await call_tool("list_colombia_resources", {})
        await call_tool("get_api_reference", {"resource": "departments"})
        search_result = await call_tool(
            "search_items", {"resource": "departments", "keyword": "Antioquia"}
        )
        department_id = find_department_id(search_result)
        if department_id is None:
            print("\nNo se encontró un id de departamento en la respuesta; se omite la segunda prueba.")
            return
        await call_tool("get_natural_areas_by_department", {"departmentId": department_id})
    except Exception as exc:
        print(f"Error al conectar o consultar API Colombia MCP: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
