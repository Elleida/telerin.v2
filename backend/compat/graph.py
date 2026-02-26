"""
LangGraph State Machine (telerin.v1)

This graph is a port of the more complete `teamagent-langgraph/graph.py`
but adapted so that `run_graph` keeps the `llm_stream_handler` (streaming)
signature used by `telerin.v0` and the Streamlit app.

The intent of `telerin.v1` is to provide a drop-in upgraded graph
implementation while preserving the external API used by the rest of the
codebase.
"""

from typing import TypedDict, Annotated, List, Dict, Any, Optional
import os
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
try:
    from langchain_google_genai import ChatGoogleGenerativeAI
    LANGCHAIN_GOOGLE_AVAILABLE = True
except ImportError:
    LANGCHAIN_GOOGLE_AVAILABLE = False
import json
import re
import requests
import time

from backend.config import OLLAMA_BASE_URL, OLLAMA_LLM_MODEL, CONVERSATION_MEMORY_CONFIG, GEMINI_MODEL, GEMINI_API_KEY
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
    Devuelve (year, month, day) o None.
    day=0 significa mes completo.
    """
    q = query.lower()
    if not any(kw in q for kw in _COVERAGE_KEYWORDS):
        return None

    # Año 19XX (rango editorial de la revista)
    year_m = re.search(r'\b(19[5-9]\d)\b', query)
    if not year_m:
        return None
    year = int(year_m.group(1))

    # Mes por nombre
    month = None
    for name, num in _MONTHS_ES.items():
        if name in q:
            month = num
            break
    # Mes por número si no se encontró por nombre
    if month is None:
        m = re.search(r'\b(1[0-2]|0?[1-9])\b', query)
        if m:
            month = int(m.group(1))
    if month is None:
        return None

    # Día concreto (opcional): "el 15 de", "día 15"
    day = 0
    day_m = re.search(r'(?:el|día)\s+(\d{1,2})\s+de', q)
    if day_m:
        day = int(day_m.group(1))

    return (year, month, day)


class GraphState(TypedDict):
    messages: List[Any]
    user_query: str
    query_type: str
    search_results: List[Dict]
    final_response: str
    sources: List[Dict]
    prompt_used: str
    error: str
    sql_queries: List[Dict]
    search_time: float
    db_search_time: float
    reranking_time: float
    response_time: float
    search_classification: Optional[str]
    conversation_memory: Optional[ConversationMemory]
    enhanced_query: Optional[str]
    conversation_context: Optional[Dict]
    is_contextual_follow_up: bool
    llm_backend: str
    llm_model: Optional[str]
    stream: bool
    stream_handler: Optional[Any]


def create_search_agent(llm_backend: str = "ollama", llm_model: Optional[str] = None):
    if llm_backend == "gemini" and LANGCHAIN_GOOGLE_AVAILABLE:
        api_key = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")
        model = llm_model or GEMINI_MODEL
        if not api_key:
            raise ValueError("GEMINI_API_KEY no configurado en .env")
        print(f"   🤖 Search agent: Gemini ({model})")
        llm = ChatGoogleGenerativeAI(
            model=model,
            google_api_key=api_key,
            temperature=0.1,
        )
    else:
        if llm_backend == "gemini":
            print("   ⚠️ langchain-google-genai no disponible, usando Ollama como fallback")
        model = llm_model or OLLAMA_LLM_MODEL
        print(f"   🤖 Search agent: Ollama ({model})")
        llm = ChatOllama(
            model=model,
            base_url=OLLAMA_BASE_URL,
            temperature=0.1,
            timeout=120.0,
        )
    return llm.bind_tools(SEARCH_TOOLS)


def search_node(state: GraphState) -> GraphState:
    print("\n" + "🔵" * 40)
    print("🔵 SEARCH NODE - INICIO")
    print("🔵" * 40)

    user_query = state.get("user_query", "")
    messages = state.get("messages", [])
    conversation_memory = state.get("conversation_memory")
    handler = state.get("stream_handler")

    # Propagar el backend LLM activo a todas las llamadas internas ANTES de cualquier uso
    llm_backend = state.get("llm_backend", "ollama")
    llm_model = state.get("llm_model")
    set_active_llm_backend(llm_backend, llm_model)
    print(f"🧠 LLM backend activo: {llm_backend} ({llm_model or 'modelo .env'})")

    print(f"📝 Query del usuario: {user_query}")

    enhanced_query = None
    is_contextual_follow_up = False
    conversation_context = None

    if conversation_memory is not None and CONVERSATION_MEMORY_CONFIG.get("auto_enhance_query", True):
        if not getattr(conversation_memory, 'messages', None):
            print("ℹ️ Sin historial — primera interacción, no se analiza contexto")
        else:
            print(f"\n🧠 Analizando contexto ({len(conversation_memory.messages)} turno(s) en memoria)...")
            try:
                if handler:
                    handler("__CONTEXT_ANALYSIS__")
            except Exception:
                pass
            try:
                # Una sola llamada LLM: detecta follow-up y mejora la query en un paso
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
                is_contextual_follow_up = False
                conversation_context = None
    else:
        if conversation_memory is None:
            print("⚠️ No hay conversation_memory en el estado")
        elif not getattr(conversation_memory, 'messages', None):
            print("ℹ️ Memoria vacía (primera interacción)")
        if not CONVERSATION_MEMORY_CONFIG.get("auto_enhance_query", True):
            print("⚠️ auto_enhance_query está desactivado en config")

    state["enhanced_query"] = enhanced_query
    state["is_contextual_follow_up"] = is_contextual_follow_up
    state["conversation_context"] = conversation_context

    actual_query = enhanced_query if enhanced_query else user_query
    print(f"🔍 Query enviada a búsqueda: {actual_query}")

    intent = classify_query_intent(actual_query)
    print(f"🔍 Intent detected: {intent}")

    if intent == "greeting":
        print("🔵 SEARCH NODE - FIN (Saludo)\n")
        state.update({
            "query_type": "greeting",
            "search_results": [],
            "sql_queries": [],
            "messages": messages,
        })
        try:
            if handler:
                handler("__SEARCH_DONE__")
        except Exception:
            pass
        return state

    if intent == "system_info":
        print("🔵 SEARCH NODE - FIN (System info)\n")
        state.update({
            "query_type": "system_info",
            "search_results": [],
            "sql_queries": [],
            "messages": messages,
        })
        try:
            if handler:
                handler("__SEARCH_DONE__")
        except Exception:
            pass
        return state

    print("📊 Procediendo con búsqueda en base de datos...")
    set_last_search_context(actual_query, [])

    # ── Intercept: preguntas sobre cobertura/huecos de programación ───────────
    _coverage = _detect_coverage_query(actual_query)
    if _coverage:
        _year, _month, _day = _coverage
        print(f"🗓️ Cobertura detectada: year={_year}, month={_month}, day={_day}")
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
                "title":             _label,
                "content":           json.dumps(_data, ensure_ascii=False, indent=2),
                "table_source":      "schedule_coverage",
                "date":              f"{_year}-{_month:02d}",
                "_score":            1.0,
                "relevance_score":   1.0,
                **_data,
            }
            _cov_results = [_synthetic]
        except Exception as _exc:
            print(f"⚠️ Error en check_schedule_coverage: {_exc}")
            _cov_results = []

        set_last_search_context(actual_query, _cov_results)
        try:
            if handler:
                handler("__SEARCH_DONE__")
        except Exception:
            pass
        state.update({
            "query_type": "data_search",
            "search_results": _cov_results,
            "sql_queries": get_last_executed_sql_queries(),
            "search_time": 0.0,
            "db_search_time": 0.0,
            "reranking_time": 0.0,
            "search_classification": "schedule_coverage",
            "messages": messages,
        })
        print(f"🔵 SEARCH NODE - FIN (coverage query, resultados={len(_cov_results)})\n")
        return state
    # ─────────────────────────────────────────────────────────────────────────

    # Crear instrucciones para el agente de búsqueda
    search_instructions = """Eres TELERÍN 📺, un agente de búsqueda especializado en la base de datos histórica de TeleRadio (1957-1965).
Tu única responsabilidad es encontrar información relevante usando las herramientas disponibles.

⚠️ TABLAS AUTORIZADAS Y SUS COLUMNAS (OBLIGATORIO - USA SOLO ESTOS NOMBRES):

🔸 teleradio_content_editorial:
   Columnas disponibles: id, magazine_id, article_id, page_number, section_title, main_title, full_text, brief_summary, media_description, caption_literal

🔸 teleradio_content_tv_schedule:
   Columnas disponibles: id, magazine_id, channel, block_name, page_number, date, time, title, is_color, sponsor, content_description, media_description, caption_literal, day_of_week

🔸 teleradio_content_radio_schedule:
   Columnas disponibles: id, magazine_id, station, station_information, page_number, date, time, title, content_description, linked_article_id, day_of_week

🔸 teleradio_content_advertising:
   Columnas disponibles: id, magazine_id, advertiser, ad_copy, page_number

🔸 teleradio_content_others:
   Columnas disponibles: id, magazine_id, title, description, content, page_number

🔸 teleradio_content_image_embeddings:
    Columnas disponibles: id, magazine_id, page_number, src, description, caption_literal, bbox

INSTRUCCIONES:
1. Usa hybrid_search para búsquedas generales de texto
2. Usa custom_sql_search SOLO si necesitas filtros específicos o joins
2b. Para preguntas sobre PROGRAMACIÓN de un DÍA DE LA SEMANA concreto, SIEMPRE usa custom_sql_search con filtro day_of_week sobre teleradio_content_tv_schedule y/o teleradio_content_radio_schedule.
        Valores válidos para day_of_week: "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"
        Detecta este tipo de preguntas cuando incluyan:
        - nombres de días: "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"
        - expresiones como "los lunes", "cada martes", "programación del jueves", "qué había los viernes"
        OBLIGATORIO: combina siempre day_of_week con un filtro de date si se menciona mes y/o año.
        Ejemplos (ajusta según la pregunta):
        - Programación de TV los lunes (sin restricción de fecha):
            SELECT magazine_id, channel, date, time, title, content_description, day_of_week
            FROM teleradio_content_tv_schedule
            WHERE day_of_week = 'lunes'
            ORDER BY date, time
            LIMIT 50
        - Programación de TV los miércoles de un mes concreto (ej. enero 1960):
            SELECT magazine_id, channel, date, time, title, content_description, day_of_week
            FROM teleradio_content_tv_schedule
            WHERE day_of_week = 'miércoles'
              AND date >= '1960-01-01' AND date < '1960-02-01'
            ORDER BY date, time
            LIMIT 50
        - Programación de radio los sábados de un año (ej. 1962):
            SELECT magazine_id, station, date, time, title, content_description, day_of_week
            FROM teleradio_content_radio_schedule
            WHERE day_of_week = 'sábado'
              AND date >= '1962-01-01' AND date < '1963-01-01'
            ORDER BY date, time
            LIMIT 50
            LIMIT 50
        - Programación de TV y radio los jueves (join para ambos):
            SELECT 'TV' AS tipo, magazine_id, channel AS emisora, date, time, title, day_of_week
            FROM teleradio_content_tv_schedule
            WHERE day_of_week = 'jueves'
            UNION ALL
            SELECT 'Radio' AS tipo, magazine_id, station AS emisora, date, time, title, day_of_week
            FROM teleradio_content_radio_schedule
            WHERE day_of_week = 'jueves'
            ORDER BY date, time
            LIMIT 50
3. Si el usuario pide BUSCAR IMÁGENES por una descripción textual, usa image_text_search
4. Usa check_schedule_coverage cuando el usuario pregunte:
   - Si hubo algún día SIN programación en un mes o año concreto ("¿faltó algún día?", "¿qué días no hubo emisión?", "¿hubo cobertura completa en...?")
   - Qué días tiene programación una fecha exacta ("¿qué hubo el 15 de abril de 1963?")
   Parámetros: year=<año>, month=<mes 1-12>, day=<día o 0 si se consulta el mes completo>
   Ejemplos:
   - "¿hubo algún día sin programación en abril de 1962?"  → check_schedule_coverage(year=1962, month=4, day=0)
   - "¿qué programación había el 18 de abril de 1962?"     → check_schedule_coverage(year=1962, month=4, day=18)
5. SIEMPRE usa los nombres exactos de tablas y columnas listados arriba
6. NO inventes nombres de columnas que no existen
7. Ejecuta la búsqueda y devuelve los resultados en formato JSON

Pregunta del usuario: {query}

Ejecuta la búsqueda apropiada ahora."""

    print("📁 Creando agente de búsqueda...") 
    # Crear agente de búsqueda (usando el mismo backend LLM que el resto del flujo)
    search_agent = create_search_agent(llm_backend=llm_backend, llm_model=llm_model)

    try:
        if handler:
            handler("__PLANNING__")
    except Exception:
        pass

    # Construir mensajes para el agente (usar query mejorada)
    agent_messages = [
        SystemMessage(content=search_instructions.format(query=actual_query)),
        HumanMessage(content=actual_query)
    ]

    # Execute the agent and ToolNode; fallback to hybrid_search if needed
    search_results: List[Dict] = []
    search_time = 0.0
    db_search_time = 0.0
    reranking_time = 0.0
    sql_queries: List[Dict] = []
    search_classification = None

    try:
        response = search_agent.invoke(agent_messages)
        if hasattr(response, 'tool_calls') and response.tool_calls:
            unique_tool_calls = []
            seen_calls = set()
            for tool_call in response.tool_calls:
                tool_name = tool_call.get('name', '')
                args = tool_call.get('args', {})
                if tool_name == 'hybrid_search':
                    query_key = args.get('query', '')
                    tables_key = tuple(sorted(args.get('table_names', []))) if args.get('table_names') else ()
                    call_signature = (tool_name, query_key, tables_key)
                else:
                    call_signature = (tool_name, tuple(sorted(args.items())))
                if call_signature not in seen_calls:
                    seen_calls.add(call_signature)
                    unique_tool_calls.append(tool_call)
            if len(unique_tool_calls) < len(response.tool_calls):
                response.tool_calls = unique_tool_calls

            # Log de las queries que el agente va a ejecutar
            for tc in unique_tool_calls:
                tc_name = tc.get('name', '?')
                tc_args = tc.get('args', {})
                if tc_name == 'hybrid_search':
                    tables = tc_args.get('table_names') or ['auto']
                    print(f"   🗄️  [{tc_name}] query='{tc_args.get('query', '')}' | tablas={tables}")
                elif tc_name == 'get_sql_query_results':
                    sql = tc_args.get('sql_query', tc_args.get('query', ''))
                    print(f"   🗄️  [{tc_name}] SQL={sql!r}")
                else:
                    print(f"   🗄️  [{tc_name}] args={tc_args}")

            tool_node = ToolNode(SEARCH_TOOLS)
            tool_messages = {"messages": agent_messages + [response]}
            try:
                if handler:
                    handler("__DB_SEARCH__")
            except Exception:
                pass
            _t0_tools = time.time()
            tool_results = tool_node.invoke(tool_messages)
            _tool_elapsed = time.time() - _t0_tools

            for msg in tool_results.get('messages', []):
                if hasattr(msg, 'content'):
                    try:
                        result_data = json.loads(msg.content)
                        if result_data.get('success'):
                            # check_schedule_coverage expone 'results' (formato estándar)
                            # Otros tools exponen 'results' directamente
                            tool_results_list = result_data.get('results', [])
                            if tool_results_list:
                                search_results.extend(tool_results_list)
                            if 'search_time' in result_data:
                                search_time += result_data['search_time']
                            if 'db_search_time' in result_data:
                                db_search_time += result_data['db_search_time']
                            if 'reranking_time' in result_data:
                                reranking_time += result_data['reranking_time']
                            if 'search_classification' in result_data:
                                search_classification = result_data['search_classification']
                    except Exception:
                        pass

            # Si ninguna herramienta reportó sus propios tiempos, usar el tiempo
            # total de ejecución del ToolNode como db_search_time de referencia
            if db_search_time == 0.0 and search_time == 0.0:
                db_search_time = round(_tool_elapsed, 3)
                search_time = db_search_time
        else:
            # fallback to hybrid_search (el agente no generó tool_calls)
            try:
                if handler:
                    handler("__DB_SEARCH__")
            except Exception:
                pass
            limit = get_sql_results_limit()

            print("⚠️ El agente no generó tool_calls, usando hybrid_search como fallback...")
            print(f"   🗄️  [hybrid_search] query='{actual_query}'")
            raw = hybrid_search.invoke({"query": actual_query, "limit": limit})
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, dict) and parsed.get('success'):
                    search_results = parsed.get('results', [])
                    search_time = parsed.get('search_time', 0)
                    db_search_time = parsed.get('db_search_time', 0)
                    reranking_time = parsed.get('reranking_time', 0)
                    search_classification = parsed.get('search_classification')
            except Exception:
                pass
    except Exception as e:
        print(f"⚠️ Error invoking search agent: {e}")
        # fallback to hybrid_search (excepción en el agente)
        try:
            if handler:
                handler("__DB_SEARCH__")
        except Exception:
            pass
        try:
            limit = get_sql_results_limit()
            print(f"   🗄️  [hybrid_search fallback] query='{actual_query}'")
            raw = hybrid_search.invoke({"query": actual_query, "limit": limit})
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict) and parsed.get('success'):
                search_results = parsed.get('results', [])
                search_time = parsed.get('search_time', 0)
                db_search_time = parsed.get('db_search_time', 0)
                reranking_time = parsed.get('reranking_time', 0)
                search_classification = parsed.get('search_classification')
        except Exception:
            pass

    set_last_search_context(actual_query, search_results)
    try:
        if handler:
            handler("__SEARCH_DONE__")
    except Exception:
        pass

    sql_queries = get_last_executed_sql_queries()

    print(f"✅ Búsqueda completada. Resultados: {len(search_results)}")
    print(f"🔍 DEBUG SEARCH_NODE: db_search_time={db_search_time:.2f}s, reranking_time={reranking_time:.2f}s, search_time={search_time:.2f}s")

    state.update({
        "query_type": "data_search",
        "search_results": search_results,
        "sql_queries": sql_queries,
        "search_time": search_time,
        "db_search_time": db_search_time,
        "reranking_time": reranking_time,
        "search_classification": search_classification,
        "messages": messages + [response] if 'response' in locals() else messages,
    })

    print("🔵 SEARCH NODE - FIN\n")
    return state


def response_node(state: GraphState) -> GraphState:
    print("\n" + "🟢" * 40)
    print("🟢 RESPONSE NODE - INICIO")
    print("🟢" * 40)

    user_query = state.get("user_query", "")
    search_results = state.get("search_results", [])
    error = state.get("error", "")
    query_type = state.get("query_type", "data_search")

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
            "sources": [],
            "prompt_used": "",
            "error": "",
            "sql_queries": state.get("sql_queries", []),
        }

    if query_type == "system_info":
        try:
            # For system info/help queries, ask the LLM to generate a help response
            # even when there are no documents to cite.
            help_context = """
INSTRUCCIONES: El usuario solicita ayuda sobre cómo usar el asistente. Contesta en español, de forma clara y concisa.

Describe brevemente (2-6 puntos) las capacidades principales del asistente, qué tipos de consultas puede resolver y 3 ejemplos prácticos de preguntas que el usuario puede hacer.

No cites ni inventes documentos ni números de documento; no es necesario proporcionar referencias bibliográficas.

No des información técnica sobre la arquitectura interna (por ejemplo: "cómo funciona el modelo"); céntrate en el uso práctico.

Evita respuestas largas; usa viñetas cuando sea posible y ofrece una última línea con una sugerencia de siguiente paso (por ejemplo: Prueba: "Busca anuncios de Coca-Cola").
"""
            gen = generate_response_internal(
                user_query=user_query,
                search_results=[],
                additional_context=help_context,
                llm_backend=state.get('llm_backend', 'ollama'),
                llm_model=state.get('llm_model'),
                stream=False,
                stream_handler=None,
                allow_llm_without_docs=True,
            )
            response_text = gen.get("response") if isinstance(gen, dict) else str(gen)
            return {
                **state,
                "final_response": response_text,
                "sources": [],
                "prompt_used": gen.get("prompt_used") if isinstance(gen, dict) else "",
                "error": "",
                "sql_queries": state.get("sql_queries", []),
            }
        except Exception as e:
            return {**state, "final_response": f"Error generando info del sistema: {e}", "sources": [], "prompt_used": "", "error": str(e)}

    # data_search
    try:
        _rn_handler = state.get('stream_handler')
        try:
            if _rn_handler:
                _rn_handler("__GENERATING__")
        except Exception:
            pass
        gen = generate_response_internal(
            user_query=user_query,
            search_results=search_results,
            additional_context="",
            llm_backend=state.get('llm_backend', 'ollama'),
            llm_model=state.get('llm_model'),
            stream=state.get('stream', False),
            stream_handler=state.get('stream_handler', None),
        )

        conversation_memory = state.get("conversation_memory")
        if conversation_memory is not None and CONVERSATION_MEMORY_CONFIG.get("entity_extraction", True):
            try:
                _resp_text = gen.get("response") if isinstance(gen, dict) else str(gen)
                _entities = _extract_entities(user_query, _resp_text, search_results)
                if _entities:
                    _entity_summary = ", ".join(f"{k}: {v}" for k, v in _entities.items() if v)
                    print(f"🏷️ Entidades extraídas: {_entity_summary}")
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

        final_resp = gen.get('response') if isinstance(gen, dict) else str(gen)
        sources = gen.get('sources', []) if isinstance(gen, dict) else []
        prompt_used = gen.get('prompt_used', '') if isinstance(gen, dict) else ''
        response_time = gen.get('response_time', 0) if isinstance(gen, dict) else 0
        
        # 🆕 DEBUG: Verificar tiempos de búsqueda
        db_search_time = state.get('db_search_time', 0)
        reranking_time = state.get('reranking_time', 0)
        print(f"🔍 DEBUG RESPUESTA_NODE: db_search_time={db_search_time:.2f}s, reranking_time={reranking_time:.2f}s, response_time={response_time:.2f}s")
        
        return {
            **state,
            "final_response": final_resp,
            "sources": sources,
            "prompt_used": prompt_used,
            "response_time": response_time,
            "error": "" if gen.get('success', True) else gen.get('error', ''),
            "sql_queries": state.get('sql_queries', []),
            "db_search_time": db_search_time,
            "reranking_time": reranking_time,
        }
    except Exception as e:
        return {**state, "final_response": f"Error generando respuesta: {e}", "error": str(e)}


def should_continue(state: GraphState) -> str:
    error = state.get("error", "")
    search_results = state.get("search_results", [])
    if error:
        return "end"
    return "response"


def create_graph() -> StateGraph:
    workflow = StateGraph(GraphState)
    workflow.add_node("search", search_node)
    workflow.add_node("response", response_node)
    workflow.set_entry_point("search")
    workflow.add_conditional_edges(
        "search",
        should_continue,
        {"response": "response", "end": END},
    )
    workflow.add_edge("response", END)
    app = workflow.compile()
    return app


def run_graph(
    user_query: str,
    conversation_memory: Optional[ConversationMemory] = None,
    llm_backend: str = "ollama",
    llm_model: Optional[str] = None,
    stream: bool = False,
    llm_stream_handler=None,
) -> Dict[str, Any]:
    state: GraphState = {
        "messages": [],
        "user_query": user_query,
        "conversation_memory": conversation_memory,
        "llm_backend": llm_backend,
        "llm_model": llm_model,
        "stream": stream,
        "stream_handler": llm_stream_handler,
        "search_results": [],
        "query_type": None,
        "search_time": 0.0,
        "db_search_time": 0.0,
        "reranking_time": 0.0,
        "response_time": 0.0,
        "sql_queries": [],
        "enhanced_query": None,
        "conversation_context": None,
        "is_contextual_follow_up": False,
    }

    app = create_graph()
    try:
        final_state = app.invoke(state)
        return {
            "success": True,
            "response": final_state.get("final_response", ""),
            "sources": final_state.get("sources", []),
            "prompt_used": final_state.get("prompt_used", ""),
            "search_results": final_state.get("search_results", []),
            "sql_queries": final_state.get("sql_queries", []),
            "error": final_state.get("error", ""),
            "query_type": final_state.get("query_type"),
            "search_classification": final_state.get("search_classification"),
            "search_time": final_state.get("search_time", 0),
            "db_search_time": final_state.get("db_search_time", 0),
            "reranking_time": final_state.get("reranking_time", 0),
            "response_time": final_state.get("response_time", 0),
            "enhanced_query": final_state.get("enhanced_query"),
            "is_contextual_follow_up": final_state.get("is_contextual_follow_up", False),
            "conversation_context": final_state.get("conversation_context"),
        }
    except Exception as e:
        return {"success": False, "response": "Lo siento, ocurrió un error procesando tu consulta.", "sources": [], "prompt_used": "", "search_results": [], "error": str(e)}


__all__ = ["run_graph", "create_graph"]
