"""
Configuración del agente chatbot LangGraph
"""
import os
import logging
from dotenv import load_dotenv

_log = logging.getLogger(__name__)

# Cargar variables de entorno
load_dotenv()

# Configuración de CrateDB
CRATEDB_URL = os.getenv("CRATEDB_URL")
CRATEDB_USERNAME = os.getenv("CRATEDB_USERNAME")
CRATEDB_PASSWORD = os.getenv("CRATEDB_PASSWORD")

# Configuración de Ollama
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL")
OLLAMA_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "llama3.2")
OLLAMA_EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

_log.info("🔧 CONFIGURACIÓN LANGGRAPH: OLLAMA_BASE_URL=%s  OLLAMA_LLM_MODEL=%s  CRATEDB_URL=%s",
          OLLAMA_BASE_URL, OLLAMA_LLM_MODEL, CRATEDB_URL)

# Configuración de la base de datos
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "teleradio_content")
PNG_BASE_URL = os.getenv("PNG_BASE_URL", "http://signal4.cps.unizar.es/rtve/teleradio/png")

# Configuración de Reranker
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "qwen3rerank")
# Embeddings de imágenes (opcional, solo si se usan)
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "http://gtc2pc9.cps.unizar.es:11434")
IMAGE_DESCRIPTION_MODEL = os.getenv("IMAGE_DESCRIPTION_MODEL", "qwen3-vl:8b")
IMAGE_EMBEDDING_MODEL = os.getenv("IMAGE_EMBEDDING_MODEL", "clip")
# Streaming de respuestas (opcional, solo si se quiere usar)
DISABLE_STREAMING = os.getenv("DISABLE_STREAMING", "0")

# Validación de configuración
def validate_config():
    """Valida que todas las variables de configuración críticas estén presentes"""
    missing = []
    
    # Variables críticas (deben estar presentes)
    if not CRATEDB_URL:
        missing.append("CRATEDB_URL")
    if not CRATEDB_USERNAME:
        missing.append("CRATEDB_USERNAME")
    if not OLLAMA_BASE_URL:
        missing.append("OLLAMA_BASE_URL")
    
    # Nota: CRATEDB_PASSWORD puede estar vacía (autenticación sin contraseña)
    # Nota: OLLAMA_LLM_MODEL tiene valor por defecto
    # Nota: OLLAMA_EMBEDDING_MODEL tiene valor por defecto
    
    if missing:
        raise ValueError(f"Variables de entorno faltantes: {', '.join(missing)}")
    
    return True

# Configuración de búsqueda - Estructura de tablas y campos
SEARCH_CONFIG = {
    "default_table": "teleradio_content_others",
    "default_limit": 10,
    "max_limit": 100,
    "embedding_field": "content_embedding",

    # Definición de tablas con sus campos de búsqueda REALES
    "tables": {
        "teleradio_content_image_embeddings": {
            "name": "image embeddings",
            "description": "Embeddings de imagenes",
            "search_fields": ["image_embedding","description_embedding","description","caption_literal"],
            "display_fields": ["magazine_id", "page_number"],
            "embedding_field": ["image_embedding", "description_embedding"],
            "date_field": "published_date",
            "keywords": [
                "image embedding", "embedding de imagen", "vector de imagen", "buscar imagen", "buscar imagenes",
                "similar image", "similar images", "imagen similar", "imagenes similares",
                "foto embedding", "foto vector", "similar foto", "fotos similares"
            ]
        },
        "teleradio_content_editorial": {
            "name": "editorial",
            "description": "Artículos, reportajes y contenido editorial",
            "search_fields": ["main_title", "full_text", "brief_summary","caption_literal"],
            "display_fields": ["main_title", "section_title", "page_number"],
            "keywords": ["artículo", "reportaje", "editorial", "noticia", "revista", "sección"]
        },
        "teleradio_content_tv_schedule": {
            "name": "programación TV",
            "description": "Programación de televisión",
            "search_fields": ["title", "content_description", "caption_literal"],
            "display_fields": ["title", "channel", "time", "date","day_of_week"],
            "date_field": "date",
            "keywords": ["tv", "televisión", "canal", "programa", "horario", "emisión", "prime time"]
        },
        "teleradio_content_radio_schedule": {
            "name": "programación radio",
            "description": "Programación de radio",
            "search_fields": ["title", "content_description"],
            "display_fields": ["title", "station", "time", "date","day_of_week"],
            "date_field": "date",
            "keywords": ["radio", "emisora", "onda", "frecuencia", "transmisión"]
        },
        "teleradio_content_advertising": {
            "name": "publicidad",
            "description": "Anuncios y contenido publicitario",
            "search_fields": ["ad_copy"],
            "display_fields": ["advertiser", "ad_copy"],
            "keywords": ["anuncio", "publicidad", "marca", "producto", "comercial", "advertencia"]
        },
        "teleradio_content_others": {
            "name": "otros contenidos",
            "description": "Otros contenidos generales",
            "search_fields": ["title", "content", "description"],
            "display_fields": ["title", "content"],
            "keywords": ["contenido", "general", "miscelánea"]
        }
    }
}

# Configuración de Memoria Conversacional
CONVERSATION_MEMORY_CONFIG = {
    "max_history": 100,  # Máximo de turnos a guardar
    "context_window": 5,  # Últimos N turnos como contexto activo
    "entity_extraction": True,  # Extraer entidades automáticamente
    "auto_enhance_query": True,  # Mejorar queries con contexto automáticamente
    "persistence": "session",  # session (memoria en sesión) o database (futuro)
    "show_enhanced_query": True,  # Mostrar query mejorada en UI
    "show_context_panel": True,  # Mostrar panel de contexto en sidebar
}

# Validar configuración al importar
try:
    validate_config()
    _log.info("✅ Configuración validada correctamente")
except ValueError as e:
    _log.error("❌ Error en configuración: %s", e)
    raise
