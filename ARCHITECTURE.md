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

Punto de entrada. Registra los routers, monta el directorio de imágenes estáticas en `/images` (desde `PNG_BASE_DIR`) y ejecuta la siguiente secuencia en el evento `startup`:

```python
@app.on_event("startup")
async def on_startup():
    ensure_users_table()          # tabla telerin_users
    ensure_sessions_table()       # tabla telerin_sessions
    ensure_query_log_table()      # tabla telerin_query_log
    ensure_feedback_table()       # tabla telerin_feedback
    start_cleanup_scheduler()     # scheduler de limpieza de sesiones inactivas
```

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

#### Estado por hilo (`threading.local`)

Todo el estado mutable que varía por query (límites, umbrales, historial de chat, backend LLM activo, resultados de búsqueda, tokens, queries SQL ejecutadas) se almacena en un objeto `threading.local()` en lugar de variables globales de módulo:

```python
_tl = threading.local()
```

Esto garantiza que dos usuarios simultáneos nunca interfieran entre sí:

| Variable (antes global) | Atributo en `_tl` | Valor por defecto |
|---|---|---|
| `SQL_RESULTS_LIMIT` | `_tl.sql_results_limit` | `60` |
| `LLM_SCORE_THRESHOLD` | `_tl.llm_score_threshold` | `0.5` |
| `_CHAT_HISTORY` | `_tl.chat_history` | `[]` |
| `_CURRENT_LLM_BACKEND/MODEL` | `_tl.llm_backend`, `_tl.llm_model` | `"ollama"`, `None` |
| `LAST_GENERATED_PROMPT` | `_tl.last_generated_prompt` | `None` |
| `LAST_TOKEN_COUNTS` | `_tl.token_counts` | `{prompt: 0, response: 0}` |
| `LAST_EXECUTED_SQL_QUERIES` | `_tl.executed_sql_queries` | `[]` |
| `LAST_USER_QUERY/RESULTS` | `_tl.last_user_query`, `_tl.last_search_results` | `None`, `[]` |

La única variable que permanece global es `_GENERIC_SEARCH_CACHE` (caché de clasificación de intención), cuyo contenido no depende del usuario.

---

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

#### `_add_png_links_to_response(response_text, sources)`

Post-procesa la respuesta generada por el LLM sustituyendo las referencias textuales a documentos (`Documento N`, `Documento 1, 2, 3`, `Documentos N, M`) por enlaces Markdown que apuntan a la imagen PNG del número de revista correspondiente. El número del documento se conserva siempre junto al enlace. Las referencias sin URL asociada se mantienen como texto plano.

---

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

Soporta serialización completa con `to_dict()` / `from_dict()` para persistencia en CrateDB.

**Gestión de sesiones (`backend/services/session_store.py`) — Caché L1 + L2:**

Cada usuario autenticado tiene su propio objeto `ConversationMemory`, indexado por `user_id` (UUID):

```
get_session(user_id)
  ├─ L1 (RAM, por proceso) ─► hit → devuelve inmediato, actualiza last_access
  └─ L1 miss
       ├─ L2 (CrateDB telerin_sessions) ─► restaura ConversationMemory.from_dict()
       └─ no existe → nueva instancia vacía

save_session(user_id, memory)           ← fire-and-forget tras cada turno
  └─ upsert en telerin_sessions (INSERT → falla → UPDATE)

clear_session(user_id)                  ← botón "Limpiar" del usuario
  └─ pop de L1 + DELETE de CrateDB
```

**Tablas CrateDB del sistema:**

| Tabla | Contenido |
|---|---|
| `telerin_users` | Usuarios y credenciales |
| `telerin_sessions` | Memoria conversacional serializada (JSON) |
| `telerin_feedback` | Ratings 👍/👎 con timings y tokens |
| `telerin_query_log` | Queries de usuario y respuestas del agente |

**Limpieza automática de sesiones (scheduler daemon):**

```
start_cleanup_scheduler()   ← startup de main.py
  └─ hilo daemon "session-cleanup", cada SESSION_CLEANUP_INTERVAL segundos:
       ├─ evict_stale_l1(SESSION_MAX_IDLE_HOURS)    → libera RAM
       └─ evict_stale_cratedb(SESSION_MAX_IDLE_DAYS) → compacta CrateDB
```

`clear_session()` elimina la entrada del diccionario (`pop`) en lugar de vaciar el objeto en sitio. Esto evita la siguiente race condition:

```
# Race condition evitada:
Hilo A (query anterior): todavía ejecutando → add_turn() al objeto antiguo ← huérfano, descartado
Hilo B (nueva query)   : get_session() → crea ConversationMemory nuevo y limpio ✅
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

El router `feedback.py` persiste cada valoración en dos destinos:
1. **CrateDB** `telerin_feedback` (primario, multi-instancia).
2. **`feedback.log`** (backup JSON Lines, legado).

El router `query_logger.py` persiste cada par query/respuesta en:
1. **CrateDB** `telerin_query_log` (fire-and-forget en hilo daemon).
2. **`queries_responses.log`** (backup rotativo con compresión gzip).

```json
{
  "ts": "2026-02-26T12:00:00+00:00",
  "user": "alice",
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

---

### 10. Estadísticas de uso (`/api/stats`)

Solo accesible para administradores. Lee de **CrateDB** `telerin_feedback` (fallback a `feedback.log`) y calcula:

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

## Concurrencia y escalado multiusuario

```
Usuario A (uuid-A)                     Usuario B (uuid-B)
       │                                       │
  WebSocket coroutine A              WebSocket coroutine B
       │                                       │
  session_store["uuid-A"]           session_store["uuid-B"]
  ConversationMemory_A  ←──────────────────────────────────┐
  (L1 RAM proceso)                                         │
  ← save → CrateDB telerin_sessions                       │
  (L2 compartida entre workers)  ──────────────────────────┘
          │                                    │
  Hilo _bg(mem=Memory_A)            Hilo _bg(mem=Memory_B)
  threading.local()_A               threading.local()_B
  (sql_limit, threshold,            (sql_limit, threshold,
   chat_history, tokens, …)          chat_history, tokens, …)
```

Garantías:
- **Memoria conversacional**: objeto `ConversationMemory` distinto por `user_id`
- **Persistencia cross-restart**: sesiones serializadas en CrateDB — sobreviven reinicios del proceso
- **Multi-worker**: cualquier worker carga la sesión de CrateDB si no está en su L1 local
- **Parámetros de query**: `threading.local()` en `tools.py` — cada hilo tiene su copia privada
- **Captura de memoria en hilo**: `def _bg(mem=memory)` — argumento por defecto fija el objeto en el momento de creación, independientemente de los `clear` posteriores
- **Operaciones atómicas sobre el store**: protegidas con `threading.Lock`
- **Limpieza automática**: scheduler daemon expulsa sesiones inactivas de RAM (L1) y CrateDB (L2)

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
| `SESSION_MAX_IDLE_HOURS` | `24` | Horas sin acceso para liberar sesión de RAM (L1) |
| `SESSION_MAX_IDLE_DAYS` | `30` | Días de inactividad para borrar sesión de CrateDB (L2) |
| `SESSION_CLEANUP_INTERVAL` | `3600` | Segundos entre ciclos del scheduler de limpieza |
