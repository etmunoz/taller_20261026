# Reglas del proyecto

- Los agentes van en `src/{nombre_agente}/` con `graph.py`, `prompts.py` y `tools.py`.
- Cada nodo tiene su propio prompt en `prompts.py`. No pongas reglas ni prompts dentro de `graph.py`.
- Los agentes se ejecutan desde la raíz con `python main.py {nombre_agente} "mensaje"`.
- `--saltos` imprime los nodos recorridos y, si hubo llamadas a herramientas, el nombre de cada herramienta, sus argumentos y un resumen del resultado.
- `--diagrama` imprime el Mermaid del grafo sin ejecutarlo.
- Las herramientas MCP se cargan desde la URL `API_COLOMBIA_MCP_URL` de `.env`, en `tools.py`, usando `langchain-mcp-adapters` (`MultiServerMCPClient`, transporte `streamable_http`). Nunca reescribas herramientas que el MCP ya ofrece.
- Los scripts sueltos van en `scripts/`; las pruebas en `tests/` y se ejecutan con pytest. Las pruebas de promptfoo van en `promptfoo/`.
- Verifica que Node.js 18 o superior esté disponible con `node --version`, porque promptfoo se ejecuta con `npx`. Si no está disponible, indica cómo instalarlo según el sistema operativo.
