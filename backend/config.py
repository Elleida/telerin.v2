"""
Configuración del backend FastAPI — TeleRadio Multi-Agent Team (telerin.v2)
"""
import os
from dotenv import load_dotenv

# Siempre cargar el .env desde la raíz del proyecto (un nivel arriba de backend/)
_here = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_here, "..", ".env"))

# ── CrateDB ────────────────────────────────────────────────────────────────
CRATEDB_URL      = os.getenv("CRATEDB_URL", "http://localhost:4200/_sql")
CRATEDB_USERNAME = os.getenv("CRATEDB_USERNAME", "crate")
CRATEDB_PASSWORD = os.getenv("CRATEDB_PASSWORD", "")

# ── Ollama ─────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL        = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_LLM_MODEL       = os.getenv("OLLAMA_LLM_MODEL", "llama3.2")
OLLAMA_EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
GEMINI_MODEL           = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_API_KEY         = os.getenv("GEMINI_API_KEY", "")

# Alias para image_search.py
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", OLLAMA_BASE_URL)
IMAGE_DESCRIPTION_MODEL = os.getenv("IMAGE_DESCRIPTION_MODEL", OLLAMA_LLM_MODEL)

# ── Search ─────────────────────────────────────────────────────────────────
COLLECTION_NAME    = os.getenv("COLLECTION_NAME", "teleradio_content")
RERANKER_MODEL     = os.getenv("RERANKER_MODEL", "qwen3rerank")
IMAGE_EMBEDDING_MODEL = os.getenv("IMAGE_EMBEDDING_MODEL", "")
PNG_BASE_URL       = os.getenv("PNG_BASE_URL", "")

# ── Search config ──────────────────────────────────────────────────────────────
SEARCH_CONFIG = {
    "default_table":   "teleradio_content_others",
    "default_limit":   10,
    "max_limit":       100,
    "embedding_field": "content_embedding",
    "bm25_weight":     float(os.getenv("BM25_WEIGHT", "0.3")),
    "vector_weight":   float(os.getenv("VECTOR_WEIGHT", "0.7")),
    "top_k":           int(os.getenv("TOP_K", "10")),
    "rerank_top_k":    int(os.getenv("RERANK_TOP_K", "5")),
    "tables": {
        "teleradio_content_image_embeddings": {
            "name": "image embeddings",
            "description": "Embeddings de imagenes",
            "search_fields": ["image_embedding", "description_embedding", "description", "caption_literal"],
            "display_fields": ["magazine_id", "page_number"],
            "embedding_field": ["image_embedding", "description_embedding"],
            "date_field": "published_date",
            "keywords": [
                "image embedding", "embedding de imagen", "vector de imagen", "buscar imagen", "buscar imagenes",
                "similar image", "similar images", "imagen similar", "imagenes similares",
                "foto embedding", "foto vector", "similar foto", "fotos similares"
            ],
        },
        "teleradio_content_editorial": {
            "name": "editorial",
            "description": "Artículos, reportajes y contenido editorial",
            "search_fields": ["main_title", "full_text", "brief_summary", "caption_literal"],
            "display_fields": ["main_title", "section_title", "page_number"],
            "keywords": ["artículo", "reportaje", "editorial", "noticia", "revista", "sección"],
        },
        "teleradio_content_tv_schedule": {
            "name": "programación TV",
            "description": "Programación de televisión",
            "search_fields": ["title", "content_description", "caption_literal"],
            "display_fields": ["title", "channel", "time", "date", "day_of_week"],
            "date_field": "date",
            "keywords": ["tv", "televisión", "canal", "programa", "horario", "emisión", "prime time"],
        },
        "teleradio_content_radio_schedule": {
            "name": "programación radio",
            "description": "Programación de radio",
            "search_fields": ["title", "content_description"],
            "display_fields": ["title", "station", "time", "date", "day_of_week"],
            "date_field": "date",
            "keywords": ["radio", "emisora", "onda", "frecuencia", "transmisión"],
        },
        "teleradio_content_advertising": {
            "name": "publicidad",
            "description": "Anuncios y contenido publicitario",
            "search_fields": ["ad_copy"],
            "display_fields": ["advertiser", "ad_copy"],
            "keywords": ["anuncio", "publicidad", "marca", "producto", "comercial"],
        },
        "teleradio_content_others": {
            "name": "otros contenidos",
            "description": "Otros contenidos generales",
            "search_fields": ["title", "content", "description"],
            "display_fields": ["title", "content"],
            "keywords": ["contenido", "general", "miscelánea"],
        },
    },
}

# ── Conversation memory ────────────────────────────────────────────────────
CONVERSATION_MEMORY_CONFIG = {
    "max_history":         int(os.getenv("MEMORY_MAX_HISTORY", "100")),
    "context_window":      int(os.getenv("MEMORY_CONTEXT_WINDOW", "5")),
    "auto_enhance_query":  os.getenv("MEMORY_AUTO_ENHANCE", "true").lower() == "true",
    "show_context_panel":  True,
    "show_enhanced_query": True,
}

# ── Auth / JWT ─────────────────────────────────────────────────────────────
JWT_SECRET_KEY   = os.getenv("JWT_SECRET_KEY", "CHANGE_ME_IN_PRODUCTION_USE_RANDOM_32_CHARS")
JWT_ALGORITHM    = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "8"))

# ── CORS (orígenes permitidos) ─────────────────────────────────────────────
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")

# ── Logging ────────────────────────────────────────────────────────────────
LOG_FILE     = os.getenv("QUERY_LOG_FILE",       "queries_responses.log")
LOG_MAX_BYTES = int(os.getenv("QUERY_LOG_MAX_BYTES", "5242880"))
LOG_BACKUP   = int(os.getenv("QUERY_LOG_BACKUP_COUNT", "5"))

# ── AI Server (VIVOClient) ─────────────────────────────────────────────────
AI_SERVER_URL = os.getenv("AI_SERVER_URL", "http://localhost:5001")
