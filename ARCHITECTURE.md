# TELERÍN v2 — Arquitectura del Sistema

## Visión general

```
┌───────────────────────────────────────────────────────────┐
│                        Usuario                              │
└────────────────────────────┬────────────────────────────┘
                             │ HTTP / WebSocket
          (nginx opcional)   │
┌────────────────────────────▼─────────────────────────────┐
│     Frontend  Next.js 14 (puerto 8502, basePath=/teleradio)   │
│  • server.js: proxy HTTP + WS → backend :8000               │
│  • ChatPanel  • ContextPanel  • DebugPanel  • Sidebar        │
│  • ImageSearchTab  • Admin panel                             │
└────────────────────────────┬────────────────────────────┘
     REST /teleradio/api/*  │  WS /teleradio/ws/chat
     (Next.js rewrite)      │  (server.js proxy)
┌────────────────────────────▼─────────────────────────────┐
│          Backend  FastAPI (puerto 8000, solo local)           │
│                                                              │
│  /api/ws/chat ─►  chat.router  ─►  LangGraph Graph           │
│  /api/auth    ─►  auth.router                               │
│  /api/session ─►  session.router                            │
│  /api/image   ─►  image.router                              │
│  /api/stats   ─►  stats.router  (solo admin)                │
│  /api/feedback─►  feedback.router                           │
│  /api/models  ─►  models.router (lista Ollama)              │
│  /images/*    ─►  StaticFiles (PNG de revistas)             │
└────┬────────────────┬────────────────┬────────────────┘
       │                │                │
   CrateDB          Ollama /          VIVOClient
  (BM25 + vec)      Gemini           (reranker)
```

---

## Enrutamiento HTTP/WebSocket

Todo el tráfico externo entra por el servidor Next.js en el puerto 8502, que actúa como único punto de entrada:

| Ruta (externa) | Mecanismo | Destino interno |
|---|---|---|
| `GET /teleradio/*` (páginas) | Next.js SSR/SPA | `frontend/src/app/` |
| `GET/POST /teleradio/api/*` | Next.js rewrite | `localhost:8000/api/*` |
| `GET /teleradio/images/*` | Next.js rewrite | `localhost:8000/images/*` |
| `WS /teleradio/ws/chat` | `server.js` `prependListener` + `http-proxy` | `ws://localhost:8000/ws/chat` |
| `WS /_stcore/*` | `server.js` `prependListener` | Rechazado (400) — Streamlit legado |

**Configuración clave `next.config.js`:**
```js
basePath: '/teleradio',
experimental: { proxyTimeout: 120_000 },  // para llamadas LLM largas
rewrites: [
  { source: '/api/:path*',    destination: 'http://localhost:8000/api/:path*' },
  { source: '/images/:path*', destination: 'http://localhost:8000/images/:path*' },
]
```

**Imágenes PNG:** `PNG_BASE_URL=/teleradio/images` en el backend genera rutas relativas. El frontend las normaliza (via `normalizePngUrl()`) antes de renderizar, convirtiendo cualquier URL absoluta antigua a relativa.

---

## Componentes del backend

### 1. FastAPI (`backend/main.py`)

Punto de entrada. Registra los routers, monta el directorio de imágenes estáticas en `/images` (desde `PNG_BASE_DIR`) y lanza la inicialización de la tabla de usuarios al arranque.

**Routers:**

| Router | Prefijo | Descripción |
|---|---|---|
| `chat.py` | `/ws/chat` | WebSocket streaming |
| `auth.py` | `/api/auth` | JWT login + CRUD usuarios |
| `session.py` | `/api/session` | Gestión de sesiones de memoria |
| `image.py` | `/api/image` | Búsqueda semántica de imágenes |
| `feedback.py` | `/api/feedback` | Log de valoraciones |
| `stats.py` | `/api/stats` | Estadísticas (solo admin) |
| `models.py` | `/api/models` | Lista modelos Ollama disponibles |

---

### 2. Grafo LangGraph (`backend/compat/graph.py`)

Orquesta el flujo completo como un grafo de estados. Cada petición de chat recorre los nodos en orden:

```
START
  │
  ▼
search_node
  │
  │  1. set_active_llm_backend(backend, model)
  │  2. Analizar contexto conversacional (si hay historial):
  │     └─ LLM: ¿es follow-up? + query mejorada (1 llamada)
  │  3. classify_query_intent → greeting / system_info / data_search
  │  4. [data_search] hybrid_search(query, tables)  ── acumula timing con +=
  │  5. [data_search] rerank_results (VIVOClient)   ── acumula timing con +=
  │  6. create_search_agent(llm_backend, llm_model)
  │     └─ LangGraph ReAct agent con tools de búsqueda
  │
  ▼
response_node
  │
  │  generate_response_internal(results, llm_backend, stream)
  │  → captura prompt_tokens + response_tokens → guarda en GraphState
  │  conversation_memory.add_turn(query, response, ...)
  │
  ▼
END → WebSocket stream al cliente (con tokens en mensaje 'final')
```

**Estado del grafo** (`GraphState`):

```python
{
  "user_query": str,
  "enhanced_query": Optional[str],
  "query_type": str,              # greeting | system_info | data_search
  "search_results": List[Dict],
  "final_response": str,
  "llm_backend": str,             # ollama | gemini
  "llm_model": Optional[str],
  "conversation_memory": ConversationMemory,
  "stream": bool,
  "stream_handler": Callable,
  "prompt_tokens": int,           # tokens de entrada al LLM
  "response_tokens": int,         # tokens de salida del LLM
  # timing fields (acumulados con += en llamadas múltiples):
  "db_search_time": float,
  "reranking_time": float,
  "response_time": float,
}
```

---

### 3. Herramientas de búsqueda (`backend/compat/tools.py`)

#### `hybrid_search(query, table, limit)`

Combina dos tipos de búsqueda sobre CrateDB:

```
Query
  ├─► BM25 full-text search  (peso: 0.3)
  └─► Vector similarity search (peso: 0.7, embeddings qwen3-embedding)
         │
         ▼
    Fusión de resultados (deduplicación por doc_id)
         │
         ▼
    _rerank_results() → VIVOClient (si disponible)
         │
         ▼
    Ordenación final:
      - Con reranking: por rerank_score
      - Sin reranking: por relevance_score (BM25)
```

> Nota: el filtrado por umbral se aplica **antes** del `[:limit]`, garantizando que el límite no descarte resultados relevantes prematuramente.

**Tablas CrateDB disponibles:**

| Tabla | Contenido |
|---|---|
| `teleradio_content_tv_schedule` | Programación de televisión |
| `teleradio_content_radio_schedule` | Programación de radio |
| `teleradio_content_advertising` | Publicidad y anuncios |
| `teleradio_content_editorial` | Artículos y reportajes |
| `teleradio_content_image_embeddings` | Embeddings de imágenes |
| `teleradio_content_others` | Contenido misceláneo |

#### Conteo de tokens

`tools.py` captura `prompt_tokens` y `response_tokens` en cada llamada LLM (Ollama y Gemini, streaming y no-streaming) a través de `_set_token_counts()`. El `response_node` los extrae y los propaga al `GraphState`, desde donde llegan al mensaje WebSocket `final` y al `feedback.log`.

#### `call_llm_prompt(prompt, backend, model)`

Abstracción unificada para llamar al LLM activo:

```python
if backend == "gemini":
    genai.Client(api_key=GEMINI_API_KEY).models.generate_content(model, prompt)
else:  # ollama
    requests.post(f"{OLLAMA_BASE_URL}/api/generate", json={model, prompt})
```

---

### 4. Agente de búsqueda ReAct

Creado dinámicamente en `create_search_agent(llm_backend, llm_model)`:

```python
# Ollama
llm = ChatOllama(model=llm_model, base_url=OLLAMA_BASE_URL)

# Gemini
llm = ChatGoogleGenerativeAI(model=llm_model, google_api_key=GEMINI_API_KEY)

agent = create_react_agent(llm, tools=[
    hybrid_search,
    get_sql_query_results,
    ...
])
```

El agente decide qué herramientas usar y en qué orden para responder la query, pudiendo hacer múltiples búsquedas en tablas distintas.

---

### 5. Memoria conversacional (`backend/compat/memory.py`)

```
ConversationMemory
  ├── messages: List[ConversationTurn]   # hasta max_history=100 turnos
  ├── context_window: 5                 # turnos activos para análisis
  └── entities: {years, channels, programs, topics, ...}
```

---

### 6. Análisis de contexto y mejora de query

```
Primera interacción:
  → Sin historial → 0 llamadas LLM → query original a búsqueda

Interacciones siguientes:
  → analyze_and_enhance_query(query, últimos 5 turnos)
       │
       └─► Prompt al LLM activo:
             "¿Es follow-up? Si sí, reescribe la query con el contexto"
             │
             ▼
           { is_follow_up: bool, enhanced_query: str, confidence: float }
             │
    confidence > 0.5 y is_follow_up?
       ├─ SÍ → usar enhanced_query para búsqueda
       └─ NO → usar query original
```

**Tipos de follow-up detectados:**

| Tipo | Ejemplo |
|---|---|
| `expanding` | "¿Y en televisión?" |
| `narrowing` | "Solo los de 1962" |
| `clarifying` | "¿Quién lo presentaba?" |
| `switching` | Cambio completo de tema → no es follow-up |
| `none` | Pregunta independiente → no es follow-up |

---

### 7. Selección dinámica de backend LLM

```
Frontend (Sidebar) selecciona backend
         │
         ▼
WebSocket: { llm_backend: "gemini", llm_model: "gemini-3-flash-preview" }
         │
         ▼
search_node: set_active_llm_backend("gemini", "gemini-3-flash-preview")
         │
         ├─► classify_query_intent()        → call_llm_prompt(gemini)
         ├─► analyze_and_enhance_query()    → call_llm_prompt(gemini)
         ├─► create_search_agent("gemini")  → ChatGoogleGenerativeAI
         └─► generate_response_internal()   → Gemini streaming
```

El selector de modelos Ollama filtra automáticamente los modelos disponibles mostrando solo los que empiezan por `qwen`, `glm` o `gemma3`.

---

### 8. Reranking (VIVOClient)

```python
AI_SERVER_URL = "http://host:5001"   # servicio externo

_rerank_results(results, query):
  ├─ Envía (query, [doc_texts]) a VIVOClient
  ├─ Recibe rerank_score por documento
  ├─ Guarda rerank_score (NO sobreescribe relevance_score BM25)
  └─ Ordena por rerank_score DESC
```

Si VIVOClient no está disponible, los resultados se ordenan por `relevance_score` (BM25). Los tiempos de reranking se acumulan con `+=` en el `GraphState` para soportar múltiples llamadas al agente.

---

### 9. Feedback y log

El router `feedback.py` guarda una línea JSON por valoración en `feedback.log`:

```json
{
  "ts": "2026-02-26T12:00:00+00:00",
  "user": "alice",
  "session": "abc123",
  "rating": "down",
  "query": "¿Quién presentaba Caras Nuevas?",
  "response": "...",
  "comment": "Confunde el año de emisión",
  "num_sources": 3,
  "llm_model": "ollama/glm-4.7-flash",
  "prompt_tokens": 1240,
  "response_tokens": 187,
  "timings": { "db_search_s": 0.8, "reranking_s": 0.3, "response_s": 4.1, "total_s": 5.2 }
}
```

El campo `comment` solo aparece en valoraciones negativas (👎) donde el usuario ha escrito algo en el cajetín.

---

### 10. Estadísticas de uso (`/api/stats`)

Solo accesible para administradores. Lee `feedback.log` y calcula:

| Métrica | Descripción |
|---|---|
| `total`, `up`, `down` | Totales de valoraciones |
| `avg_db_search_s` | Tiempo medio de búsqueda BM25+vector |
| `avg_reranking_s` | Tiempo medio de reranking |
| `avg_response_s` | Tiempo medio de generación LLM |
| `avg_total_s` | Tiempo total medio |
| `avg_num_sources` | Número medio de fuentes |
| `avg_prompt_tokens` | Tokens de entrada medios |
| `avg_response_tokens` | Tokens de salida medios |
| `by_day` | Actividad de los últimos 30 días |
| `by_user` | Actividad por usuario |
| `recent` | Últimas 20 consultas (incluye `comment` si hay) |

---

## Frontend (`frontend/src/`)

```
src/
├── app/
│   ├── page.tsx          # Layout principal (3 paneles)
│   ├── login/page.tsx    # Página de login
│   └── admin/page.tsx    # Panel de administración (stats + usuarios)
├── components/
│   ├── ChatPanel.tsx     # Historial de chat + input + feedback con comentario
│   ├── Sidebar.tsx       # Selector backend/modelo, parámetros, logout
│   ├── ContextPanel.tsx  # Contexto conversacional activo
│   ├── DebugPanel.tsx    # Queries SQL, docs recuperados, scores, imágenes
│   ├── ImageSearchTab.tsx
│   └── ImageLightbox.tsx
├── hooks/
│   └── useChatWs.ts      # Hook WS: conecta al mismo origen, normaliza png_url
└── lib/
    ├── api.ts            # Llamadas REST (BASE = NEXT_PUBLIC_BASE_PATH)
    ├── auth.ts           # JWT storage + decodificación
    └── types.ts          # Tipos compartidos
```

**Flujo de mensajes:**
```
Usuario escribe → useChatWs.sendMessage()
  → WebSocket ws://<host>/teleradio/ws/chat
  → server.js prependListener lo intercepta → http-proxy → ws://localhost:8000/ws/chat
  → Backend procesa y emite chunks de streaming
  → useChatWs acumula chunks + normaliza png_url → ChatPanel renderiza respuesta progresiva
  → Al final: DebugPanel y ContextPanel se actualizan con sources e info de timing/tokens
```

**Normalización de URLs de imagen:**
```
Backend devuelve: "/teleradio/images/ejemplar_5/..."
  → normalizePngUrl() pasa sin cambios (ya es relativa)

Backend legacy devuelve: "http://signal4:8000/images/..."
  → normalizePngUrl() extrae /images/... y prepend /teleradio
  → "/teleradio/images/..."

Browser: GET /teleradio/images/... → Next.js rewrite → GET http://localhost:8000/images/...
```

---

## Flujo completo de una petición

```
1. Usuario: "¿Quién presentaba Caras Nuevas?"
2. Browser → ws://dihana.unizar.es/teleradio/ws/chat
3. nginx → signal4:8502/teleradio/ws/chat
4. server.js prependListener → http-proxy → ws://localhost:8000/ws/chat
5. chat.router → session_store → ConversationMemory
6. search_node:
   a. set_active_llm_backend("ollama", "glm-4.7-flash")
   b. Hay turnos en memoria → analyze_and_enhance_query()
      LLM: "is_follow_up: true → '¿Quién presentaba el programa Caras Nuevas?'"
   c. classify_query_intent → "data_search"
   d. hybrid_search(...) [db_search_time acumulado con +=]
   e. _rerank_results() → VIVOClient [reranking_time acumulado con +=]
   f. create_search_agent("ollama") → ReAct agent refina si hace falta
7. response_node:
   a. generate_response_internal(results, "ollama") → streaming
   b. captura prompt_tokens + response_tokens en GraphState
   c. conversation_memory.add_turn(query, response, ...)
8. WebSocket envía chunks → mensaje final con sources, tiempos y tokens
9. Frontend: ChatPanel renderiza, DebugPanel muestra fuentes con imágenes
10. Usuario da 👎 → aparece cajetín → escribe comentario → Enviar
11. /api/feedback guarda entrada en feedback.log con comment
12. Admin panel → /api/stats → muestra comentario en rojo bajo la consulta
```

---

## Variables de entorno relevantes para ajuste fino

| Variable | Defecto | Efecto |
|---|---|---|
| `BM25_WEIGHT` | `0.3` | Peso de BM25 en fusión |
| `VECTOR_WEIGHT` | `0.7` | Peso de embeddings en fusión |
| `TOP_K` | `10` | Documentos a recuperar por búsqueda |
| `RERANK_TOP_K` | `5` | Top documentos tras reranking |
| `MEMORY_CONTEXT_WINDOW` | `5` | Turnos enviados al LLM para análisis de follow-up |
| `MEMORY_MAX_HISTORY` | `100` | Máximo de turnos almacenados por sesión |
| `MEMORY_AUTO_ENHANCE` | `true` | Activar/desactivar mejora de query con contexto |
| `DISABLE_STREAMING` | `0` | Forzar respuesta no-streaming |


## Visión general

```
┌─────────────────────────────────────────────────────────────┐
│                        Usuario                              │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTP / WebSocket
┌────────────────────────────▼────────────────────────────────┐
│               Frontend  (Next.js 14, puerto 3000)           │
│  • ChatPanel  • ContextPanel  • DebugPanel  • Sidebar       │
└────────────────────────────┬────────────────────────────────┘
                             │ REST + WebSocket
┌────────────────────────────▼────────────────────────────────┐
│               Backend  (FastAPI, puerto 8000)                │
│                                                             │
│   /chat/ws  ──►  chat.router  ──►  LangGraph Graph          │
│   /auth     ──►  auth.router                                │
│   /session  ──►  session.router                             │
│   /images   ──►  image.router                               │
└──────┬────────────────┬────────────────┬────────────────────┘
       │                │                │
   CrateDB          Ollama /          VIVOClient
  (BM25 + vec)      Gemini           (reranker)
```

---

## Componentes del backend

### 1. FastAPI (`backend/main.py`)

Punto de entrada. Registra los routers, monta el directorio de imágenes estáticas (`/images`) y lanza la inicialización de la tabla de usuarios al arranque.

**Routers:**

| Router | Ruta | Descripción |
|---|---|---|
| `chat.py` | `/chat/ws`, `/chat/query` | WebSocket streaming + REST |
| `auth.py` | `/auth/login`, `/auth/me` | JWT login |
| `session.py` | `/session/*` | Gestión de sesiones de memoria |
| `image.py` | `/images/search` | Búsqueda semántica de imágenes |

---

### 2. Grafo LangGraph (`backend/compat/graph.py`)

Orquesta el flujo completo como un grafo de estados. Cada petición de chat recorre los nodos en orden:

```
START
  │
  ▼
search_node ──────────────────────────────────────────────────────┐
  │                                                               │
  │  1. set_active_llm_backend(backend, model)                   │
  │  2. Analizar contexto conversacional (si hay historial):      │
  │     └─ LLM: ¿es follow-up? + query mejorada (1 llamada)      │
  │  3. classify_query_intent → greeting / system_info / data_search│
  │  4. [data_search] hybrid_search(query, tables)               │
  │  5. [data_search] rerank_results (VIVOClient)                │
  │  6. create_search_agent(llm_backend, llm_model)              │
  │     └─ LangGraph ReAct agent con tools de búsqueda           │
  │                                                               │
  ▼                                                               │
response_node ◄────────────────────────────────────────────────── ┘
  │
  │  generate_response_internal(results, llm_backend, stream)
  │  conversation_memory.add_turn(query, response, ...)
  │
  ▼
END → WebSocket stream al cliente
```

**Estado del grafo** (`GraphState`):

```python
{
  "user_query": str,
  "enhanced_query": Optional[str],
  "query_type": str,              # greeting | system_info | data_search
  "search_results": List[Dict],
  "final_response": str,
  "llm_backend": str,             # ollama | gemini
  "llm_model": Optional[str],
  "conversation_memory": ConversationMemory,
  "stream": bool,
  "stream_handler": Callable,
  ...
}
```

---

### 3. Herramientas de búsqueda (`backend/compat/tools.py`)

#### `hybrid_search(query, table, limit)`

Combina dos tipos de búsqueda sobre CrateDB:

```
Query
  ├─► BM25 full-text search  (peso: 0.3)
  └─► Vector similarity search (peso: 0.7, embeddings qwen3-embedding)
         │
         ▼
    Fusión de resultados (deduplicación por doc_id)
         │
         ▼
    _rerank_results() → VIVOClient (si disponible)
         │
         ▼
    Ordenación final:
      - Con reranking: por rerank_score
      - Sin reranking: por relevance_score (BM25)
```

**Tablas CrateDB disponibles:**

| Tabla | Contenido |
|---|---|
| `teleradio_content_tv_schedule` | Programación de televisión |
| `teleradio_content_radio_schedule` | Programación de radio |
| `teleradio_content_advertising` | Publicidad y anuncios |
| `teleradio_content_editorial` | Artículos y reportajes |
| `teleradio_content_image_embeddings` | Embeddings de imágenes |
| `teleradio_content_others` | Contenido misceláneo |

#### `call_llm_prompt(prompt, backend, model)`

Abstracción unificada para llamar al LLM activo:

```python
if backend == "gemini":
    genai.Client(api_key=GEMINI_API_KEY).models.generate_content(model, prompt)
else:  # ollama
    requests.post(f"{OLLAMA_BASE_URL}/api/generate", json={model, prompt})
```

#### `set_active_llm_backend(backend, model)` / `get_active_llm_backend()`

Variables globales por proceso que propagan el backend seleccionado a todos los módulos internos.

---

### 4. Agente de búsqueda ReAct

Creado dinámicamente en `create_search_agent(llm_backend, llm_model)`:

```python
# Ollama
llm = ChatOllama(model=llm_model, base_url=OLLAMA_BASE_URL)

# Gemini
llm = ChatGoogleGenerativeAI(model=llm_model, google_api_key=GEMINI_API_KEY)

agent = create_react_agent(llm, tools=[
    hybrid_search,
    get_sql_query_results,
    ...
])
```

El agente decide qué herramientas usar y en qué orden para responder la query, pudiendo hacer múltiples búsquedas en tablas distintas.

---

### 5. Memoria conversacional (`backend/compat/memory.py`)

```
ConversationMemory
  ├── messages: List[ConversationTurn]   # hasta max_history=100 turnos
  ├── context_window: 5                 # turnos activos para análisis
  └── entities: {years, channels, programs, topics, ...}
```

**`ConversationTurn`** almacena por turno:
- `user_query` — pregunta original
- `response` — respuesta completa del sistema
- `enhanced_query` — query mejorada (si fue follow-up)
- `search_results` — documentos recuperados
- `query_type` — tipo de consulta
- `entities_found` — entidades extraídas

---

### 6. Análisis de contexto y mejora de query

#### Flujo (una sola llamada LLM)

```
Primera interacción:
  → Sin historial → 0 llamadas LLM → query original a búsqueda

Interacciones siguientes:
  → analyze_and_enhance_query(query, últimos 5 turnos)
       │
       └─► Prompt al LLM activo:
             "¿Es follow-up? Si sí, reescribe la query con el contexto"
             Historial: [Usuario: ..., Asistente: ..., Usuario: ...]
             │
             ▼
           { is_follow_up: bool, enhanced_query: str, confidence: float }
             │
    confidence > 0.5 y is_follow_up?
       ├─ SÍ → usar enhanced_query para búsqueda
       └─ NO → usar query original
```

**Tipos de follow-up detectados:**

| Tipo | Ejemplo |
|---|---|
| `expanding` | "¿Y en televisión?" |
| `narrowing` | "Solo los de 1962" |
| `clarifying` | "¿Quién lo presentaba?" |
| `switching` | Cambio completo de tema → no es follow-up |
| `none` | Pregunta independiente → no es follow-up |

**Módulos involucrados:**
- `memory.py` → `get_enhanced_query()` → `QueryEnhancer.enhance_query()`
- `query_enhancer.py` → `LLMContextAnalyzer.analyze_and_enhance_query()`
- `llm_context_analyzer.py` → `call_llm_prompt()` (backend activo)

---

### 7. Selección dinámica de backend LLM

```
Frontend (Sidebar) selecciona backend
         │
         ▼
WebSocket: { llm_backend: "gemini", llm_model: "gemini-3-flash-preview" }
         │
         ▼
search_node: set_active_llm_backend("gemini", "gemini-3-flash-preview")
         │
         ├─► classify_query_intent()        → call_llm_prompt(gemini)
         ├─► analyze_and_enhance_query()    → call_llm_prompt(gemini)
         ├─► create_search_agent("gemini")  → ChatGoogleGenerativeAI
         ├─► _is_generic_date_query()       → call_llm_prompt(gemini)
         └─► generate_response_internal()   → Gemini streaming
```

Todos los módulos llaman a `get_active_llm_backend()` para obtener el backend actual sin recibir parámetros explícitos.

---

### 8. Reranking (VIVOClient)

```python
AI_SERVER_URL = "http://host:5001"   # servicio externo

_rerank_results(results, query):
  ├─ Envía (query, [doc_texts]) a VIVOClient
  ├─ Recibe rerank_score por documento
  ├─ Guarda rerank_score (NO sobreescribe relevance_score BM25)
  └─ Ordena por rerank_score DESC
```

Si VIVOClient no está disponible, los resultados se ordenan por `relevance_score` (BM25).

---

## Frontend (`frontend/src/`)

```
src/
├── app/
│   ├── page.tsx          # Layout principal (3 paneles)
│   └── login/page.tsx    # Página de login
├── components/
│   ├── ChatPanel.tsx     # Historial de chat + input
│   ├── Sidebar.tsx       # Selector backend LLM, modelo, parámetros
│   ├── ContextPanel.tsx  # Contexto conversacional activo
│   ├── DebugPanel.tsx    # Queries SQL, docs recuperados, scores
│   ├── ImageSearchTab.tsx
│   └── ImageLightbox.tsx
├── hooks/
│   └── useChatWs.ts      # Hook WebSocket con streaming
└── lib/
    ├── api.ts            # Llamadas REST
    ├── auth.ts           # JWT storage
    └── types.ts          # Tipos compartidos
```

**Flujo de mensajes:**
```
Usuario escribe → useChatWs.sendMessage()
  → WebSocket envía { query, llm_backend, llm_model, session_id }
  → Backend procesa y emite chunks de streaming
  → useChatWs acumula chunks → ChatPanel renderiza respuesta progresiva
  → Al final: ContextPanel y DebugPanel se actualizan
```

---

## Flujo completo de una petición

```
1. Usuario: "¿quién presentaba Caras Nuevas?"
2. Frontend → WebSocket { query, llm_backend: "gemini", session_id }
3. chat.router → session_store → ConversationMemory
4. search_node:
   a. set_active_llm_backend("gemini", "gemini-3-flash-preview")
   b. Hay 2 turnos en memoria → analyze_and_enhance_query()
      LLM: "is_follow_up: true → '¿Quién presentaba el programa Caras Nuevas?'"
   c. classify_query_intent → "data_search"
   d. hybrid_search("¿Quién presentaba el programa Caras Nuevas?", ...)
   e. _rerank_results() → VIVOClient ordena por relevancia semántica
   f. create_search_agent("gemini") → ReAct agent refina si hace falta
5. response_node:
   a. generate_response_internal(results, "gemini") → streaming
   b. conversation_memory.add_turn(query, response, ...)
6. Frontend recibe chunks → renderiza respuesta
7. ContextPanel muestra: "Follow-up detectado | 3 turnos en memoria"
```

---

## Variables de entorno relevantes para ajuste fino

| Variable | Defecto | Efecto |
|---|---|---|
| `BM25_WEIGHT` | `0.3` | Peso de BM25 en fusión |
| `VECTOR_WEIGHT` | `0.7` | Peso de embeddings en fusión |
| `TOP_K` | `10` | Documentos a recuperar por búsqueda |
| `RERANK_TOP_K` | `5` | Top documentos tras reranking |
| `MEMORY_CONTEXT_WINDOW` | `5` | Turnos enviados al LLM para análisis de follow-up |
| `MEMORY_MAX_HISTORY` | `100` | Máximo de turnos almacenados por sesión |
| `MEMORY_AUTO_ENHANCE` | `true` | Activar/desactivar mejora de query con contexto |
| `DISABLE_STREAMING` | `0` | Forzar respuesta no-streaming |
