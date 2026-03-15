"""
LangGraph ReAct Agent — telerin.v2

Orquestador que sigue la filosofía ReAct (Reasoning + Acting):
    Thought → Act (tool calls) → Observe (results) → Thought → …
hasta que el agente decide que tiene suficiente contexto para responder.

Arquitectura del grafo:
    START
      │
      ▼
  preprocess_node   (enhance query, detect intent/coverage shortcut)
      │
      ├─ greeting / system_info / coverage_done ──────────────────────────┐
      │                                                                    │
      └─ data_search                                                       │
           │                                                               │
           ▼                                                               │
       agent_node   ◄────────────────────────────────────────┐            │
           │                                                  │            │
           ├─ tool_calls? ──► tools_node ────────────────────┘            │
           │                   (acumula search_results)                    │
           └─ no tool_calls / límite alcanzado                             │
                │                                                          │
                ▼                                                          │
          response_node ◄─────────────────────────────────────────────────┘
              │
              ▼
             END

La interfaz pública `run_graph` conserva exactamente la misma firma y el mismo
diccionario de retorno que el grafo anterior para no romper nada en el backend.
"""

from typing import TypedDict, List, Dict, Any, Optional
import os
import json
import re
import time

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_ollama import ChatOllama
from langchain_core.messages import (
    HumanMessage, AIMessage, SystemMessage, ToolMessage, BaseMessage
)

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
    LANGCHAIN_GOOGLE_AVAILABLE = True
except ImportError:
    LANGCHAIN_GOOGLE_AVAILABLE = False

from backend.config import (
    OLLAMA_BASE_URL, OLLAMA_LLM_MODEL,
    CONVERSATION_MEMORY_CONFIG,
    GEMINI_MODEL, GEMINI_API_KEY,
)
from backend.compat.tools import (
    SEARCH_TOOLS,
    hybrid_search,
    generate_response_internal,
    set_last_search_context,
    get_last_search_context,
    get_last_executed_sql_queries,
    get_sql_results_limit,
    classify_query_intent,
    check_schedule_coverage,
    set_active_llm_backend,
)
from backend.compat.memory import ConversationMemory
from backend.compat.context_extractor import ConversationContextExtractor

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_ENTITY_EXTRACTOR = ConversationContextExtractor()


def _extract_entities(query: str, response: str, search_results: list) -> dict:
    """Extracción de entidades deshabilitada — el historial completo se pasa al LLM."""
    return {}


_MONTHS_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

_COVERAGE_KEYWORDS = [
    "día sin programación", "días sin programación",
    "sin emisión", "sin programar", "no hubo programación",
    "no hubo emisión", "hubo algún día", "faltó", "faltaron",
    "días que faltaron", "qué días no", "cuántos días",
    "sin cobertura", "día sin programa", "días sin programa",
    "cobertura de programación", "huecos en la parrilla",
    "parrilla completa", "todos los días hubo",
]


def _detect_coverage_query(query: str):
    """
    Detecta si la consulta es sobre días sin cobertura de programación.
    Devuelve (year, month, day) o None. day=0 significa mes completo.
    """
    q = query.lower()
    if not any(kw in q for kw in _COVERAGE_KEYWORDS):
        return None

    year_m = re.search(r'\b(19[5-9]\d)\b', query)
    if not year_m:
        return None
    year = int(year_m.group(1))

    month = None
    for name, num in _MONTHS_ES.items():
        if name in q:
            month = num
            break
    if month is None:
        m = re.search(r'\b(1[0-2]|0?[1-9])\b', query)
        if m:
            month = int(m.group(1))
    if month is None:
        return None

    day = 0
    day_m = re.search(r'(?:el|día)\s+(\d{1,2})\s+de', q)
    if day_m:
        day = int(day_m.group(1))

    return (year, month, day)


# ──────────────────────────────────────────────────────────────────────────────
# Estado del grafo
# ──────────────────────────────────────────────────────────────────────────────

class GraphState(TypedDict):
    messages: List[Any]                    # mensajes del loop ReAct (crece en cada nodo)
    user_query: str
    query_type: str
    search_results: List[Dict]             # resultados acumulados de todas las tool calls
    final_response: str
    sources: List[Dict]
    prompt_used: str
    error: str
    sql_queries: List[Dict]
    search_time: float
    db_search_time: float
    reranking_time: float
    response_time: float
    prompt_tokens: int
    response_tokens: int
    search_classification: Optional[str]
    conversation_memory: Optional[ConversationMemory]
    enhanced_query: Optional[str]
    conversation_context: Optional[Dict]
    is_contextual_follow_up: bool
    llm_backend: str
    llm_model: Optional[str]
    stream: bool
    stream_handler: Optional[Any]


# ──────────────────────────────────────────────────────────────────────────────
# Prompt del agente ReAct
# ──────────────────────────────────────────────────────────────────────────────

REACT_SYSTEM_PROMPT = """\
Eres TELERÍN 📺, un agente inteligente especializado en la base de datos histórica \
de TeleRadio (1958-1965).

Tu razonamiento sigue el ciclo ReAct:
  1. RAZONA sobre qué información necesitas para responder bien la pregunta.
  2. ACTÚA ejecutando la herramienta más adecuada con los parámetros correctos.
  3. OBSERVA el resultado y decide si necesitas más información o ya puedes responder.
  4. REPITE hasta estar satisfecho con la información obtenida.
  5. Cuando tengas suficiente información, NO llames más herramientas y resume los \
hallazgos clave en una frase o párrafo breve (en español). La respuesta elaborada \
la generará otro componente con los resultados acumulados.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ TABLAS AUTORIZADAS Y COLUMNAS (usa SOLO estos nombres exactos)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔸 teleradio_content_editorial
   id, magazine_id, article_id, page_number, section_title, main_title,
   full_text, brief_summary, media_description, caption_literal

🔸 teleradio_content_tv_schedule
   id, magazine_id, channel, block_name, page_number, date, time, title,
   is_color, sponsor, content_description, media_description, caption_literal,
   day_of_week, credits['cast'], credits['crew']

🔸 teleradio_content_radio_schedule
   id, magazine_id, station, station_information, page_number, date, time,
   title, content_description, linked_article_id, day_of_week

🔸 teleradio_content_advertising
   id, magazine_id, advertiser, ad_copy, page_number

🔸 teleradio_content_others
   id, magazine_id, title, description, content, page_number

🔸 teleradio_content_image_embeddings
   id, magazine_id, page_number, src, description, caption_literal, bbox

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HERRAMIENTAS DISPONIBLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• hybrid_search       — búsqueda semántica+BM25 general sobre todas las tablas
• custom_sql_search   — SQL para filtros específicos (fechas, canales, días de la semana, joins)
• image_text_search   — búsqueda de imágenes por descripción textual
• check_schedule_coverage — días sin programación en un mes/año concreto

REGLAS DE USO:
1. Para preguntas generales → hybrid_search primero.
2. Para filtros de fecha, canal, día de la semana o joins → custom_sql_search.
3. Si hybrid_search no da suficientes resultados → reformula y prueba de nuevo,
   o complementa con custom_sql_search en las tablas relevantes.
4. Para preguntas sobre días concretos de la semana SIEMPRE usa custom_sql_search
   con WHERE day_of_week = 'lunes'|'martes'|'miércoles'|'jueves'|'viernes'|'sábado'|'domingo'
5. En UNION/UNION ALL nunca uses _score en SELECT ni ORDER BY.
6. NUNCA inventes columnas. Usa solo las listadas arriba.
7. NUNCA uses términos de búsquedas anteriores ni tu conocimiento propio.
   Deriva siempre la query de la pregunta actual del usuario:
   Pregunta del usuario: {query}
8. Máximo {max_iterations} rondas de herramientas. Si tras ese límite los datos
   son insuficientes, devuelve un resumen de lo encontrado.
"""

# ──────────────────────────────────────────────────────────────────────────────
# Configuración del loop ReAct
# ──────────────────────────────────────────────────────────────────────────────

MAX_REACT_ITERATIONS = 5   # número máximo de rondas tools → agente


# ──────────────────────────────────────────────────────────────────────────────
# Factory del LLM
# ──────────────────────────────────────────────────────────────────────────────

def _create_llm(llm_backend: str, llm_model: Optional[str] = None):
    if llm_backend == "gemini" and LANGCHAIN_GOOGLE_AVAILABLE:
        api_key = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")
        model = llm_model or GEMINI_MODEL
        if not api_key:
            raise ValueError("GEMINI_API_KEY no configurado en .env")
        print(f"   🤖 ReAct agent: Gemini ({model})")
        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=api_key,
            temperature=0.1,
        )
    else:
        if llm_backend == "gemini":
            print("   ⚠️ langchain-google-genai no disponible — usando Ollama como fallback")
        model = llm_model or OLLAMA_LLM_MODEL
        print(f"   🤖 ReAct agent: Ollama ({model})")
        return ChatOllama(
            model=model,
            base_url=OLLAMA_BASE_URL,
            temperature=0.1,
            timeout=120.0,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Nodo 1 — Preprocesamiento
# (enhance query, detectar intent, shortcut para coverage queries)
# ──────────────────────────────────────────────────────────────────────────────

def preprocess_node(state: GraphState) -> GraphState:
    print("\n" + "🟡" * 40)
    print("🟡 PREPROCESS NODE — INICIO")
    print("🟡" * 40)

    user_query = state.get("user_query", "")
    conversation_memory = state.get("conversation_memory")
    handler = state.get("stream_handler")
    llm_backend = state.get("llm_backend", "ollama")
    llm_model = state.get("llm_model")

    set_active_llm_backend(llm_backend, llm_model)
    print(f"🧠 LLM backend: {llm_backend} ({llm_model or 'modelo .env'})")
    print(f"📝 Query: {user_query}")

    # ── 1. Enriquecer query con contexto conversacional ───────────────────────
    enhanced_query = None
    is_contextual_follow_up = False
    conversation_context = None

    if conversation_memory is not None and CONVERSATION_MEMORY_CONFIG.get("auto_enhance_query", True):
        if getattr(conversation_memory, 'messages', None):
            print(f"🧠 Analizando contexto ({len(conversation_memory.messages)} turno(s))...")
            try:
                if handler:
                    handler("__CONTEXT_ANALYSIS__")
            except Exception:
                pass
            try:
                enhanced_query = conversation_memory.get_enhanced_query(user_query)
                if enhanced_query and enhanced_query != user_query:
                    is_contextual_follow_up = True
                    print(f"🔗 Follow-up detectado")
                    print(f"   ├─ Original : {user_query}")
                    print(f"   └─ Mejorada : {enhanced_query}")
                else:
                    enhanced_query = None
                    print(f"🔗 No es follow-up — query independiente: {user_query}")
                conversation_context = conversation_memory.extract_context()
            except Exception:
                enhanced_query = None
        else:
            print("ℹ️ Sin historial — primera interacción")
    else:
        if conversation_memory is None:
            print("⚠️ No hay conversation_memory en el estado")
        elif not getattr(conversation_memory, 'messages', None):
            print("ℹ️ Memoria vacía (primera interacción)")
        if not CONVERSATION_MEMORY_CONFIG.get("auto_enhance_query", True):
            print("⚠️ auto_enhance_query desactivado en config")

    actual_query = enhanced_query if enhanced_query else user_query

    # ── 2. Clasificar intent ───────────────────────────────────────────────────
    intent = classify_query_intent(actual_query)
    print(f"🔍 Intent detectado: {intent}")

    state.update({
        "enhanced_query": enhanced_query,
        "is_contextual_follow_up": is_contextual_follow_up,
        "conversation_context": conversation_context,
        "query_type": intent,
        "search_results": [],
        "sql_queries": [],
        "messages": [],
    })

    # ── 3. Shortcut: cobertura de programación (evita loop ReAct) ─────────────
    if intent == "data_search":
        _coverage = _detect_coverage_query(actual_query)
        if _coverage:
            _year, _month, _day = _coverage
            print(f"🗓️ Cobertura detectada — year={_year}, month={_month}, day={_day}")
            try:
                if handler:
                    handler("__DB_SEARCH__")
            except Exception:
                pass
            try:
                _raw = check_schedule_coverage.invoke({"year": _year, "month": _month, "day": _day})
                _data = json.loads(_raw) if isinstance(_raw, str) else _raw
                _label = (
                    f"Cobertura {_year}-{_month:02d}-{_day:02d}"
                    if _day else
                    f"Cobertura {_year}-{_month:02d}"
                )
                _synthetic = {
                    "title":           _label,
                    "content":         json.dumps(_data, ensure_ascii=False, indent=2),
                    "table_source":    "schedule_coverage",
                    "date":            f"{_year}-{_month:02d}",
                    "_score":          1.0,
                    "relevance_score": 1.0,
                    **_data,
                }
                state["search_results"] = [_synthetic]
                # Marcar como "completado" para saltar el loop ReAct
                state["query_type"] = "coverage_done"
            except Exception as exc:
                print(f"⚠️ Error en check_schedule_coverage: {exc}")

            set_last_search_context(actual_query, state.get("search_results", []))
            try:
                if handler:
                    handler("__SEARCH_DONE__")
            except Exception:
                pass

    print("🟡 PREPROCESS NODE — FIN\n")
    return state


# ──────────────────────────────────────────────────────────────────────────────
# Nodo 2 — Agente ReAct
# (razona sobre la pregunta y decide qué herramientas invocar, o ya cuándo parar)
# ──────────────────────────────────────────────────────────────────────────────

def agent_node(state: GraphState) -> GraphState:
    print("\n" + "🔵" * 40)

    messages: List[Any] = state.get("messages", [])
    llm_backend = state.get("llm_backend", "ollama")
    llm_model = state.get("llm_model")
    handler = state.get("stream_handler")
    user_query = state.get("user_query", "")
    enhanced_query = state.get("enhanced_query")
    actual_query = enhanced_query if enhanced_query else user_query

    # Contar cuántos ToolMessages hay para saber en qué iteración estamos
    tool_msg_count = sum(1 for m in messages if isinstance(m, ToolMessage))
    iteration = tool_msg_count + 1
    print(f"🔵 REACT AGENT NODE — iteración {iteration}/{MAX_REACT_ITERATIONS}")
    print("🔵" * 40)

    llm = _create_llm(llm_backend, llm_model).bind_tools(SEARCH_TOOLS)

    if not messages:
        # Primera entrada: construir el contexto inicial
        system_prompt = REACT_SYSTEM_PROMPT.format(
            query=actual_query,
            max_iterations=MAX_REACT_ITERATIONS,
        )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=actual_query),
        ]
        print(f"📝 Primera invocación del agente ReAct")
        print(f"   Query efectiva: {actual_query}")
        try:
            if handler:
                handler("__PLANNING__")
        except Exception:
            pass
    else:
        print(f"🔄 Re-invocación ({len(messages)} mensajes en historial)")

    response = llm.invoke(messages)
    new_messages = messages + [response]

    if response.tool_calls:
        print(f"   🛠️  El agente solicita {len(response.tool_calls)} herramienta(s):")
        for tc in response.tool_calls:
            tc_args = tc.get('args', {})
            print(f"      → [{tc.get('name')}] {tc_args}")
    else:
        print("   ✅ El agente no solicita más herramientas → preparando respuesta")
        if hasattr(response, 'content') and response.content:
            print(f"   💭 Razonamiento final: {str(response.content)[:200]}…")

    return {**state, "messages": new_messages}


# ──────────────────────────────────────────────────────────────────────────────
# Nodo 3 — Ejecución de herramientas
# (ejecuta las tool calls del agente y acumula los search_results)
# ──────────────────────────────────────────────────────────────────────────────

def tools_node(state: GraphState) -> GraphState:
    print("\n" + "🟠" * 40)
    print("🟠 TOOLS NODE — ejecutando herramientas")
    print("🟠" * 40)

    messages: List[Any] = state.get("messages", [])
    handler = state.get("stream_handler")

    try:
        if handler:
            handler("__DB_SEARCH__")
    except Exception:
        pass

    tool_executor = ToolNode(SEARCH_TOOLS)
    _t0 = time.time()
    tool_output = tool_executor.invoke({"messages": messages})
    _elapsed = time.time() - _t0

    tool_messages: List[Any] = tool_output.get("messages", [])
    new_messages = messages + tool_messages

    # ── Acumular resultados de búsqueda de esta ronda ─────────────────────────
    accumulated: List[Dict] = list(state.get("search_results", []))
    sql_queries: List[Dict] = list(state.get("sql_queries", []))
    search_time: float = state.get("search_time", 0.0)
    db_search_time: float = state.get("db_search_time", 0.0)
    reranking_time: float = state.get("reranking_time", 0.0)
    search_classification: Optional[str] = state.get("search_classification")

    for msg in tool_messages:
        if not hasattr(msg, 'content'):
            continue
        try:
            parsed = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
            if isinstance(parsed, dict) and parsed.get('success'):
                results_list: List[Dict] = parsed.get('results', [])
                if results_list:
                    accumulated.extend(results_list)
                if 'executed_queries' in parsed:
                    sql_queries.extend(parsed['executed_queries'])
                search_time      += parsed.get('search_time', 0.0)
                db_search_time   += parsed.get('db_search_time', 0.0)
                reranking_time   += parsed.get('reranking_time', 0.0)
                if parsed.get('search_classification'):
                    search_classification = parsed['search_classification']
        except Exception:
            pass

    # Si las herramientas no reportaron tiempos, usar el tiempo total del ToolNode
    if db_search_time == 0.0 and search_time == 0.0:
        db_search_time = round(_elapsed, 3)
        search_time = db_search_time

    user_query = state.get("user_query", "")
    enhanced_query = state.get("enhanced_query")
    set_last_search_context(enhanced_query or user_query, accumulated)

    try:
        if handler:
            handler("__SEARCH_DONE__")
    except Exception:
        pass

    print(f"✅ Herramientas ejecutadas en {_elapsed:.2f}s")
    print(f"   Nuevos resultados esta ronda: {len(accumulated) - len(state.get('search_results', []))}")
    print(f"   Total acumulado: {len(accumulated)} resultados\n")

    return {
        **state,
        "messages":             new_messages,
        "search_results":       accumulated,
        "sql_queries":          sql_queries,
        "search_time":          search_time,
        "db_search_time":       db_search_time,
        "reranking_time":       reranking_time,
        "search_classification": search_classification,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Nodo 4 — Generación de respuesta final
# (igual que antes: llama a generate_response_internal con todos los resultados)
# ──────────────────────────────────────────────────────────────────────────────

def response_node(state: GraphState) -> GraphState:
    print("\n" + "🟢" * 40)
    print("🟢 RESPONSE NODE — INICIO")
    print("🟢" * 40)

    user_query    = state.get("user_query", "")
    enhanced_query = state.get("enhanced_query")
    effective_query = enhanced_query if enhanced_query else user_query
    search_results  = state.get("search_results", [])
    query_type      = state.get("query_type", "data_search")
    handler         = state.get("stream_handler")

    # ── Saludo ────────────────────────────────────────────────────────────────
    if query_type == "greeting":
        greeting = "¡Hola! Soy TELERÍN 📻, tu asistente de TeleRadio. ¿En qué puedo ayudarte?"
        conversation_memory = state.get("conversation_memory")
        if conversation_memory is not None and CONVERSATION_MEMORY_CONFIG.get("entity_extraction", True):
            try:
                conversation_memory.add_turn(
                    user_query=user_query,
                    response=greeting,
                    query_type="greeting",
                    enhanced_query=state.get("enhanced_query"),
                    search_results=[],
                    entities_found=_extract_entities(user_query, greeting, []),
                    relevance_score=0.0,
                )
            except Exception:
                pass
        return {
            **state,
            "final_response": greeting,
            "sources":        [],
            "prompt_used":    "",
            "error":          "",
            "sql_queries":    state.get("sql_queries", []),
        }

    # ── Información del sistema / ayuda ──────────────────────────────────────
    if query_type == "system_info":
        try:
            help_context = (
                "INSTRUCCIONES: El usuario solicita ayuda sobre cómo usar el asistente. "
                "Contesta en español, de forma clara y concisa.\n\n"
                "Describe brevemente (2-6 puntos) las capacidades principales del asistente, "
                "qué tipos de consultas puede resolver y 3 ejemplos prácticos de preguntas "
                "que el usuario puede hacer.\n\n"
                "No cites ni inventes documentos ni referencias bibliográficas.\n"
                "No des información técnica sobre la arquitectura interna.\n"
                "Evita respuestas largas; usa viñetas cuando sea posible y ofrece una "
                "última línea con una sugerencia de siguiente paso."
            )
            gen = generate_response_internal(
                user_query=effective_query,
                search_results=[],
                additional_context=help_context,
                llm_backend=state.get('llm_backend', 'ollama'),
                llm_model=state.get('llm_model'),
                stream=False,
                stream_handler=None,
                allow_llm_without_docs=True,
            )
            return {
                **state,
                "final_response": gen.get("response") if isinstance(gen, dict) else str(gen),
                "sources":        [],
                "prompt_used":    gen.get("prompt_used", "") if isinstance(gen, dict) else "",
                "error":          "",
                "sql_queries":    state.get("sql_queries", []),
            }
        except Exception as e:
            return {**state, "final_response": f"Error generando info del sistema: {e}",
                    "sources": [], "prompt_used": "", "error": str(e)}

    # ── data_search / coverage_done ───────────────────────────────────────────
    try:
        try:
            if handler:
                handler("__GENERATING__")
        except Exception:
            pass

        gen = generate_response_internal(
            user_query=effective_query,
            search_results=search_results,
            additional_context="",
            llm_backend=state.get('llm_backend', 'ollama'),
            llm_model=state.get('llm_model'),
            stream=state.get('stream', False),
            stream_handler=state.get('stream_handler'),
        )

        # Guardar turno en memoria conversacional
        conversation_memory = state.get("conversation_memory")
        if conversation_memory is not None and CONVERSATION_MEMORY_CONFIG.get("entity_extraction", True):
            try:
                _resp_text = gen.get("response") if isinstance(gen, dict) else str(gen)
                _entities  = _extract_entities(user_query, _resp_text, search_results)
                if _entities:
                    print(f"🏷️ Entidades: {', '.join(f'{k}: {v}' for k, v in _entities.items() if v)}")
                conversation_memory.add_turn(
                    user_query=user_query,
                    response=_resp_text,
                    query_type="data_search",
                    enhanced_query=state.get("enhanced_query"),
                    search_results=search_results,
                    entities_found=_entities,
                    relevance_score=1.0 if search_results else 0.5,
                )
            except Exception:
                pass

        try:
            gen.setdefault("sql_queries", get_last_executed_sql_queries())
        except Exception:
            pass

        final_resp      = gen.get('response')      if isinstance(gen, dict) else str(gen)
        sources         = gen.get('sources', [])   if isinstance(gen, dict) else []
        prompt_used     = gen.get('prompt_used', '') if isinstance(gen, dict) else ''
        response_time   = gen.get('response_time', 0) if isinstance(gen, dict) else 0
        prompt_tokens   = gen.get('prompt_tokens', 0) if isinstance(gen, dict) else 0
        response_tokens = gen.get('response_tokens', 0) if isinstance(gen, dict) else 0
        db_search_time  = state.get('db_search_time', 0)
        reranking_time  = state.get('reranking_time', 0)

        print(f"🟢 RESPONSE NODE — FIN: {len(final_resp or '')} chars, "
              f"{prompt_tokens}+{response_tokens} tokens, "
              f"db={db_search_time:.2f}s rerank={reranking_time:.2f}s gen={response_time:.2f}s")

        return {
            **state,
            "final_response":  final_resp,
            "sources":         sources,
            "prompt_used":     prompt_used,
            "response_time":   response_time,
            "prompt_tokens":   prompt_tokens,
            "response_tokens": response_tokens,
            "error":           "" if gen.get('success', True) else gen.get('error', ''),
            "sql_queries":     state.get('sql_queries', []),
            "db_search_time":  db_search_time,
            "reranking_time":  reranking_time,
        }

    except Exception as e:
        return {**state, "final_response": f"Error generando respuesta: {e}", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────────
# Funciones de routing
# ──────────────────────────────────────────────────────────────────────────────

def route_after_preprocess(state: GraphState) -> str:
    """Decide si ir al loop ReAct o directamente a respuesta."""
    qt = state.get("query_type", "data_search")
    if qt in ("greeting", "system_info", "coverage_done"):
        return "response"
    return "agent"


def route_after_agent(state: GraphState) -> str:
    """Decide si el agente quiere ejecutar más herramientas o ya terminar."""
    messages: List[Any] = state.get("messages", [])
    if not messages:
        return "response"

    last = messages[-1]

    if not (hasattr(last, 'tool_calls') and last.tool_calls):
        return "response"

    # Comprobar límite de iteraciones
    tool_msg_count = sum(1 for m in messages if isinstance(m, ToolMessage))
    if tool_msg_count >= MAX_REACT_ITERATIONS:
        print(f"⚠️ Límite de {MAX_REACT_ITERATIONS} iteraciones alcanzado — forzando respuesta")
        return "response"

    return "tools"


# ──────────────────────────────────────────────────────────────────────────────
# Construcción del grafo
# ──────────────────────────────────────────────────────────────────────────────

def create_graph() -> StateGraph:
    workflow = StateGraph(GraphState)

    workflow.add_node("preprocess", preprocess_node)
    workflow.add_node("agent",      agent_node)
    workflow.add_node("tools",      tools_node)
    workflow.add_node("response",   response_node)

    workflow.set_entry_point("preprocess")

    workflow.add_conditional_edges(
        "preprocess",
        route_after_preprocess,
        {"agent": "agent", "response": "response"},
    )
    workflow.add_conditional_edges(
        "agent",
        route_after_agent,
        {"tools": "tools", "response": "response"},
    )
    # Después de ejecutar las herramientas → volver al agente para observar y razonar
    workflow.add_edge("tools", "agent")
    workflow.add_edge("response", END)

    return workflow.compile()


# ──────────────────────────────────────────────────────────────────────────────
# Interfaz pública — firma idéntica a la versión anterior
# ──────────────────────────────────────────────────────────────────────────────

def run_graph(
    user_query: str,
    conversation_memory: Optional[ConversationMemory] = None,
    llm_backend: str = "ollama",
    llm_model: Optional[str] = None,
    stream: bool = False,
    llm_stream_handler=None,
) -> Dict[str, Any]:
    state: GraphState = {
        "messages":                [],
        "user_query":              user_query,
        "conversation_memory":     conversation_memory,
        "llm_backend":             llm_backend,
        "llm_model":               llm_model,
        "stream":                  stream,
        "stream_handler":          llm_stream_handler,
        "search_results":          [],
        "query_type":              None,
        "search_time":             0.0,
        "db_search_time":          0.0,
        "reranking_time":          0.0,
        "response_time":           0.0,
        "prompt_tokens":           0,
        "response_tokens":         0,
        "sql_queries":             [],
        "enhanced_query":          None,
        "conversation_context":    None,
        "is_contextual_follow_up": False,
        "final_response":          "",
        "sources":                 [],
        "prompt_used":             "",
        "error":                   "",
        "search_classification":   None,
    }

    app = create_graph()
    try:
        final_state = app.invoke(state, config={"recursion_limit": 20})
        return {
            "success":                  True,
            "response":                 final_state.get("final_response", ""),
            "sources":                  final_state.get("sources", []),
            "prompt_used":              final_state.get("prompt_used", ""),
            "search_results":           final_state.get("search_results", []),
            "sql_queries":              final_state.get("sql_queries", []),
            "error":                    final_state.get("error", ""),
            "query_type":               final_state.get("query_type"),
            "search_classification":    final_state.get("search_classification"),
            "search_time":              final_state.get("search_time", 0),
            "db_search_time":           final_state.get("db_search_time", 0),
            "reranking_time":           final_state.get("reranking_time", 0),
            "response_time":            final_state.get("response_time", 0),
            "prompt_tokens":            final_state.get("prompt_tokens", 0),
            "response_tokens":          final_state.get("response_tokens", 0),
            "enhanced_query":           final_state.get("enhanced_query"),
            "is_contextual_follow_up":  final_state.get("is_contextual_follow_up", False),
            "conversation_context":     final_state.get("conversation_context"),
        }
    except Exception as e:
        return {
            "success":       False,
            "response":      "Lo siento, ocurrió un error procesando tu consulta.",
            "sources":       [],
            "prompt_used":   "",
            "search_results": [],
            "error":         str(e),
        }


__all__ = ["run_graph", "create_graph"]
