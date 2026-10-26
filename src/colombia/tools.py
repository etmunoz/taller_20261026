"""Carga las herramientas ofrecidas por el servidor MCP de API Colombia."""

import os

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()


async def get_tools():
    url = os.getenv("API_COLOMBIA_MCP_URL")
    if not url:
        raise RuntimeError("Falta API_COLOMBIA_MCP_URL en el archivo .env")
    client = MultiServerMCPClient(
        {"api_colombia": {"transport": "streamable_http", "url": url}}
    )
    return await client.get_tools(server_name="api_colombia")
