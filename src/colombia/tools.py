"""Carga las herramientas del MCP de API Colombia con guardrails."""

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()

logger = logging.getLogger(__name__)

TOOLS_PERMITIDAS = {
    "list_colombia_resources",
    "get_api_reference",
    "search_items",
    "get_items_by_name",
    "get_item_by_id",
    "list_items",
    "list_items_paged",
    "get_cities_by_department",
    "get_natural_areas_by_department",
    "get_touristic_attractions_by_department",
    "get_country_info",
}

MAX_LLAMADAS_POR_PREGUNTA = 6
TIMEOUT_SEGUNDOS = 10
MAX_RESULTADOS_PAGINA = 50
MAX_LONGITUD_TEXTO = 60


class LimiteHerramientas:
    """Cuenta y limita las llamadas a herramientas dentro de una pregunta."""

    def __init__(self, maximo: int = MAX_LLAMADAS_POR_PREGUNTA) -> None:
        self.maximo = maximo
        self.usadas = 0

    def reiniciar(self) -> None:
        self.usadas = 0

    @property
    def disponible(self) -> bool:
        return self.usadas < self.maximo


def _validar_argumentos(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Ajusta argumentos al rango permitido sin romper la llamada."""
    ajustados = dict(kwargs)
    if "pageSize" in ajustados and ajustados["pageSize"] is not None:
        try:
            ajustados["pageSize"] = max(1, min(int(ajustados["pageSize"]), MAX_RESULTADOS_PAGINA))
        except (TypeError, ValueError):
            ajustados["pageSize"] = MAX_RESULTADOS_PAGINA
    for key in ("keyword", "name"):
        value = ajustados.get(key)
        if isinstance(value, str) and len(value) > MAX_LONGITUD_TEXTO:
            ajustados[key] = value[:MAX_LONGITUD_TEXTO]
    return ajustados


def _contenido(response: Any) -> Any:
    """Extrae el texto de la ToolMessage que produce la tool MCP original."""
    content = response.content if isinstance(response, ToolMessage) else response
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, Mapping) else str(block) for block in content
        )
    return content


def _envolver(tool: BaseTool, limite: LimiteHerramientas) -> BaseTool:
    """Envuelve una tool con timeout, tope de llamadas y validación de argumentos."""

    async def _call(**kwargs: Any) -> Any:
        if not limite.disponible:
            return (
                f"Límite de {limite.maximo} llamadas a herramientas alcanzado. "
                "Responde con la información que ya tienes."
            )
        limite.usadas += 1
        try:
            # Sin callbacks anidados: la tool interna no debe duplicar los eventos
            # de --saltos que ya emite el envoltorio.
            salida = await asyncio.wait_for(
                tool.ainvoke(_validar_argumentos(kwargs), config={"callbacks": []}),
                timeout=TIMEOUT_SEGUNDOS,
            )
            return _contenido(salida)
        except asyncio.TimeoutError:
            return (
                f"La herramienta {tool.name} tardó más de {TIMEOUT_SEGUNDOS} segundos. "
                "Responde con la información que ya tienes."
            )

    return tool.model_copy(update={"coroutine": _call, "response_format": "content"})


async def get_tools(limite: LimiteHerramientas | None = None) -> list[BaseTool]:
    url = os.getenv("API_COLOMBIA_MCP_URL")
    if not url:
        raise RuntimeError("Falta API_COLOMBIA_MCP_URL en el archivo .env")
    client = MultiServerMCPClient(
        {"api_colombia": {"transport": "streamable_http", "url": url}}
    )
    tools_servidor = await client.get_tools(server_name="api_colombia")

    for tool in tools_servidor:
        if tool.name not in TOOLS_PERMITIDAS:
            logger.warning("Tool MCP ignorada por la allowlist: %s", tool.name)
    faltantes = TOOLS_PERMITIDAS - {tool.name for tool in tools_servidor}
    if faltantes:
        logger.warning(
            "El servidor MCP no expone estas tools permitidas: %s",
            ", ".join(sorted(faltantes)),
        )

    limite = limite or LimiteHerramientas()
    return [_envolver(tool, limite) for tool in tools_servidor if tool.name in TOOLS_PERMITIDAS]
