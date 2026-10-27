"""Descubre y prueba las herramientas del servidor MCP de API Colombia."""

import asyncio
import json
import os
import time
from collections.abc import Mapping
from typing import Any

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table


MAX_RESPONSE_CHARS = 1200
console = Console()


def parse_result(value: Any) -> Any:
    """Desenvuelve el formato habitual de respuesta de herramientas MCP."""
    if isinstance(value, list) and all(isinstance(item, Mapping) and "text" in item for item in value):
        blocks = [item["text"] for item in value]
        value = blocks[0] if len(blocks) == 1 else blocks
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def describe(value: Any) -> str:
    """Convierte la respuesta a texto y la recorta para mostrarla en consola."""
    # MCP suele devolver bloques de texto con JSON serializado: desenvolverlos
    # permite que la respuesta sea legible y no una lista de objetos anidados.
    value = parse_result(value)
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
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
    arguments = []
    for name, details in schema.items():
        kind = details.get("type", "object") if isinstance(details, Mapping) else "desconocido"
        if isinstance(kind, list):
            kind = " | ".join(kind)
        arguments.append((name, str(kind)))
    return arguments


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
        console.print("[bold red]Error:[/bold red] falta API_COLOMBIA_MCP_URL. Configúrala en .env.")
        return

    try:
        client = MultiServerMCPClient(
            {"api_colombia": {"transport": "streamable_http", "url": url}}
        )
        tools = await client.get_tools(server_name="api_colombia")
        console.print("\n[bold cyan]API COLOMBIA MCP[/bold cyan]  [dim]Descubrimiento de herramientas y recursos[/dim]\n")
        tools_table = Table(title="Herramientas disponibles", header_style="bold magenta", show_lines=True)
        tools_table.add_column("Nombre", style="cyan", no_wrap=True)
        tools_table.add_column("Descripción")
        tools_table.add_column("Argumentos", style="green")
        for tool in sorted(tools, key=lambda item: item.name):
            first_line = (tool.description or "").splitlines()[0] if tool.description else ""
            args = "\n".join(f"{name}: {kind}" for name, kind in arguments_for(tool)) or "sin argumentos"
            tools_table.add_row(tool.name, first_line or "Sin descripción", args)
        console.print(tools_table)

        by_name = {tool.name: tool for tool in tools}

        async def call_tool(name: str, payload: dict[str, Any], *, show_response: bool = True) -> Any:
            tool = by_name.get(name)
            if tool is None:
                raise RuntimeError(f"El servidor no expone la herramienta requerida '{name}'.")
            started = time.perf_counter()
            result = await tool.ainvoke(payload)
            elapsed_ms = (time.perf_counter() - started) * 1000
            arguments = json.dumps(payload, ensure_ascii=False)
            if show_response:
                response = describe(result)
                try:
                    json.loads(response)
                    renderable = Syntax(response, "json", theme="monokai", word_wrap=True)
                except json.JSONDecodeError:
                    renderable = response
                console.print(f"[dim]Argumentos:[/dim] {arguments}")
                console.print(Panel(renderable, title=f"[bold cyan]{name}[/bold cyan]", subtitle=f"{elapsed_ms:.1f} ms", expand=False, border_style="blue"))
            else:
                console.print(f"[dim]OK  {name}[/dim]  [green]{elapsed_ms:.1f} ms[/green]")
            return result

        resources_result = await call_tool("list_colombia_resources", {}, show_response=False)
        resources = parse_result(resources_result)
        if isinstance(resources, list):
            resources_table = Table(title="Recursos disponibles", header_style="bold magenta", show_lines=True)
            resources_table.add_column("Clave", style="cyan", no_wrap=True)
            resources_table.add_column("Descripción")
            for resource in sorted(resources, key=lambda item: str(item.get("resource", "")) if isinstance(item, Mapping) else ""):
                if isinstance(resource, Mapping):
                    resources_table.add_row(str(resource.get("resource", "(sin clave)")), str(resource.get("description", "Sin descripción")))
            console.print(resources_table)
        else:
            console.print("[yellow]No se pudo interpretar la lista de recursos devuelta por el servidor.[/yellow]")
        await call_tool("get_api_reference", {"resource": "departments"})
        search_result = await call_tool(
            "search_items", {"resource": "departments", "keyword": "Antioquia"}
        )
        department_id = find_department_id(search_result)
        if department_id is None:
            console.print("[yellow]No se encontró un id de departamento; se omite la consulta de áreas naturales.[/yellow]")
            return
        await call_tool("get_natural_areas_by_department", {"departmentId": department_id})
    except Exception as exc:
        console.print(f"[bold red]Error al conectar o consultar API Colombia MCP:[/bold red] {exc}")


if __name__ == "__main__":
    asyncio.run(main())
