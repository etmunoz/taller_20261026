# Pruebas de promptfoo — agente Colombia

Evalúa el agente completo tal como lo usa la CLI: `guardia_entrada`, agente ReAct
con las tools MCP, `guardia_salida` y las respuestas fijas de bloqueo. El provider
crea un `thread_id` nuevo por caso, así que no hay memoria compartida entre pruebas.

## Requisitos

- `.venv` con las dependencias del proyecto (`uv sync`).
- Node.js 18 o superior (`node --version`).
- `GOOGLE_API_KEY` y `API_COLOMBIA_MCP_URL` definidas en el entorno o en `.env`.
  Promptfoo las necesita al arrancar el provider; el provider también carga `.env`.

## Cómo correr

Desde la raíz del proyecto, con el entorno activado:

```powershell
.\.venv\Scripts\Activate.ps1
$env:GOOGLE_API_KEY
$env:API_COLOMBIA_MCP_URL
npx --yes promptfoo@0.125.1 eval -c promptfoo/promptfooconfig.yaml --no-cache
```

`--no-cache` es importante para que el assert de latencia mida una llamada real y
para que `--repeat 3` no reutilice respuestas cacheadas. El provider declara
`workers: 4`, igual que la concurrencia por defecto de promptfoo; así la latencia
reportada no incluye la espera en la cola de un único proceso Python.

Ver la tabla interactiva en el navegador:

```powershell
npx --yes promptfoo@0.125.1 view
```

Depurar un solo caso (el patrón busca en `description`):

```powershell
npx --yes promptfoo@0.125.1 eval -c promptfoo/promptfooconfig.yaml --filter-pattern "Antioquia" --no-cache
```

Medir consistencia (tres ejecuciones por caso):

```powershell
npx --yes promptfoo@0.125.1 eval -c promptfoo/promptfooconfig.yaml --repeat 3 --no-cache
```

Después de `--repeat 3`, abre `npx promptfoo view` y compara las tres respuestas de
cada caso. **Consistencia no es un assert**: se revisa visualmente si las respuestas
cambian de contenido, de formato o de ruta.

## Métricas y pesos

Cada assert tiene un campo `metric` con uno de estos nombres, que promptfoo agrupa
en los chips de la parte superior de la tabla:

| Métrica | Qué evalúa | Peso |
|---|---|---|
| Precision | La información es correcta y sin errores evidentes. | 1 |
| Respaldo | Nombres, cifras y lugares vienen de una fuente citada o se reconoce que no hay dato. | 2 |
| Completitud | Responde todas las partes de la pregunta (solo en casos de contenido). | 1 |
| Cumplimiento | Solo Colombia, no revela instrucciones, no usa datos personales, no promete acciones. | 2 |
| Consistencia | No usa assert; se revisa con `--repeat 3` y la vista web. | — |

El assert de latencia (menos de 20 000 ms) se reporta bajo **Cumplimiento** para que
todos los asserts usen las cinco métricas acordadas. Los rechazos correctos de los
guardrails cuentan como aprobados en Precision, Respaldo y Cumplimiento.

## Qué significa cada columna

- **Provider**: el agente evaluado; aquí siempre es `colombia-agent` (el provider
  Python `provider.py`).
- **Prompt / caso**: la pregunta enviada (`vars.pregunta`). La descripción del caso
  aparece como título de la fila.
- **Pass rate**: porcentaje de casos (o repeticiones) en los que pasaron *todos*
  sus asserts; `100%` significa que ningún criterio quedó incumplido.
- **Score**: promedio ponderado de los asserts; los de Respaldo y Cumplimiento
  pesan el doble. Un caso puede tener score alto y aun así aparecer como fallido
  si un assert de peso no pasó.
- **Latency**: tiempo de la llamada al provider, en milisegundos. Se compara con el
  umbral de 20 000 ms.
- **Failures / asserts**: cuando un caso falla, muestra el assert incumplido y el
  motivo que dio el grader o la comparación de texto.

En la vista web, los chips superiores muestran el porcentaje de aprobación por
métrica; úsalos para detectar si el problema es de respaldo, de cumplimiento o de
completitud. La metadata de cada salida incluye `ruta`, `tools`, `veredicto` y
`revision` para inspeccionar qué hizo el grafo.

## Solución de problemas

Si `npx` falla con `ERR_MODULE_NOT_FOUND` y una ruta a `openai/index.mjs`, el caché
de `npx` quedó incompleto. Bórralo y vuelve a instalar:

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\npm-cache\_npx"
npx --yes promptfoo@0.125.1 --version
```

Si cambias la concurrencia de promptfoo, ajusta también `config.workers` del
provider al mismo número para que la latencia no mida la cola del proceso Python.
