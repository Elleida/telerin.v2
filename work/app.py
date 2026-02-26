"""
Interfaz Streamlit para TeleRadio Multi-Agent Team - Versión LangGraph

Interfaz visual que utiliza LangGraph para coordinar agentes:
- Buscar información en la base de datos de TeleRadio
- Generar respuestas usando LLM
- Mostrar fuentes y prompts utilizados
"""

import streamlit as st
import json
import time
from graph import run_graph
from tools import (
    generate_response_internal,
    set_sql_results_limit,
    set_llm_score_threshold,
    get_sql_results_limit,
    get_llm_score_threshold,
    clear_generic_search_cache,
    _rerank_results,
)
from image_search import (
    get_image_embedding,
    get_image_description,
    search_similar_images,
    render_image_results,
)
import traceback
import contextlib
import requests
import base64
from io import BytesIO
from PIL import Image
import os
import re
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
import gzip
import shutil

# NUEVO: Importar sistema de memoria
from memory import ConversationMemory
from config import (
    CONVERSATION_MEMORY_CONFIG,
    OLLAMA_LLM_MODEL,
    OLLAMA_EMBEDDING_MODEL,
    RERANKER_MODEL,
    IMAGE_EMBEDDING_MODEL,
    GEMINI_MODEL,
)

import threading
import queue

try:
    from streamlit.runtime.scriptrunner import get_script_run_ctx, add_script_run_ctx
except Exception:
    get_script_run_ctx = None
    add_script_run_ctx = None

try:
    from vivoembclient import VIVOClient, encode_image_to_base64
    VIVOEMBCLIENT_AVAILABLE = True
except ImportError:
    VIVOEMBCLIENT_AVAILABLE = False
    print("Warning: vivoembclient not available. Image search will not work.")


# Cargar variables de entorno
load_dotenv()

# Configurar logger para queries y respuestas con rotación
LOG_FILE = os.getenv("QUERY_LOG_FILE", "queries_responses.log")
# Tamaño máximo por fichero en bytes (por defecto 5MiB)
MAX_BYTES = int(os.getenv("QUERY_LOG_MAX_BYTES", "5242880"))
# Número de archivos de backup a mantener
BACKUP_COUNT = int(os.getenv("QUERY_LOG_BACKUP_COUNT", "5"))

logger = logging.getLogger("telerin_logger")

# Reduce verbosity from Streamlit scriptrunner warnings in background threads
try:
    logging.getLogger("streamlit.runtime.scriptrunner").setLevel(logging.ERROR)
except Exception:
    pass


class CompressingRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that compresses the rotated log file to .gz."""
    def doRollover(self):
        try:
            # Close the current stream first (RotatingFileHandler may do this internally)
            if self.stream:
                self.stream.close()
                self.stream = None
        except Exception:
            pass

        # Perform normal rollover (this will create baseFilename.1, .2, ...)
        super().doRollover()

        # Compress the most recent rotated file (baseFilename.1)
        try:
            rotated = f"{self.baseFilename}.1"
            if os.path.exists(rotated):
                gz_name = rotated + ".gz"
                with open(rotated, 'rb') as f_in:
                    with gzip.open(gz_name, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                os.remove(rotated)
        except Exception:
            # Never raise from logging cleanup
            pass


if not logger.handlers:
    logger.setLevel(logging.INFO)
    fh = CompressingRotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    fh.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(fh)


def _log_user_query(query: str, session_id: str | None = None):
    try:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "user",
            "session_id": session_id,
            "query": query,
        }
        logger.info(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


def _log_assistant_response(query: str, response: str | None, session_id: str | None = None, extra: dict | None = None):
    try:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "assistant",
            "session_id": session_id,
            "query": query,
            "response": response,
        }
        if extra:
            payload.update(extra)
        logger.info(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass

# Initialize VIVO Client for image embeddings (cached)
@st.cache_resource(show_spinner=False)
def get_vivo_client() -> VIVOClient | None:
    if not VIVOEMBCLIENT_AVAILABLE:
        return None
    try:
        ai_server_url = os.getenv("AI_SERVER_URL", "http://localhost:5001")
        client = VIVOClient(base_url=ai_server_url)
        print(f"VIVOClient initialized with URL: {ai_server_url}")
        return client
    except Exception as e:
        print(f"Warning: Could not initialize VIVOClient: {e}")
        return None

VIVO_CLIENT = get_vivo_client()
if VIVO_CLIENT is None:
    VIVOEMBCLIENT_AVAILABLE = False

# Configuración de página
st.set_page_config(
    page_title="TeleRadio - Multi-Agent Search",
    page_icon="📺",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilos CSS
st.markdown("""
<style>
    .main-title {
        font-size: 4.5rem;
        font-weight: bold;
        margin-bottom: 1rem;
        background: linear-gradient(to right, #FF6B35, #F7931E);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    
    .subtitle {
        font-size: 1.2rem;
        color: #666;
        margin-bottom: 2rem;
    }
    
    .response-box {
        background-color: #f0f2f6;
        padding: 1.5rem;
        border-radius: 0.5rem;
        border-left: 4px solid #FF6B35;
        margin: 1rem 0;
    }
    
    .source-badge {
        display: inline-block;
        background-color: #e8eef7;
        color: #0d47a1;
        padding: 0.3rem 0.8rem;
        border-radius: 0.3rem;
        font-size: 0.9rem;
        margin-right: 0.5rem;
        margin-bottom: 0.5rem;
    }
    
    .error-box {
        background-color: #ffebee;
        padding: 1rem;
        border-radius: 0.5rem;
        border-left: 4px solid #f44336;
        color: #c62828;
    }
</style>
""", unsafe_allow_html=True)

# Inicializar estado de sesión
def _get_session_id() -> str | None:
    if get_script_run_ctx is None:
        return None
    ctx = get_script_run_ctx()
    if not ctx:
        return None
    return getattr(ctx, "session_id", None)

current_session_id = _get_session_id()
if "session_id" not in st.session_state:
    st.session_state.session_id = current_session_id
elif current_session_id and st.session_state.session_id != current_session_id:
    # Si el session_id cambió, reiniciar estado para evitar compartir contexto
    st.session_state.session_id = current_session_id
    st.session_state.messages = []
    st.session_state.initial_greeting_shown = False
    st.session_state.conversation_memory = ConversationMemory(
        max_history=CONVERSATION_MEMORY_CONFIG.get("max_history", 100),
        context_window=CONVERSATION_MEMORY_CONFIG.get("context_window", 5)
    )
    st.session_state.last_response = None
    st.session_state.image_search_results = None
    st.session_state.image_description = None
    st.session_state.image_description_enabled = False
    st.session_state.last_response_source = None

if "messages" not in st.session_state:
    st.session_state.messages = []

if "initial_greeting_shown" not in st.session_state:
    st.session_state.initial_greeting_shown = False

# NUEVO: Inicializar memoria conversacional
if "conversation_memory" not in st.session_state:
    st.session_state.conversation_memory = ConversationMemory(
        max_history=CONVERSATION_MEMORY_CONFIG.get("max_history", 100),
        context_window=CONVERSATION_MEMORY_CONFIG.get("context_window", 5)
    )

if "last_response" not in st.session_state:
    st.session_state.last_response = None

if "image_search_results" not in st.session_state:
    st.session_state.image_search_results = None

if "image_description" not in st.session_state:
    st.session_state.image_description = None

if "image_description_enabled" not in st.session_state:
    st.session_state.image_description_enabled = False

if "last_response_source" not in st.session_state:
    st.session_state.last_response_source = None

if "image_search_sql" not in st.session_state:
    st.session_state.image_search_sql = None

if "max_image_results" not in st.session_state:
    st.session_state.max_image_results = 60


# Funciones de búsqueda de imágenes movidas a `image_search.py`
# Usar: from image_search import get_image_embedding, get_image_description, search_similar_images, render_image_results

# Encabezado
st.markdown('<p class="main-title">📺 TELERÍN - Sistema Multi-Agente sobre TeleRadio</p>', unsafe_allow_html=True)
st.markdown('<p class="subtitle">Base de datos de revistas de televisión y radio de RTVE (1958-1965)</p>', unsafe_allow_html=True)

# Información del sistema
with st.sidebar:
    st.markdown("## ⚙️ Sistema Multi-Agente LangGraph")
    # st.info("""
    # **Arquitectura:**
    # - 🔍 Search Node: Busca información
    # - 📝 Response Node: Genera respuestas
    # - 🎯 LangGraph: Orquesta el flujo
    
    # **Base de datos:**
    # - content_editorial
    # - content_tv_schedule
    # - content_radio_schedule
    # - content_advertising
    # - content_others
    # - content_image_embeddings
    # """)
    

        
    st.markdown("---")
    st.markdown("## 🗑️ Limpiar Conversación")
    
    if st.button("🔄 Borrar Todo", key="clear_button", width='stretch'):
        st.session_state.messages = []
        st.session_state.initial_greeting_shown = False
        st.session_state.last_response = None
        st.session_state.image_search_results = None
        st.session_state.image_description = None
        st.session_state.image_description_enabled = False
        # Limpiar campos de búsqueda de imagen (texto y fichero subido)
        try:
            st.session_state["image_text_query"] = ""
        except Exception:
            pass
        try:
            # Remove the key instead of assigning None to avoid Streamlit widget policy error
            st.session_state.pop("uploaded_image_file", None)
        except Exception:
            pass
        st.session_state.image_description_error = False
        st.session_state.last_response_source = None
        st.session_state.image_search_sql = None
        # NUEVO: Limpiar memoria conversacional
        st.session_state.conversation_memory.clear()
        st.success("✅ Conversación y memoria limpiadas")
        st.rerun()
        
    st.markdown("---")
    st.markdown("## 🔧 Ajustes de búsqueda")

    default_sql_limit = st.session_state.get("sql_limit", get_sql_results_limit())
    sql_limit = st.slider(
        "Límite SQL por consulta",
        min_value=5,
        max_value=200,
        value=default_sql_limit,
        step=5,
        help="Número máximo de filas que se recuperan en cada consulta SQL"
    )

    default_llm_threshold = st.session_state.get("llm_score_threshold", get_llm_score_threshold())
    llm_score_threshold = st.slider(
        "Umbral de score para enviar al LLM",
        min_value=0.0,
        max_value=1.0,
        value=float(default_llm_threshold),
        step=0.01,
        help="Enviar solo documentos cuyo score de reranking sea mayor o igual al umbral"
    )

    st.session_state.sql_limit = sql_limit
    st.session_state.llm_score_threshold = llm_score_threshold
    set_sql_results_limit(sql_limit)
    set_llm_score_threshold(llm_score_threshold)


    st.markdown("---")
    st.markdown("## 🧠 LLM Backend")
    # Inicializar clave de sesión para el radio si no existe (preservar selección)
    if "llm_backend_radio" not in st.session_state:
        # Establecer valor inicial según lo que pueda estar en session_state.llm_backend
        current = st.session_state.get("llm_backend", "ollama")
        st.session_state.llm_backend_radio = "Ollama" if current == "ollama" else "Gemini"

    llm_backend = st.radio(
        "Seleccionar backend para generación de respuestas",
        options=["Ollama", "Gemini"],
        key="llm_backend_radio",
        help="Elige 'Ollama' para usar el modelo configurado en .env o 'Gemini' para enviar la petición a la API de Gemini"
    )

    # Normalizar valor interno y guardar en session_state.llm_backend
    backend_key = "ollama" if llm_backend.startswith("Ollama") else "gemini"

    # Si el backend cambió respecto al almacenado, actualizar el modelo por defecto
    prev_backend = st.session_state.get("llm_backend")
    if prev_backend != backend_key:
        default_for_backend = GEMINI_MODEL if backend_key == "gemini" else OLLAMA_LLM_MODEL
        # Actualizar la key del widget directamente; no se pasa `value=` al widget
        # para evitar el warning "widget created with default value AND session state set".
        st.session_state.llm_model_input = default_for_backend
        st.session_state.llm_model_name = default_for_backend

    # Inicializar la key del widget si todavía no existe (primera carga)
    if "llm_model_input" not in st.session_state:
        st.session_state.llm_model_input = GEMINI_MODEL if backend_key == "gemini" else OLLAMA_LLM_MODEL

    # Guardar backend actual
    st.session_state.llm_backend = backend_key

    # El widget usa la session state como única fuente de verdad (sin `value=`).
    model_input = st.text_input(
        "Nombre de modelo (opcional)",
        key="llm_model_input",
        help="Si no se especifica, se usará el modelo indicado en .env (OLLAMA_LLM_MODEL) o el modelo por defecto de Gemini configurado en variables de entorno."
    )
    st.session_state.llm_model_name = model_input.strip() if model_input else None

    # Modo de respuesta: Bloque completo o Streaming
    if "streaming_mode" not in st.session_state:
        st.session_state.streaming_mode = False

    stream_choice = st.radio(
        "Modo de respuesta",
        options=["Bloque (completa)", "Streaming"],
        index=1 if st.session_state.streaming_mode else 0,
        key="llm_stream_mode",
        help="Elige 'Streaming' para ver la respuesta incrementándose en tiempo real (si el backend lo soporta)."
    )
    st.session_state.streaming_mode = True if stream_choice.startswith("Streaming") else False

    st.markdown("---")
    # Imagen: moved to the 'Búsqueda de Imágenes' tab
    
    # NUEVO: Panel de Contexto Conversacional
    if CONVERSATION_MEMORY_CONFIG.get("show_context_panel", True):
        st.markdown("## 🧠 Contexto Conversacional")
        
        conversation_memory = st.session_state.conversation_memory
        
        if len(conversation_memory) > 0:
            # Mostrar resumen del contexto
            context = conversation_memory.extract_context()
            context_summary = context.get("context_summary", {})
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.metric("💬 Turnos", len(conversation_memory))
                
                time_period = context_summary.get("time_period", "No especificado")
                st.caption(f"📅 **Periodo:** {time_period}")
                
                content_types = context_summary.get("content_types", [])
                if content_types:
                    st.caption(f"📺 **Medios:** {', '.join(content_types[:2])}")
            
            with col2:
                focus = context_summary.get("conversation_focus", "General")
                st.caption(f"🎯 **Foco:**")
                st.caption(f"{focus}")
                
                topics = context_summary.get("main_topics", [])
                if topics:
                    st.caption(f"🏷️ **Temas:** {', '.join(topics[:2])}")
            
            # Expandable con más detalles
            with st.expander("Ver detalles del contexto"):
                last_turn = conversation_memory.get_last_turn()
                recent_searches = context.get("recent_searches", [])
                global_entities = context.get("global_entities", {})

                st.markdown("**Ultimo turno**")
                if last_turn:
                    st.markdown(f"- Usuario: {last_turn.get('user_query', '')}")
                    st.markdown(f"- Query mejorada: {last_turn.get('enhanced_query') or 'N/A'}")
                    st.markdown(f"- Tipo: {last_turn.get('query_type', 'unknown')}")
                else:
                    st.markdown("- N/A")

                st.markdown("**Respuesta anterior (porción utilizada en contexto)**")
                if last_turn and last_turn.get('response'):
                    response_text = last_turn.get('response', '')
                    # Mostrar como texto en caja expandible
                    st.info(response_text)
                else:
                    st.markdown("- N/A")

                st.markdown("**Resumen del contexto**")
                st.json(context_summary)

                st.markdown("**Entidades globales**")
                st.json(global_entities)

                st.markdown("**Busquedas recientes**")
                if recent_searches:
                    for item in recent_searches:
                        st.markdown(f"- {item.get('query', '')}")
                else:
                    st.markdown("- N/A")
        else:
            st.info("💭 No hay conversación activa aún")



# # Mostrar resultados de búsqueda de imágenes si existen
# def render_image_results(results, title: str):
#     if not results:
#         return

#     st.markdown(f"### 🖼️ {title}")

#     # Mostrar métricas
#     col1, col2, col3 = st.columns(3)
#     with col1:
#         st.metric("Imágenes encontradas", len(results))
#     with col2:
#         avg_similarity = sum(r.get('similarity', 0) for r in results) / len(results) if results else 0
#         st.metric("Similitud promedio", f"{avg_similarity:.2%}")
#     with col3:
#         max_similarity = max((r.get('similarity', 0) for r in results), default=0)
#         st.metric("Máxima similitud", f"{max_similarity:.2%}")

#     st.markdown("---")

#     # Mostrar resultados en grid de 4 columnas
#     for row_idx in range(0, len(results), 4):
#         cols = st.columns(4)
        
#         for col_idx in range(4):
#             result_idx = row_idx + col_idx
#             if result_idx < len(results):
#                 result = results[result_idx]
#                 with cols[col_idx]:
#                     with st.expander(f"#{result_idx+1} ({result.get('similarity', 0):.2%})"):
#                         if result.get('png_url'):
#                             try:
#                                 pngpath = result['png_url']
#                                 image = Image.open(requests.get(pngpath, stream=True).raw)
#                                 st.image(image, width=250)
#                             except:
#                                 st.warning("⚠️ Error al cargar")
                        
#                         st.markdown(f"""
#                         **{result.get('magazine_id', 'N/A')}**  
#                         📖 Pág: {result.get('page_number', 'N/A')}  
#                         [🔗 PNG]({result.get('png_url', '#')})
#                         """)

#                         if result.get('description'):
#                             st.caption(f"📝 {result.get('description')}")
#                         if result.get('caption_literal'):
#                             st.caption(f"🗒️ {result.get('caption_literal')}")

#     st.markdown("---")

# Mostrar resultados de búsqueda por descripción textual de imágenes
# Solo aparece cuando se usó image_text_search explícitamente (table_source == "image_embeddings"
# con guión bajo, que es la firma exacta de ese tool; hybrid_search usa "image embeddings" con espacio).
if (
    st.session_state.last_response
    and st.session_state.last_response.get("search_results")
    and st.session_state.last_response_source != "image_search"
):
    search_results = st.session_state.last_response.get("search_results", [])

    text_image_results = [
        r for r in search_results
        if r.get("table_source") == "image_embeddings"
    ]
    if text_image_results:
        with st.expander("🔍 Resultados de búsqueda relacionados con imágenes (basados en descripción textual)"):   
            render_image_results(text_image_results, "Resultados de Búsqueda por Descripción")

tabs = st.tabs(["Conversación", "Búsqueda de Imágenes"])

with tabs[0]:
    # Área principal de chat
    st.markdown("### 💬 Conversación")

    def _has_chat_messages() -> bool:
        """Return True if there are any non-empty chat messages stored in session state."""
        msgs = st.session_state.get("messages", [])
        if not msgs:
            return False
        for message in msgs:
            # message may be a dict or an object with 'content'
            if isinstance(message, dict):
                content = message.get("content")
            else:
                content = getattr(message, "content", None)

            if content and str(content).strip():
                return True
        return False

#   vamos a definir 2 columnas, una para el chat y otra para mostrar información adicional como fuentes, SQL utilizado, etc.
    chat_col, info_col = st.columns([2, 1])

    with chat_col:
    # Mostrar mensaje de presentación inicial si no hay mensajes previos
        if not st.session_state.initial_greeting_shown and not _has_chat_messages():
            greeting_text = (
                "🎉 ¡Hola! Soy **TELERÍN** 📻, tu asistente inteligente de búsqueda en el archivo histórico de **TeleRadio** (1958-1965).\n\n"
                "¿Cómo puedo ayudarte? Puedo:\n"
                "- 📺 Buscar programas de televisión o radio\n"
                "- 📅 Encontrar contenido por fecha, canal o año\n"
                "- 📝 Buscar artículos por tema\n"
                "- 🖼️ Buscar imágenes similares\n"
                "- 📊 Proporcionarte información sobre la historia de la TV española\n\n"
                "Cuéntame, ¿qué te gustaría buscar?"
            )

            # Guardar saludo en el historial para que se muestre en reruns (al cambiar backend, etc.)
            # NOTA: no renderizamos el saludo aquí directamente para evitar mostrarlo dos veces
            st.session_state.messages.append({"role": "assistant", "content": greeting_text})
            st.session_state.initial_greeting_shown = True

        # Mostrar mensajes previos
        for message in st.session_state.messages:
            if message["role"] == "user":
                with st.chat_message("user", avatar="👤"):
                    st.write(message["content"])
            else:
                with st.chat_message("assistant", avatar="🤖"):
                    st.write(message["content"])


        # Input del usuario
        user_query = st.chat_input("P.ej: ¿quién o quienés presentaban el programa \"Caras Nuevas\" y dime qué tipo de programa era? ¿cómo lo clasificarias?")

    if user_query:
        # Detectar comandos de control (reiniciar conversación, borrar memoria)
        query_lower = user_query.lower().strip()
        reset_keywords = ["reinicia", "reinicio", "reset", "limpiar", "limpiar la conversación", "limpiar la memoria", "borrar", "borrar todo", "borra todo", "borra la memoria", "reinicia la conversación", "reinicia todo"]
        
        # Búsqueda exacta: la query debe ser exactamente uno de los keywords o contener solo de uno al inicio/final
        is_reset_command = query_lower in reset_keywords or any(
            re.match(rf"^{re.escape(keyword)}(\s*[\.\!\?])?$", query_lower) 
            for keyword in reset_keywords
        )
        
        with chat_col:
            if is_reset_command:
                # Mostrar comando del usuario
                with st.chat_message("user", avatar="👤"):
                    st.write(user_query)
                
                # Limpiar memoria conversacional y historial
                st.session_state.messages = []
                st.session_state.initial_greeting_shown = False
                st.session_state.last_response = None
                st.session_state.image_search_results = None
                st.session_state.image_description = None
                st.session_state.image_description_enabled = False
                st.session_state.last_response_source = None
                st.session_state.image_search_sql = None
                st.session_state.conversation_memory.clear()
                
                # Limpiar caché de clasificación de búsquedas
                clear_generic_search_cache()
                
                # Mostrar confirmación
                with st.chat_message("assistant", avatar="🤖"):
                    st.success("✅ Conversación reiniciada. La memoria y el historial han sido borrados. Puedes empezar una nueva conversación.")
                
                st.rerun()
            
        # Agregar mensaje del usuario (si no fue comando)
        st.session_state.messages.append({"role": "user", "content": user_query})
        try:
            _log_user_query(user_query, st.session_state.session_id)
        except Exception:
            pass

        # Mostrar el mensaje del usuario inmediatamente
        with st.chat_message("user", avatar="👤"):
            st.write(user_query)
            enhanced_placeholder = st.empty()
        
        # Procesar con LangGraph PRIMERO
        # Si estamos en modo streaming, no mostrar el spinner global para que
        # los placeholders de estado/progreso sean visibles; usamos nullcontext.
        spinner_ctx = st.spinner("🔄 Buscando información y generando respuesta con LangGraph...")
        try:
            if st.session_state.get("streaming_mode", False):
                spinner_ctx = contextlib.nullcontext()
        except Exception:
            spinner_ctx = st.spinner("🔄 Buscando información y generando respuesta con LangGraph...")

        with spinner_ctx:
            try:
                # Capturar tiempo de inicio
                start_time = time.time()
                
                # Si hay una descripción de imagen en el contexto, agregarla a la consulta
                query_with_context = user_query
                if st.session_state.image_description and st.session_state.image_description_enabled:
                    query_with_context = f"Contexto de imagen analizada: {st.session_state.image_description}\n\nConsulta del usuario: {user_query}"
                    # Limpiar la descripción después de usarla
                    st.session_state.image_description = None
                    st.session_state.image_description_enabled = False
                
                # Ejecutar grafo (NUEVO: pasar memoria conversacional)
                print(f"\n🔍 [APP] Pasando conversation_memory a run_graph:")
                print(f"   Tipo: {type(st.session_state.conversation_memory)}")
                print(f"   Es None?: {st.session_state.conversation_memory is None}")
                if st.session_state.conversation_memory:
                    print(f"   Mensajes en memoria: {len(st.session_state.conversation_memory.messages)}")

                # Debug: mostrar backend y modo streaming justo antes de ejecutar el grafo
                print(f"   DEBUG [APP] session_state.llm_backend: {st.session_state.get('llm_backend', None)!r}, llm_model_name: {st.session_state.get('llm_model_name', None)!r}")
                print(f"   DEBUG [APP] session_state.streaming_mode: {st.session_state.get('streaming_mode', None)!r}")
                # Placeholders for status and incremental progress (visible in both modes)
                status_placeholder = st.empty()
                progress_placeholder = st.empty()

                # Initial status: searching
                try:
                    status_placeholder.info("🔎 Buscando información...")
                except Exception:
                    pass

                # If streaming mode is enabled, run `run_graph` in a background thread
                # and poll a queue from the main thread to update the UI incrementally.
                if st.session_state.get("streaming_mode", False):
                    stream_buffer = []
                    q = queue.Queue()
                    result_container = {}

                    with st.chat_message("assistant", avatar="🤖"):
                        stream_placeholder = st.empty()
                        sources_placeholder = st.empty()

                        def _enqueue_handler(chunk):
                            try:
                                q.put_nowait(chunk)
                            except Exception:
                                pass

                        def _bg_run(ctx=None, conv_mem=None, backend_snapshot=None, model_snapshot=None):
                            try:
                                # Attach Streamlit ScriptRunContext to this thread if available
                                try:
                                    if ctx is not None and add_script_run_ctx is not None:
                                        add_script_run_ctx(ctx)
                                except Exception:
                                    pass

                                # Use snapshots captured from main thread to avoid accessing session_state
                                cm = conv_mem
                                backend = backend_snapshot or "ollama"
                                model_name = model_snapshot

                                res = run_graph(
                                    query_with_context,
                                    cm,
                                    llm_backend=backend,
                                    llm_model=model_name,
                                    stream=True,
                                    llm_stream_handler=_enqueue_handler
                                )
                                result_container['result'] = res
                            except Exception as e:
                                result_container['result'] = {"success": False, "error": str(e)}

                        # Capture current ScriptRunContext (may be None) and pass to thread
                        ctx = None
                        try:
                            if get_script_run_ctx is not None:
                                ctx = get_script_run_ctx()
                        except Exception:
                            ctx = None

                        # Snapshot conversation_memory to avoid accessing session_state in background thread
                        conv_snapshot = None
                        try:
                            conv_snapshot = st.session_state.conversation_memory
                        except Exception:
                            conv_snapshot = None

                        # Snapshot conversation_memory, backend and model to avoid accessing session_state in background
                        conv_snapshot = None
                        backend_snapshot = None
                        model_snapshot = None
                        try:
                            conv_snapshot = st.session_state.conversation_memory
                            backend_snapshot = st.session_state.get("llm_backend", "ollama")
                            model_snapshot = st.session_state.get("llm_model_name")
                        except Exception:
                            conv_snapshot = None
                            backend_snapshot = None
                            model_snapshot = None

                        bg_thread = threading.Thread(target=_bg_run, args=(ctx, conv_snapshot, backend_snapshot, model_snapshot), daemon=True)
                        bg_thread.start()


                        # Poll the queue and update placeholder while background thread runs
                        # Mapa de señales de control → texto de estado
                        _STATUS_LABELS = {
                            "__CONTEXT_ANALYSIS__": "🧠 Analizando contexto conversacional...",
                            "__PLANNING__":          "🤖 Planificando búsqueda...",
                            "__DB_SEARCH__":         "🗃️ Buscando en base de datos...",
                            "__SEARCH_DONE__":        "🤔 Preparando respuesta...",
                            "__GENERATING__":         "✍️ Generando respuesta...",
                        }

                        first_chunk_seen = False
                        while bg_thread.is_alive() or not q.empty():
                            try:
                                chunk = q.get(timeout=0.1)
                                # Señales de control de fases
                                if isinstance(chunk, str) and chunk in _STATUS_LABELS:
                                    try:
                                        status_placeholder.info(_STATUS_LABELS[chunk])
                                    except Exception:
                                        pass
                                    continue

                                # If the producer sent structured messages (dict), handle types
                                if isinstance(chunk, dict):
                                    ctype = chunk.get("type")
                                    if ctype == "sources":
                                        payload = chunk.get("payload") or chunk.get("sources") or []
                                        # Do not render sources during streaming; store them for final result
                                        try:
                                            if payload:
                                                res = result_container.get('result', {})
                                                res['sources'] = payload
                                                res['num_sources'] = len(payload)
                                                result_container['result'] = res
                                        except Exception:
                                            pass
                                        continue
                                    elif ctype == "chunk":
                                        text = chunk.get("text", "")
                                        # treat like a normal fragment
                                        chunk = text
                                    elif ctype == "final":
                                        final_text = chunk.get("response", "")
                                        try:
                                            stream_buffer = [final_text]
                                            stream_placeholder.markdown(f'<div class="response-box">{final_text}</div>', unsafe_allow_html=True)
                                            progress_placeholder.empty()
                                            status_placeholder.success("✅ Respuesta lista")
                                        except Exception:
                                            pass
                                        # also propagate some metadata into result_container if available
                                        try:
                                            res = result_container.get('result', {})
                                            res['response'] = final_text
                                            if 'sources' in chunk:
                                                res['sources'] = chunk.get('sources')
                                                res['num_sources'] = len(res['sources'])
                                            if 'response_time' in chunk:
                                                res['response_time'] = chunk.get('response_time')
                                            if 'sql_queries' in chunk:
                                                res['sql_queries'] = chunk.get('sql_queries')
                                            result_container['result'] = res
                                        except Exception:
                                            pass
                                        continue

                                # On first real content chunk, update status to Generating
                                if not first_chunk_seen:
                                    try:
                                        status_placeholder.info("✍️ Generando respuesta...")
                                    except Exception:
                                        pass
                                    first_chunk_seen = True

                                stream_buffer.append(chunk)
                                stream_placeholder.markdown(f'<div class="response-box">{"".join(stream_buffer)}</div>', unsafe_allow_html=True)
                                # also update a small progress area
                                try:
                                    progress_placeholder.caption(f"Recibiendo {len(stream_buffer)} fragmentos...")
                                except Exception:
                                    pass
                            except queue.Empty:
                                # allow UI to refresh
                                time.sleep(0.05)

                        # Background finished; collect result
                        result = result_container.get('result', {})

                        # If we collected streamed chunks, prefer that as final response
                        if stream_buffer:
                            result["response"] = "".join(stream_buffer)

                        # Final status
                        try:
                            status_placeholder.success("✅ Respuesta lista")
                        except Exception:
                            pass
                        try:
                            progress_placeholder.empty()
                        except Exception:
                            pass
                        
                        # # Mostrar tiempos desglosados si están disponibles
                        # if result.get("search_time") or result.get("response_time"):
                        #     search_time = result.get("search_time", 0)
                        #     response_time = result.get("response_time", 0)
                        #     total_time = search_time + response_time
                        #     st.caption(f"⏱️ Búsqueda: {search_time:.2f}s | Respuesta: {response_time:.2f}s | Total: {total_time:.2f}s")
                        # elif result.get("elapsed_time"):
                        #     elapsed = result.get("elapsed_time", 0)
                        #     st.caption(f"⏱️ Tiempo total: {elapsed:.2f}s")
                        
                        # # Verificar si hay resultados de imágenes
                        # search_results = result.get("search_results", [])
                        # image_results = [
                        #     r for r in search_results
                        #     if r.get("png_url") or r.get("table_source") == "image_embeddings" or r.get("src")
                        # ]
                        
                        # if image_results:
                        #     st.markdown("#### 🖼️ Imágenes encontradas:")
                        #     cols = st.columns(min(3, len(image_results)))
                        #     for idx, img_result in enumerate(image_results[:3]):  # Max 3 imágenes
                        #         png_url = img_result.get("png_url") or img_result.get("src")
                        #         if png_url:
                        #             with cols[idx % 3]:
                        #                 relevance_img = img_result.get("relevance_score") or img_result.get("rerank_score") or img_result.get("similarity") or img_result.get("score") or img_result.get("relevance")
                        #                 if isinstance(relevance_img, (int, float)):
                        #                     rel_str = f"{relevance_img:.2f}"
                        #                 else:
                        #                     rel_str = str(relevance_img) if relevance_img is not None else "N/A"
                        #                 st.image(png_url, width=200, caption=f"Relevancia: {rel_str}")
                else:
                    # ── Modo bloque (no-streaming) ──────────────────────────────────────
                    # Se ejecuta run_graph en un hilo para poder actualizar el estado
                    # del placeholder en tiempo real mediante señales de control.
                    _ctrl_queue: queue.Queue = queue.Queue()

                    _STATUS_LABELS_BLOCK = {
                        "__CONTEXT_ANALYSIS__": "🧠 Analizando contexto conversacional...",
                        "__PLANNING__":          "🤖 Planificando búsqueda...",
                        "__DB_SEARCH__":          "🗜️ Buscando en base de datos...",
                        "__SEARCH_DONE__":        "🤔 Preparando respuesta...",
                        "__GENERATING__":         "✍️ Generando respuesta...",
                    }

                    def _ctrl_handler(chunk):
                        """Handler para el modo bloque: sólo encola señales de control."""
                        if isinstance(chunk, str) and chunk in _STATUS_LABELS_BLOCK:
                            try:
                                _ctrl_queue.put_nowait(chunk)
                            except Exception:
                                pass
                        # Los chunks de texto se descartan (mode streaming=False)

                    _block_result: dict = {}

                    def _bg_block(conv_mem, backend, model):
                        try:
                            res = run_graph(
                                query_with_context,
                                conv_mem,
                                llm_backend=backend,
                                llm_model=model,
                                stream=False,
                                llm_stream_handler=_ctrl_handler,
                            )
                            _block_result['result'] = res
                        except Exception as exc:
                            _block_result['result'] = {"success": False, "error": str(exc)}

                    _bg_block_thread = threading.Thread(
                        target=_bg_block,
                        args=(
                            st.session_state.conversation_memory,
                            st.session_state.get("llm_backend", "ollama"),
                            st.session_state.get("llm_model_name"),
                        ),
                        daemon=True,
                    )
                    _bg_block_thread.start()

                    # Consumir señales de control y actualizar estado en tiempo real
                    while _bg_block_thread.is_alive() or not _ctrl_queue.empty():
                        try:
                            signal = _ctrl_queue.get(timeout=0.1)
                            label = _STATUS_LABELS_BLOCK.get(signal)
                            if label:
                                try:
                                    status_placeholder.info(label)
                                except Exception:
                                    pass
                        except queue.Empty:
                            time.sleep(0.05)

                    _bg_block_thread.join(timeout=5)
                    result = _block_result.get('result', {"success": False, "error": "No result"})

                    # Finalize status for blocking mode
                    try:
                        status_placeholder.success("✅ Respuesta lista")
                        progress_placeholder.empty()
                    except Exception:
                        pass
                
                # Calcular tiempo transcurrido
                elapsed_time = time.time() - start_time
                result["elapsed_time"] = elapsed_time
                
                # 🆕 DEBUG: Verificar tiempos que vienen de run_graph
                print(f"🔍 APP DEBUG AFTER run_graph: db_search_time={result.get('db_search_time', 'N/A')}, reranking_time={result.get('reranking_time', 'N/A')}, response_time={result.get('response_time', 'N/A')}")
                
                st.session_state.last_response = result
                st.session_state.last_response_source = "chat"
                
                # CONSOLIDAR: Agregar respuesta al historial INMEDIATAMENTE en ambos modos (streaming y bloque)
                # para que persista si cambian widgets o hace rerun Streamlit
                if result.get("response"):
                    st.session_state.messages.append({"role": "assistant", "content": result["response"]})
                # Log assistant response for streaming and non-streaming flows
                try:
                    _log_assistant_response(
                        user_query,
                        result.get("response"),
                        st.session_state.session_id,
                        {
                            "source": "chat",
                            "elapsed_time": result.get("elapsed_time"),
                            "search_time": result.get("search_time"),
                            "response_time": result.get("response_time"),
                            "query_type": result.get("query_type"),
                            "search_classification": result.get("search_classification"),
                            "sql_queries": result.get("sql_queries"),
                        }
                    )
                except Exception:
                    pass
                
            except Exception as e:
                elapsed_time = time.time() - start_time
                st.session_state.last_response = {"error": str(e), "elapsed_time": elapsed_time}

        # Mostrar enhanced_query si está disponible en el resultado ACTUAL
        if (st.session_state.last_response and 
            st.session_state.last_response.get("enhanced_query") and
            st.session_state.last_response.get("enhanced_query") != user_query and
            CONVERSATION_MEMORY_CONFIG.get("show_enhanced_query", True)):
            
            enhanced_query = st.session_state.last_response["enhanced_query"]
            enhanced_placeholder.caption(f"🔍 *Interpretado como:* {enhanced_query}")
        
        # Mostrar respuesta del asistente (no crear otro mensaje si estamos en streaming,
        # ya se creó el assistant message antes de ejecutar el grafo)
        if not st.session_state.get("streaming_mode", False):
            with st.chat_message("assistant", avatar="🤖"):
                result = st.session_state.last_response
            
            # Mostrar tiempos desglosados si están disponibles
            if result.get("db_search_time") is not None or result.get("reranking_time") is not None:
                # Mostrar desglose completo si está disponible
                db_time = result.get("db_search_time", 0)
                rerank_time = result.get("reranking_time", 0)
                search_time = result.get("search_time", 0)
                response_time = result.get("response_time", 0)
                
                timing_str = f"⏱️ Búsqueda: BD {db_time:.2f}s"
                if rerank_time > 0:
                    timing_str += f" + Reranking {rerank_time:.2f}s"
                timing_str += f" = {search_time:.2f}s total"
                timing_str += f" | Respuesta: {response_time:.2f}s | Total: {search_time + response_time:.2f}s"
                st.caption(timing_str)
            elif result.get("search_time") or result.get("response_time"):
                search_time = result.get("search_time", 0)
                response_time = result.get("response_time", 0)
                total_time = search_time + response_time
                st.caption(f"⏱️ Búsqueda: {search_time:.2f}s | Respuesta: {response_time:.2f}s | Total: {total_time:.2f}s")
            elif result.get("elapsed_time"):
                elapsed = result["elapsed_time"]
                st.caption(f"⏱️ Tiempo total: {elapsed:.2f}s")
            
            # Mostrar respuesta
            if result.get("response"):
                st.markdown(f'<div class="response-box">{result["response"]}</div>', unsafe_allow_html=True)

                try:
                    _log_assistant_response(user_query, result.get("response"), st.session_state.session_id, {
                        "source": "chat",
                        "elapsed_time": result.get("elapsed_time"),
                        "search_time": result.get("search_time"),
                        "response_time": result.get("response_time"),
                        "query_type": result.get("query_type"),
                        "search_classification": result.get("search_classification"),
                        "sql_queries": result.get("sql_queries"),
                    })
                except Exception:
                    pass

                # Verificar si hay resultados de imágenes
                search_results = result.get("search_results", [])
                image_results = [
                    r for r in search_results
                    if r.get("png_url") or r.get("table_source") == "image_embeddings" or r.get("src")
                ]

                # Si la búsqueda es de imágenes, forzar MATCH sobre descripción y caption_literal además de embedding
                # y rerankear ANTES de enviar al LLM
                if image_results:
                    # Forzar reranking siempre antes de mostrar y enviar al LLM
                    # print(f"\n🔍 [APP] Rerankeando {len(image_results)} resultados de imagen antes de mostrar al usuario y enviar al LLM...")
                    # reranked = _rerank_results(user_query, image_results, len(image_results))
                    # for i, r in enumerate(image_results):
                    #     if i < len(reranked):
                    #         r.update(reranked[i])

                    st.markdown("#### 🖼️ Imágenes encontradas:")
                    cols = st.columns(min(3, len(image_results)))
                    for idx, img_result in enumerate(image_results[:3]):  # Max 3 imágenes en el chat
                        png_url = img_result.get("png_url") or img_result.get("src")
                        if png_url:
                            with cols[idx % 3]:
                                relevance_img = img_result.get("relevance_score") or img_result.get("rerank_score") or img_result.get("similarity") or img_result.get("score") or img_result.get("relevance")
                                if isinstance(relevance_img, (int, float)):
                                    rel_str = f"{relevance_img:.2f}"
                                else:
                                    rel_str = str(relevance_img) if relevance_img is not None else "N/A"
                                st.image(png_url, width=200, caption=f"Relevancia: {rel_str}")
            elif result.get("error"):
                st.markdown(f'<div class="error-box">❌ Error: {result["error"]}</div>', unsafe_allow_html=True)
            else:
                st.warning("No se pudo generar respuesta")
            
            # Rerun para mostrar imágenes si existen
            if result.get("search_results"):
                st.rerun()

with tabs[1]:
    st.markdown("### 🔎 Búsqueda de Imágenes")

    # Layout en tres columnas: izquierda -> búsqueda por texto, centro -> subida y controles, derecha -> vista previa
    col_text, col_upload, col_preview = st.columns([1, 1, 1])

    with col_text:
        st.markdown("**Búsqueda por texto**")
        text_query = st.text_input(
            "Descripción textual de la imagen (opcional)",
            value="",
            help="Describe la imagen que quieres buscar (si no subes una imagen, se hará búsqueda por texto usando embeddings CLIP)",
            key="image_text_query",
        )
        default_max_image_results = st.session_state.get("max_image_results", 60)
        max_image_results = st.slider(
            "Número de imágenes a mostrar",
            min_value=1,
            max_value=100,
            value=default_max_image_results,
            step=1,
            key="max_image_results_slider_tab"
        )
        st.session_state.max_image_results = max_image_results
        # Botón de ejecución colocado en la columna 1 (texto)
        execute_search = col_text.button("🔍 Ejecutar búsqueda de imagen/texto", key="execute_image_search")

    with col_upload:
        st.markdown("**Búsqueda por imagen**")
        uploaded_file = st.file_uploader(
            "Subir imagen para buscar similares",
            type=["png", "jpg", "jpeg"],
            help="Sube una imagen para encontrar páginas similares en la base de datos",
            key="uploaded_image_file",
        )

    with col_preview:
        st.markdown("**Vista previa**")
        # Mini preview en recuadro pequeño
        if uploaded_file is not None:
            try:
                uploaded_file.seek(0)
                img = Image.open(uploaded_file)
                st.image(img, width=220, caption="Vista previa")
            except Exception:
                st.warning("No se pudo mostrar la vista previa de la imagen.")
        # Mostrar descripción generada o error asociado
        desc = st.session_state.get("image_description")
        desc_err = st.session_state.get("image_description_error", False)
        if desc_err:
            st.error("❌ No se pudo generar descripción para la imagen.")
        elif desc:
            st.info(f"📝 {desc}")
    # Manejar la ejecución de búsqueda tras renderizar las columnas (uploaded_file ya está disponible)
    try:
        if execute_search:
            embedding = None
            image_description = text_query or None

            if uploaded_file is not None:
                try:
                    uploaded_file.seek(0)
                    image_bytes = uploaded_file.read()
                except Exception:
                    st.error("Error leyendo la imagen subida. Intenta subirla de nuevo.")
                    image_bytes = None

                if image_bytes:
                    # Generar siempre la descripción/título de la imagen
                    with st.spinner("🤖 Generando descripción de la imagen con modelo multimodal..."):
                        image_description = get_image_description(image_bytes)
                        if image_description:
                            st.info(f"📝 Título generado: {image_description}")
                            st.session_state.image_description_error = False
                        else:
                            st.error("❌ No se pudo generar una descripción de la imagen. El análisis no estará disponible.")
                            st.session_state.image_description_error = True

                    with st.spinner("🔢 Generando embedding de la imagen..."):
                        embedding = get_image_embedding(image_bytes)

            elif text_query:
                # Generar embedding CLIP de texto para búsqueda por texto
                from tools import get_clip_text_embedding
                with st.spinner("🔢 Generando embedding CLIP para la descripción..."):
                    embedding = get_clip_text_embedding(text_query)

            if not embedding:
                st.warning("No se pudo generar embedding para la búsqueda. Revisa tu entrada o sube una imagen válida.")
            else:
                with st.spinner("🔍 Buscando imágenes similares en la base de datos..."):
                    results, sql_query = search_similar_images(embedding, limit=st.session_state.get("max_image_results", 60), rerank_query=image_description)
                if results:
                    st.session_state.image_search_results = results
                    st.session_state.image_description = image_description or text_query
                    # Desactivar generación de respuesta si hubo error generando la descripción
                    if st.session_state.get("image_description_error", False):
                        st.session_state.image_description_enabled = False
                    else:
                        st.session_state.image_description_enabled = True if (image_description or text_query) else False
                    st.session_state.image_search_sql = sql_query
                    st.success(f"✅ Encontradas {len(results)} imágenes similares")
                    st.rerun()
                else:
                    st.warning("No se encontraron imágenes similares")
    except Exception as e:
        st.error(f"Error al ejecutar la búsqueda: {e}")

    # Si ya hay resultados en sesión, mostrarlos aquí y permitir analizarlos
    if st.session_state.image_search_results:
        try:
            with st.expander("🔍 Resultados de búsqueda por imagen"):
                render_image_results(st.session_state.image_search_results, "Resultados de Búsqueda por Imagen")
        except Exception as e:
            st.error(f"Error mostrando resultados: {e}")

        # Mostrar descripción si existe
        if st.session_state.image_description and st.session_state.image_description_enabled:
            st.info(f"📝 **Título de la imagen:** {st.session_state.image_description}")

        # Botón para analizar con el agente (usa reranking antes de generar respuesta)
        if st.button("🤖 Analizar resultados con el agente conversacional", key="analyze_image_results_tab"):
            # Si previamente hubo un error generando la descripción, no iniciar el análisis
            if st.session_state.get("image_description_error", False):
                st.error("No se puede analizar: no se generó una descripción válida para la imagen.")
                st.stop()

            analysis_query = st.session_state.image_description or ""

            # Registrar la consulta del usuario
            try:
                _log_user_query(f"Analizar resultados de imagen: {analysis_query}", st.session_state.session_id)
            except Exception:
                pass

            # Mostrar el mensaje del usuario en la conversación
            with st.chat_message("user", avatar="👤"):
                st.write(f"Analizar resultados de imagen: {analysis_query}")

            with st.chat_message("assistant", avatar="🤖"):
                with st.spinner("🔄 Analizando resultados..."):
                    try:
                        # Antes de generar la respuesta, rerankear los resultados de imagen
                        try:
                            rerank_limit = st.session_state.get("max_image_results", 60)
                            current_results = st.session_state.image_search_results or []
                            # Skip reranking if results already have reranker scores
                            need_rerank = True
                            if isinstance(current_results, list) and any((isinstance(r, dict) and (r.get('relevance_score') is not None or r.get('rerank_score') is not None)) for r in current_results):
                                need_rerank = False

                            if need_rerank:
                                print("   🔄 Rerankeando resultados de imagen antes de generar respuesta...")
                                reranked = _rerank_results(analysis_query, current_results, rerank_limit)
                                if reranked:
                                    st.session_state.image_search_results = reranked
                            else:
                                print("   ℹ️ Skipping rerank: results already contain reranker scores")
                        except Exception as e:
                            print(f"⚠️ Error rerankeando resultados de imagen: {e}")

                        result = generate_response_internal(
                            user_query=analysis_query,
                            search_results=st.session_state.image_search_results,
                            additional_context="Resultados de búsqueda por imagen similar (usar description y caption si existen).",
                            llm_backend=st.session_state.get("llm_backend", "ollama"),
                            llm_model=st.session_state.get("llm_model_name")
                        )
                        st.session_state.last_response = result
                        st.session_state.last_response_source = "image_search"

                        if result.get("response"):
                            st.markdown(f'<div class="response-box">{result["response"]}</div>', unsafe_allow_html=True)
                            st.session_state.messages.append({"role": "assistant", "content": result["response"]})
                            try:
                                _log_assistant_response(
                                    analysis_query,
                                    result.get("response"),
                                    st.session_state.session_id,
                                    {
                                        "source": "image_search", "sql_queries": [{"table": "content_image_embeddings", "sql": st.session_state.image_search_sql}] if st.session_state.image_search_sql else None
                                    }
                                )
                            except Exception as e:
                                print(f"Error logging assistant response for image analysis: {e}")
                        else:
                            st.warning("No se pudo generar respuesta")

                        # Limpiar descripción después de usar
                        st.session_state.image_description = None
                        st.session_state.image_description_enabled = False

                    except Exception as e:
                        st.error(f"Error: {str(e)}")


# Panel de información adicional
if st.session_state.last_response:
    st.markdown("---")
    
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    
    with col1:
        num_sources = len(st.session_state.last_response.get("sources", []))
        st.metric("Fuentes encontradas", num_sources)
    
    with col2:
        if st.session_state.last_response.get("prompt_used"):
            st.metric("Prompt disponible", "✅ Sí")
        else:
            st.metric("Prompt disponible", "❌ No")
    
    with col3:
        db_search_time = st.session_state.last_response.get("db_search_time", 0)
        st.metric("🔍 Búsqueda BD", f"{db_search_time:.2f}s")
    
    with col4:
        reranking_time = st.session_state.last_response.get("reranking_time", 0)
        st.metric("🔄 Reranking", f"{reranking_time:.2f}s")
    
    with col5:
        response_time = st.session_state.last_response.get("response_time", 0)
        st.metric("💬 Respuesta", f"{response_time:.2f}s")
    
    with col6:
        if st.session_state.last_response.get("success"):
            st.metric("Estado", "✅ Completado")
        else:
            st.metric("Estado", "❌ Error")
    
    # Desplegable con fuentes
    if st.session_state.last_response.get("sources"):
        with st.expander("📚 Ver fuentes consultadas"):
            for source in st.session_state.last_response["sources"]:
                source_text = f"""
                **Documento {source.get('document', 'N/A')}**
                - Ejemplar: {source.get('magazine_id', 'N/A')}
                - Página: {source.get('page_number', 'N/A')}"""
                # Agregar relevancia si existe (score, similarity o relevance)
                relevance_val = source.get('score') or source.get('similarity') or source.get('relevance')
                if relevance_val is not None:
                    if isinstance(relevance_val, (int, float)):
                        source_text += f"\n                - Relevancia: {relevance_val:.2f}"
                    else:
                        source_text += f"\n                - Relevancia: {relevance_val}"
                    
                # Agregar fecha si existe
                if source.get('date'):
                    source_text += f"\n                - Fecha: {source.get('date')}"
                
                source_text += f"\n                - Título: {source.get('title', 'N/A')}"
                
                # Agregar enlace PNG si existe
                if source.get('png_url'):
                    source_text += f"\n                - [🖼️ Ver página PNG]({source.get('png_url')})"
                
                st.markdown(source_text)
    
    # Desplegable con prompt
    if st.session_state.last_response.get("prompt_used"):
        with st.expander("🔍 Ver prompt enviado al LLM"):
            prompt_text = st.session_state.last_response["prompt_used"]
            st.markdown(f"```\n{prompt_text}\n```")
            st.info(f"📊 Prompt: {len(prompt_text)} caracteres")
    
    # Desplegable con resultados de búsqueda
    if st.session_state.last_response.get("search_results"):
        with st.expander("🔎 Ver resultados de búsqueda (debug)"):
            st.json(st.session_state.last_response["search_results"][:5])  # Mostrar top 5
            st.info(f"📊 Total de resultados: {len(st.session_state.last_response['search_results'])}")
    
    # Desplegable con consultas SQL ejecutadas - MOSTRAR SIEMPRE si hay queries
    sql_queries = st.session_state.last_response.get("sql_queries", [])
    if sql_queries and len(sql_queries) > 0:
        with st.expander("🗄️ Ver consultas SQL ejecutadas"):
            for idx, query_info in enumerate(sql_queries, 1):
                st.markdown(f"**Query {idx}** - Tabla: `{query_info.get('table', 'N/A')}`")
                st.code(query_info.get("sql", ""), language="sql")
                st.markdown("---")
            
            st.info(f"📝 Total de queries SQL: {len(sql_queries)}")
    
    # Desplegable con respuesta completa del grafo
    with st.expander("🤖 Respuesta completa del grafo (debug)"):
        debug_info = {
            "success": st.session_state.last_response.get("success"),
            "num_sources": len(st.session_state.last_response.get("sources", [])),
            "num_search_results": len(st.session_state.last_response.get("search_results", [])),
            "num_sql_queries": len(st.session_state.last_response.get("sql_queries", [])),
            "prompt_length": len(st.session_state.last_response.get("prompt_used", "")),
            "response_length": len(st.session_state.last_response.get("response", "")),
            "error": st.session_state.last_response.get("error", ""),
        }
        st.json(debug_info)


# Pie de página
st.markdown("---")
# Mostrar backend y modelo seleccionado en el pie de página
selected_backend = st.session_state.get("llm_backend", "ollama")
selected_model = st.session_state.get("llm_model_name") or (GEMINI_MODEL if selected_backend == "gemini" else OLLAMA_LLM_MODEL)
backend_label = "Ollama" if selected_backend == "ollama" else "Gemini"
st.markdown(f"""
<div style="text-align: center; color: #666; font-size: 0.9rem;">
    💾 Base de datos: CrateDB | 🧭 Orquestador: {OLLAMA_LLM_MODEL} | 🧠 Backend: {selected_model} ({backend_label}) | 🔢 Embeddings texto: {OLLAMA_EMBEDDING_MODEL} | 🔢 Embeddings imagen: {IMAGE_EMBEDDING_MODEL} | 🎯 Framework: LangGraph | 🔄 Reranker: {RERANKER_MODEL}
</div>
""", unsafe_allow_html=True)
