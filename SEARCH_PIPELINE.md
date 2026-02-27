# Pipeline de búsqueda — de la query del usuario a la respuesta

Este documento traza el camino completo que sigue una pregunta del usuario desde que llega al backend hasta que se genera y devuelve la respuesta.

---

## Visión general

```
Usuario
  │
  ▼
[WebSocket /ws/chat]           ← frontend → server.js → uvicorn
  │
  ▼
run_graph()                    ← routers/chat.py
  │
  ├─ 1. Enriquecimiento de query  (memory.py)
  ├─ 2. Clasificación de intent   (tools.py: classify_query_intent)
  ├─ 3. Selección de herramienta  (LangGraph: search_node)
  │     ├─ a. hybrid_search
  │     ├─ b. custom_sql_search
  │     └─ c. image_text_search / check_schedule_coverage
  ├─ 4. Búsqueda en CrateDB       (BM25 + KNN vectorial)
  ├─ 5. Reranking                 (VIVOClient / qwen3rerank)
  ├─ 6. Generación de respuesta   (LLM: Ollama o Gemini)
  └─ 7. Streaming al cliente      (WebSocket, tokens SSE)
```

---

## Paso 1 — Recepción y contexto de conversación

**Fichero:** `backend/routers/chat.py` → `run_graph()`

El router WebSocket recibe el mensaje del usuario junto con el `session_id`. Recupera la sesión (historial de turnos) desde `SessionStore` e inicializa un objeto `ConversationMemory` con los últimos `N` turnos (configurable en `CONVERSATION_MEMORY_CONFIG`).

```python
conversation_memory = ConversationMemory(...)
conversation_memory.load(session_turns)
```

La `ConversationMemory` mantiene el historial reciente y es la que sabe si la nueva query es una continuación de un hilo anterior.

---

## Paso 2 — Enriquecimiento de la query (follow-up detection)

**Fichero:** `backend/compat/memory.py` → `get_enhanced_query()`

Si hay historial y `auto_enhance_query` está activo, el sistema hace **una sola llamada al LLM** para:

1. Decidir si la query actual es un *follow-up* de la anterior (p. ej. "¿y la de radio?" tras "¿qué había en TVE el lunes?").
2. Si lo es, expandirla con el contexto que faltaba (canal, fecha, tema) para que sea autocontenida.

| Situación | Query original | Query mejorada (enviada a búsqueda) |
|---|---|---|
| Primera interacción | "¿Qué programas había en 1962?" | — (se usa tal cual) |
| Follow-up | "¿Y en Radio Nacional?" | "¿Qué programas había en Radio Nacional en 1962?" |
| Query independiente | "¿Quién presentaba el noticiero?" | — (se usa tal cual) |

Si no hay historial, este paso se omite.

---

## Paso 3 — Clasificación de intent

**Fichero:** `backend/compat/tools.py` → `classify_query_intent()`

El sistema llama al LLM activo con un prompt clasificador corto para determinar uno de tres tipos:

| Intent | Casos | Acción |
|---|---|---|
| `greeting` | "Hola", "gracias", "adiós"… | Respuesta de saludo fija sin búsqueda |
| `system_info` | "¿Qué puedo preguntarte?"… | El LLM genera ayuda sin buscar en BD |
| `data_search` | Cualquier otra cosa | Continúa el pipeline de búsqueda |

---

## Paso 4 — Intercept especial: cobertura de parrilla

**Fichero:** `backend/compat/graph.py` → `_detect_coverage_query()`

Antes de invocar al agente LLM, se comprueba con regex si la pregunta es sobre **huecos en la parrilla**: "¿faltó algún día?", "¿hubo emisión todos los días en abril de 1962?", etc.

Si se detecta este patrón, se llama directamente a `check_schedule_coverage(year, month, day)` (sin pasar por el agente) y el resultado se inyecta como un documento sintético en `search_results`. El pipeline salta al Paso 7 (generación).

---

## Paso 5 — Agente de búsqueda (LangGraph tool-call)

**Fichero:** `backend/compat/graph.py` → `search_node()`

Se crea un LLM (`ChatOllama` o `ChatGoogleGenerativeAI`) **vinculado a las herramientas** disponibles (`bind_tools`). El agente recibe un system prompt que describe las tablas y columnas de CrateDB, y la query (mejorada o original).

El LLM decide qué herramienta invocar:

| Herramienta | Cuándo la elige el agente |
|---|---|
| `hybrid_search` | Búsqueda general de texto en una o varias tablas |
| `custom_sql_search` | Necesita filtros exactos (canal, día de semana, rango de fechas) |
| `image_text_search` | El usuario pide buscar imágenes por descripción textual |
| `check_schedule_coverage` | Pregunta sobre días sin emisión (segunda vía, si no la intercepción previa) |

El sistema deduplica llamadas repetidas antes de ejecutarlas.

---

## Paso 6 — Ejecución de la búsqueda en CrateDB

### 6a. `hybrid_search` (caso más común)

**Fichero:** `backend/compat/tools.py` → `hybrid_search()`

#### 6a.1 Selección de tablas

Si el agente no especificó tablas, `classify_query_tables()` asigna una puntuación a cada tabla en función de palabras clave presentes en la query. `teleradio_content_editorial` se incluye **siempre** como tabla obligatoria.

Tablas disponibles:

| Tabla | Contenido |
|---|---|
| `teleradio_content_editorial` | Artículos, textos de redacción |
| `teleradio_content_tv_schedule` | Parrilla de televisión |
| `teleradio_content_radio_schedule` | Parrilla de radio |
| `teleradio_content_advertising` | Anuncios publicitarios |
| `teleradio_content_others` | Otros contenidos (cartas, curiosidades…) |
| `teleradio_content_image_embeddings` | Embeddings de imágenes indexadas |

#### 6a.2 Extracción de fecha

`_extract_date_filter()` analiza el texto de la query con regex para detectar expresiones como "15 de enero de 1962", "1962-04", "enero 1962", solo año, etc. Si las encuentra, genera un fragmento SQL `WHERE date = '...'` o `WHERE date >= '...' AND date < '...'`.

El texto de la query se limpia de esas referencias temporales antes de usarse en la búsqueda de texto.

#### 6a.3 Clasificación genérica vs. específica

`_is_generic_date_query()` determina si la query es solo una petición de programación completa de una fecha sin término de búsqueda específico (p. ej. "¿qué había el lunes 5 de enero de 1960?"). En ese caso **no se usa MATCH fulltext** (evita penalizar resultados por no contener palabras extra).

#### 6a.4 Construcción de la query SQL (modo híbrido)

Para búsquedas específicas, la query SQL combina:

```sql
-- BM25 (texto completo) OR KNN (vectorial)
WHERE (MATCH(campo1, 'términos') OR MATCH(campo2, 'términos'))
   OR KNN_MATCH(embedding_field, [0.12, -0.34, ...], 20)
  AND (date >= '1962-01-01' AND date < '1963-01-01')   -- si hay fecha
ORDER BY _score DESC
LIMIT 60
```

Si no hay embedding disponible, degrada a BM25 puro.

#### 6a.5 Generación del embedding

**Fichero:** `backend/compat/tools.py` → `get_query_embedding()`

El texto de búsqueda (sin la parte de fecha) se convierte en un vector numérico usando el modelo de embeddings configurado (`OLLAMA_EMBEDDING_MODEL`, habitualmente `qwen3-embedding:0.6b`). Este vector es el que se pasa a `KNN_MATCH` para la búsqueda semántica.

---

### 6b. `custom_sql_search`

El agente genera directamente la query SQL (usando las instrucciones del system prompt con los nombres exactos de tablas y columnas). El resultado se devuelve directamente sin pasar por la lógica de BM25/KNN.

---

## Paso 7 — Reranking

**Fichero:** `backend/compat/tools.py` → `_rerank_results()`

Tras obtener los candidatos de CrateDB, se aplica el modelo de **reranking** (`qwen3rerank` vía `VIVOClient`) para reordenar los resultados por relevancia semántica respecto a la query original.

El proceso:
1. Para cada resultado, se construye un par `(query, fragmento_texto)`.
2. El modelo asigna un `rerank_score` ∈ [0, 1].
3. Los resultados se reordenan de mayor a menor score y se truncan al límite configurado.

Si `VIVOClient` no está disponible, se usa el `_score` de CrateDB directamente.

---

## Paso 8 — Generación de la respuesta

**Fichero:** `backend/compat/tools.py` → `generate_response_internal()`  
**Fichero:** `backend/compat/graph.py` → `response_node()`

Con los resultados rerankeados, se construye el prompt final para el LLM:

```
[SYSTEM]  Eres TELERÍN 📺, asistente experto en la revista TeleRadio (1957-1965).
          Responde SOLO con la información del contexto proporcionado.
          Si no hay información suficiente, dilo explícitamente.

[CONTEXT] <fragmentos de texto de los resultados, con fuente y fecha>

[USER]    <query original del usuario>
```

El LLM elegido (Ollama o Gemini) genera la respuesta en español citando las fuentes del contexto.

### Modo streaming

Si `DISABLE_STREAMING=0`, la respuesta se va enviando **token a token** al cliente a través del WebSocket. Cada token se empaqueta como un evento `__TOKEN__:<texto>`.

Si `DISABLE_STREAMING=1` (por defecto en la configuración actual), el LLM genera la respuesta completa y se envía de una vez.

---

## Paso 9 — Envío de la respuesta y metadatos

**Fichero:** `backend/routers/chat.py`

Una vez el LLM finaliza, el router envía por WebSocket:

```jsonc
{
  "type": "response",
  "content": "La programación del lunes 5 de enero de 1960...",
  "sources": [
    {
      "magazine_id": "teleradio_1960_02",
      "title": "Programación TVE",
      "date": "1960-01-05",
      "page_number": 12,
      "png_url": "/teleradio/images/teleradio_1960_02/pagina_012.png"
    }
  ],
  "sql_queries": [...],
  "timing": {
    "search_time": 0.42,
    "db_search_time": 0.31,
    "reranking_time": 0.09,
    "response_time": 1.83
  },
  "tokens": {
    "prompt": 1240,
    "response": 312
  }
}
```

El turno (query + respuesta + metadatos) se guarda en `SessionStore` para alimentar la memoria de conversación en la siguiente pregunta.

---

## Resumen de tiempos típicos

| Fase | Tiempo estimado |
|---|---|
| Enriquecimiento de query (LLM) | 0.3 – 1.5 s |
| Clasificación de intent (LLM) | 0.2 – 0.8 s |
| Selección de herramienta (LLM) | 0.3 – 1.0 s |
| Búsqueda en CrateDB (BM25 + KNN) | 0.1 – 0.5 s |
| Reranking (qwen3rerank) | 0.1 – 0.4 s |
| Generación de respuesta (LLM) | 1.0 – 5.0 s |
| **Total** | **2 – 9 s** |

> Los tiempos reales dependen del modelo LLM activo (Ollama local vs. Gemini API), del número de resultados en la BD y de la carga del servidor.

---

## Diagrama de decisiones en `search_node`

```
query recibida
    │
    ├─ ¿hay historial? ──SI──► get_enhanced_query() ──► query mejorada
    │                                                          │
    │         NO ──────────────────────────────────────────────┘
    │
    ▼
classify_query_intent()
    ├─ greeting    ──► respuesta de saludo (sin búsqueda)
    ├─ system_info ──► LLM genera ayuda
    └─ data_search
         │
         ├─ _detect_coverage_query()
         │    └─ SÍ ──► check_schedule_coverage() ──► documentos sintéticos
         │
         └─ NO ──► agente LLM con tools
                    ├─ hybrid_search
                    │    ├─ classify_query_tables()
                    │    ├─ _extract_date_filter()
                    │    ├─ get_query_embedding()
                    │    └─ SQL (BM25 + KNN) → CrateDB
                    ├─ custom_sql_search
                    │    └─ SQL directo → CrateDB
                    └─ image_text_search
                         └─ KNN sobre image_embeddings

                    ▼ resultados de CrateDB
               _rerank_results()   (qwen3rerank)
                    │
                    ▼
              generate_response_internal()   (Ollama / Gemini)
                    │
                    ▼
              WebSocket → cliente
```
