# TELERÍN v2 — Arquitectura del Sistema

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
