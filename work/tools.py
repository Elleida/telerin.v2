"""
Herramientas para el agente chatbot de TeleRadio - Versión LangGraph
Incluye herramientas de búsqueda híbrida y generación de respuestas
"""
import requests
from requests.auth import HTTPBasicAuth
from typing import Dict, List, Any, Optional
import json
import re
import os
import time
from functools import lru_cache
from langchain_core.tools import tool
from config import (
    CRATEDB_URL, CRATEDB_USERNAME, CRATEDB_PASSWORD,
    OLLAMA_BASE_URL, OLLAMA_EMBEDDING_MODEL, OLLAMA_LLM_MODEL,
    SEARCH_CONFIG, PNG_BASE_URL, COLLECTION_NAME
)

try:
    from vivoembclient import VIVOClient
    VIVOEMBCLIENT_AVAILABLE = True
except ImportError:
    VIVOEMBCLIENT_AVAILABLE = False

try:
    import google.genai as genai
    GENAI_AVAILABLE = True
except Exception:
    GENAI_AVAILABLE = False


# Initialize VIVO Client for image/text embeddings (cached)
@lru_cache(maxsize=1)
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


print(f"VIVOEMBCLIENT_AVAILABLE: {VIVOEMBCLIENT_AVAILABLE}")
print(f"GENAI_AVAILABLE: {GENAI_AVAILABLE}")


def call_llm_prompt(prompt: str, backend: str = "ollama", model: str | None = None, timeout: int = 60) -> str:
    """Llama al LLM seleccionado y devuelve la respuesta textual.

    - `backend='ollama'`: hace POST a `{OLLAMA_BASE_URL}/api/generate` con payload
      {model, prompt, stream: False} y devuelve el campo `response` o `text`.
    - `backend='gemini'|'google'`: intenta usar `google.genai` si está disponible.

    Lanza excepciones informativas si el backend no está disponible.
    """
    backend = (backend or "ollama").lower()
    if backend in ("gemini", "google"):
        model_to_use = model or os.getenv("GEMINI_MODEL") or "gemini-2.0-flash"
    else:
        model_to_use = model or os.getenv("OLLAMA_LLM_MODEL") or OLLAMA_LLM_MODEL

    if backend == "ollama":
        url = OLLAMA_BASE_URL.rstrip('/') + "/api/generate"
        payload = {"model": model_to_use, "prompt": prompt, "stream": False}
        resp = requests.post(url, json=payload, timeout=timeout)
        if resp.status_code != 200:
            raise Exception(f"Ollama error {resp.status_code}: {resp.text}")
        data = resp.json()
        if isinstance(data, dict):
            return data.get("response") or data.get("text") or json.dumps(data)
        return str(data)

    if backend in ("gemini", "google"):
        if not GENAI_AVAILABLE:
            raise Exception("google.genai no disponible en el entorno; no puede usarse 'gemini'")
        # Intentar llamada genérica a google.genai
        try:
            res = genai.generate_text(model=model_to_use, prompt=prompt)
            if hasattr(res, 'text'):
                return res.text
            if isinstance(res, dict):
                return res.get('output') or json.dumps(res)
            return str(res)
        except Exception:
            # Fallback a cliente si existe
            client_cls = getattr(genai, 'Client', None)
            if client_cls is None:
                raise
            client = client_cls()
            out = client.generate(model=model_to_use, prompt=prompt)
            return getattr(out, 'text', str(out))

    raise Exception(f"Backend LLM desconocido: {backend}")

# Variable global para almacenar el último prompt generado
LAST_GENERATED_PROMPT = None
LAST_USER_QUERY = None
LAST_SEARCH_RESULTS = None
LAST_EXECUTED_SQL_QUERIES = []  # Nueva variable para guardar todas las queries SQL ejecutadas

# Caché para clasificación de búsquedas genéricas/específicas
_GENERIC_SEARCH_CACHE = {}  # {search_query: bool}
SQL_RESULTS_LIMIT = 60
LLM_SCORE_THRESHOLD = 0.5


def _clamp_limit(limit_value: int, min_value: int = 1, max_value: int = 200) -> int:
    try:
        limit_int = int(limit_value)
    except (TypeError, ValueError):
        return min_value

    if limit_int < min_value:
        return min_value
    if limit_int > max_value:
        return max_value
    return limit_int


def set_sql_results_limit(limit_value: int):
    """Guarda el límite de resultados para consultas SQL."""
    global SQL_RESULTS_LIMIT
    SQL_RESULTS_LIMIT = _clamp_limit(limit_value, min_value=1, max_value=200)


def get_sql_results_limit() -> int:
    """Obtiene el límite de resultados para consultas SQL."""
    return SQL_RESULTS_LIMIT


def _clamp_threshold(value: float, min_value: float = 0.0, max_value: float = 1.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return min_value

    if v < min_value:
        return min_value
    if v > max_value:
        return max_value
    return v


def set_llm_score_threshold(threshold_value: float):
    """Guarda el umbral (0.0 - 1.0) de score para enviar documentos al LLM."""
    global LLM_SCORE_THRESHOLD
    LLM_SCORE_THRESHOLD = _clamp_threshold(threshold_value, min_value=0.0, max_value=1.0)


def get_llm_score_threshold() -> float:
    """Obtiene el umbral de score para enviar documentos al LLM."""
    return LLM_SCORE_THRESHOLD


def clear_generic_search_cache():
    """Limpia el caché de clasificación de búsquedas genéricas/específicas"""
    global _GENERIC_SEARCH_CACHE
    cache_size = len(_GENERIC_SEARCH_CACHE)
    _GENERIC_SEARCH_CACHE.clear()
    print(f"   🗑️  Caché de búsquedas genéricas limpiado ({cache_size} entradas eliminadas)")


def set_last_prompt(prompt_text):
    """Guarda el último prompt generado"""
    global LAST_GENERATED_PROMPT
    LAST_GENERATED_PROMPT = prompt_text


def get_last_executed_sql_queries():
    """Obtiene todas las queries SQL ejecutadas en la última búsqueda"""
    global LAST_EXECUTED_SQL_QUERIES
    queries = LAST_EXECUTED_SQL_QUERIES.copy() if LAST_EXECUTED_SQL_QUERIES else []
    print(f"   🔍 get_last_executed_sql_queries() retornando {len(queries)} queries")
    return queries


def set_last_executed_sql_queries(queries: List[Dict]):
    """Guarda todas las queries SQL ejecutadas"""
    global LAST_EXECUTED_SQL_QUERIES
    LAST_EXECUTED_SQL_QUERIES = queries if queries else []
    print(f"   💾 set_last_executed_sql_queries() guardado {len(LAST_EXECUTED_SQL_QUERIES)} queries")


def add_executed_sql_query(table: str, sql: str):
    """Agrega una query SQL ejecutada a la lista global"""
    global LAST_EXECUTED_SQL_QUERIES
    LAST_EXECUTED_SQL_QUERIES.append({"table": table, "sql": sql})
    print(f"   ➕ add_executed_sql_query() - Tabla: {table}, Total queries: {len(LAST_EXECUTED_SQL_QUERIES)}")


def clear_executed_sql_queries():
    """Limpia la lista de queries SQL ejecutadas"""
    global LAST_EXECUTED_SQL_QUERIES
    LAST_EXECUTED_SQL_QUERIES = []


def set_last_search_context(query: str, results: List[Dict]):
    """Guarda la query y resultados de búsqueda para construir prompts después"""
    global LAST_USER_QUERY, LAST_SEARCH_RESULTS
    LAST_USER_QUERY = query
    LAST_SEARCH_RESULTS = results if results else []


def get_last_search_context():
    """Obtiene la query y resultados de búsqueda guardados"""
    global LAST_USER_QUERY, LAST_SEARCH_RESULTS
    return LAST_USER_QUERY, LAST_SEARCH_RESULTS


def classify_query_intent(query: str) -> str:
    """
    Clasifica si la pregunta del usuario es un saludo, sobre el sistema/funcionalidades
    o si requiere búsqueda en la base de datos.
    
    Args:
        query: Pregunta del usuario
        
    Returns:
        "greeting" si es saludo/cortesía
        "system_info" si es pregunta sobre el sistema
        "data_search" si requiere búsqueda en la BD
    """
    if not query:
        return "data_search"
    
    prompt = f"""Clasifica la siguiente pregunta del usuario.

Si es un SALUDO, DESPEDIDA o FORMA DE CORDIALIDAD (como "Hola", "buenos días", "¿hay alguien ahí?", "gracias", "adiós", "¿cómo estás?"), responde: GREETING

Si es una pregunta sobre el FUNCIONAMIENTO, CARACTERÍSTICAS o CAPACIDADES del sistema TeleRadio de búsqueda de revistas (como "¿cómo funciona?", "¿qué puedo hacer?", "¿cuáles son las características?"), responde: SYSTEM_INFO

Si es una pregunta sobre CONTENIDO o DATOS en la base de datos de revistas de televisión (como buscar programas, fechas, anuncios, etc.), responde: DATA_SEARCH

Pregunta del usuario: "{query}"

Responde solo con una palabra: GREETING, SYSTEM_INFO o DATA_SEARCH"""

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_LLM_MODEL,
                "prompt": prompt,
                "stream": False,
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            classification = result.get("response", "data_search").strip().upper()
            
            if "GREETING" in classification:
                print(f"   🤖 Query clasificada como: GREETING (saludo/cortesía)")
                return "greeting"
            elif "SYSTEM_INFO" in classification:
                print(f"   🤖 Query clasificada como: SYSTEM_INFO (pregunta sobre el sistema)")
                return "system_info"
            else:
                print(f"   🤖 Query clasificada como: DATA_SEARCH (búsqueda en BD)")
                return "data_search"
        else:
            print(f"   ⚠️ Error clasificando query (status {response.status_code}), asumiendo DATA_SEARCH")
            return "data_search"
    except Exception as e:
        print(f"   ⚠️ Error en clasificación: {str(e)}, asumiendo DATA_SEARCH")
        return "data_search"


def _is_collection_stats_query(query: str) -> bool:
    """
    Detecta si la consulta pide estadisticas generales de la coleccion.

    Ejemplos: numero de ejemplares, total de paginas, revistas por ano.
    """
    if not query:
        return False

    query_lower = query.lower()
    patterns = [
        "numero de ejemplares",
        "número de ejemplares",
        "cuantos ejemplares",
        "cantidad de ejemplares",
        "total de paginas",
        "total de páginas",
        "numero total",
        "número total",
        "cuantas paginas",
        "cuántas paginas",
        "cuantas páginas",
        "cuántas páginas",
        "total de revistas",
        "revistas por ano",
        "revistas por año",
        "por ano",
        "por año",
        "estadisticas",
        "estadísticas",
        "coleccion",
        "colección",
        "metadatos",
        "metadata",
        "numero de paginas",
        "número de paginas",
        "número de páginas"
    ]

    return any(pattern in query_lower for pattern in patterns)


def classify_query_tables(query: str) -> List[str]:
    """
    Clasifica la consulta del usuario para determinar qué tablas consultar.
    Analiza palabras clave para elegir automáticamente las tablas más relevantes.
    ⚠️ IMPORTANTE: teleradio_content_editorial SIEMPRE se incluye, excepto en estadisticas generales.
    
    Args:
        query: Texto de la consulta del usuario
        
    Returns:
        Lista de nombres de tablas a consultar
    """
    query_lower = query.lower()
    tables_to_search = []
    
    # Tabla de definiciones de tablas con palabras clave
    table_definitions = SEARCH_CONFIG["tables"]

    # Puntuación de relevancia para cada tabla
    table_scores = {table_name: 0 for table_name in table_definitions.keys()}
    
    # Analizar palabras clave
    for table_name, table_info in table_definitions.items():
        keywords = table_info.get("keywords", [])
        for keyword in keywords:
            # Búsqueda de palabra clave (palabra completa o como substring)
            if keyword.lower() in query_lower:
                table_scores[table_name] += 2
            elif any(keyword.lower() in word for word in query_lower.split()):
                table_scores[table_name] += 1
    
    # Seleccionar tablas con puntuación > 0
    scored_tables = [(name, score) for name, score in table_scores.items() if score > 0]
    
    if scored_tables:
        # Ordenar por puntuación y tomar top 2-3 tablas
        scored_tables.sort(key=lambda x: x[1], reverse=True)
        tables_to_search = [table_name for table_name, _ in scored_tables[:3]]
    else:
        # Si no hay coincidencias de palabras clave, usar todas las tablas
        # (búsqueda más amplia)
        tables_to_search = list(table_definitions.keys())
    
    # ✅ GARANTIZAR: teleradio_content_editorial SIEMPRE está incluida (tabla obligatoria)
    if "teleradio_content_editorial" not in tables_to_search:
        tables_to_search.append("teleradio_content_editorial")
        print(f"   ✅ Tabla content_editorial AÑADIDA (tabla obligatoria en todas las búsquedas)")
    else:
        print(f"   ✅ Tabla content_editorial YA INCLUIDA")
    
    return tables_to_search


def _normalize_date_parts(year: int, month: int, day: int) -> Optional[str]:
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _extract_date_filter(query: str, date_field: str) -> str:
    """
    Extrae fechas del texto y construye un filtro SQL para el campo de fecha.
    Soporta múltiples formatos:
    - Numéricos: YYYY-MM-DD, YYYY/MM/DD, DD-MM-YYYY, DD/MM/YYYY
    - Textuales: "15 de enero de 1958", "enero 15 de 1958", "enero 15, 1958"
    - Solo año: YYYY
    """
    if not query or not date_field:
        return ""

    text = query.strip()
    
    # Mapeo de meses en español a número
    months_spanish = {
        'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6,
        'julio': 7, 'agosto': 8, 'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12
    }

    # Patrón 1: "DD de MES de YYYY" o "DD de MES YYYY" (ej: "15 de enero de 1958" o "15 de enero 1958")
    pattern1 = r"\b(0[1-9]|[12]\d|3[01])\s+de\s+(" + "|".join(months_spanish.keys()) + r")\s+(?:de\s+)?(19\d{2}|20\d{2})\b"
    match = re.search(pattern1, text, re.IGNORECASE)
    if match:
        day = int(match.group(1))
        month = months_spanish[match.group(2).lower()]
        year = int(match.group(3))
        date_value = _normalize_date_parts(year, month, day)
        if date_value:
            print(f"   📅 Fecha detectada (formato texto): '{match.group(0)}' → {date_value}")
            return f"{date_field} = '{date_value}'"

    # Patrón 2: "MES DD de YYYY" o "MES DD, YYYY" (ej: "enero 15 de 1958" o "enero 15, 1958")
    pattern2 = r"\b(" + "|".join(months_spanish.keys()) + r")\s+(0[1-9]|[12]\d|3[01])(?:\s+de|\s*,)?\s+(19\d{2}|20\d{2})\b"
    match = re.search(pattern2, text, re.IGNORECASE)
    if match:
        month = months_spanish[match.group(1).lower()]
        day = int(match.group(2))
        year = int(match.group(3))
        date_value = _normalize_date_parts(year, month, day)
        if date_value:
            print(f"   📅 Fecha detectada (formato texto): '{match.group(0)}' → {date_value}")
            return f"{date_field} = '{date_value}'"

    # Patrón 3: YYYY-MM-DD o YYYY/MM/DD (ej: 1958-01-15)
    match = re.search(r"\b(19\d{2}|20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b", text)
    if match:
        date_value = match.group(0).replace("/", "-")
        print(f"   📅 Fecha detectada (formato numérico YYYY-MM-DD): {date_value}")
        return f"{date_field} = '{date_value}'"

    # Patrón 4: DD-MM-YYYY o DD/MM/YYYY (ej: 15-01-1958)
    match = re.search(r"\b(0[1-9]|[12]\d|3[01])[-/](0[1-9]|1[0-2])[-/](19\d{2}|20\d{2})\b", text)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))
        date_value = _normalize_date_parts(year, month, day)
        if date_value:
            print(f"   📅 Fecha detectada (formato numérico DD-MM-YYYY): '{match.group(0)}' → {date_value}")
            return f"{date_field} = '{date_value}'"
    
    # Patrón 5: Solo año (ej: 1958)
    match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    if match:
        year = match.group(1)
        # Para un año, buscar cualquier fecha en ese año
        # Nota: esto podría no coincidir exactamente, mejor no hacer filtro
        # skip this case
        pass

    return ""


def _remove_date_from_query(query: str) -> str:
    """
    Removes date information from query text to get only the search terms.
    Useful for separating date filters from fulltext search terms.
    
    Args:
        query: Original query text
        
    Returns:
        Query without date information
    """
    if not query:
        return query
    
    text = query.strip()
    months_spanish = {
        'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
        'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'
    }
    
    # Remove pattern: "DD de MES de YYYY" or "DD de MES YYYY"
    pattern1 = r"\b(0[1-9]|[12]\d|3[01])\s+de\s+(" + "|".join(months_spanish) + r")\s+(?:de\s+)?(19\d{2}|20\d{2})\b"
    text = re.sub(pattern1, "", text, flags=re.IGNORECASE)
    
    # Remove pattern: "MES DD de YYYY" or "MES DD, YYYY"
    pattern2 = r"\b(" + "|".join(months_spanish) + r")\s+(0[1-9]|[12]\d|3[01])(?:\s+de|\s*,)?\s+(19\d{2}|20\d{2})\b"
    text = re.sub(pattern2, "", text, flags=re.IGNORECASE)
    
    # Remove YYYY-MM-DD or YYYY/MM/DD
    text = re.sub(r"\b(19\d{2}|20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b", "", text)
    
    # Remove DD-MM-YYYY or DD/MM/YYYY
    text = re.sub(r"\b(0[1-9]|[12]\d|3[01])[-/](0[1-9]|1[0-2])[-/](19\d{2}|20\d{2})\b", "", text)
    
    # Clean up extra spaces
    text = re.sub(r"\s+", " ", text).strip()
    
    return text


def _remove_fulltext_search_from_sql(sql_query: str) -> str:
    """
    Elimina todas las condiciones MATCH y KNN_MATCH de una query SQL.
    Limpia también conectores OR/AND y paréntesis que quedan vacíos.
    
    Args:
        sql_query: Query SQL que puede contener MATCH/KNN_MATCH
        
    Returns:
        Query SQL sin condiciones de búsqueda fulltext
    """
    original_query = sql_query
    
    # Remover condiciones MATCH(...) de forma recursiva
    iterations = 0
    while re.search(r'\bMATCH\s*\([^)]*\)', sql_query, flags=re.IGNORECASE) and iterations < 10:
        sql_query = re.sub(r'\bMATCH\s*\([^)]*\)', '', sql_query, flags=re.IGNORECASE)
        iterations += 1
    
    # Remover condiciones KNN_MATCH(...) de forma recursiva
    iterations = 0
    while re.search(r'\bKNN_MATCH\s*\([^)]*\)', sql_query, flags=re.IGNORECASE) and iterations < 10:
        sql_query = re.sub(r'\bKNN_MATCH\s*\([^)]*\)', '', sql_query, flags=re.IGNORECASE)
        iterations += 1
    
    # Limpiar conectores múltiples (repetir varias veces para casos anidados)
    for _ in range(3):
        sql_query = re.sub(r'\s+(OR|AND)\s+(OR|AND)\s+', r' \1 ', sql_query, flags=re.IGNORECASE)
        sql_query = re.sub(r'\s+OR\s+OR\s+', ' OR ', sql_query, flags=re.IGNORECASE)
        sql_query = re.sub(r'\s+AND\s+AND\s+', ' AND ', sql_query, flags=re.IGNORECASE)
    
    # Limpiar paréntesis vacíos (repetir varias veces para anidados)
    for _ in range(5):
        sql_query = re.sub(r'\(\s*\)', '', sql_query)
        sql_query = re.sub(r'\(\s*(OR|AND)\s*\)', '', sql_query, flags=re.IGNORECASE)
        sql_query = re.sub(r'\(\s+(OR|AND)\s+', '(', sql_query, flags=re.IGNORECASE)
        sql_query = re.sub(r'\s+(OR|AND)\s+\)', ')', sql_query, flags=re.IGNORECASE)
    
    # Limpiar WHERE seguido directamente de OR/AND
    sql_query = re.sub(r'\bWHERE\s+(OR|AND)\s+', 'WHERE ', sql_query, flags=re.IGNORECASE)
    
    # Limpiar OR/AND antes de ORDER/GROUP/LIMIT/FROM
    sql_query = re.sub(r'\s+(OR|AND)\s+(ORDER|GROUP|LIMIT|FROM|$)', r' \2', sql_query, flags=re.IGNORECASE)
    
    # Limpiar WHERE vacío o con solo espacios antes de ORDER/GROUP/LIMIT
    sql_query = re.sub(r'\bWHERE\s+(ORDER|GROUP|LIMIT|FROM)', r'\1', sql_query, flags=re.IGNORECASE)
    
    # Limpiar WHERE al final de línea (sin nada después)
    sql_query = re.sub(r'\bWHERE\s*\n', '\n', sql_query, flags=re.IGNORECASE)
    sql_query = re.sub(r'\bWHERE\s*$', '', sql_query, flags=re.IGNORECASE)
    
    # Limpiar múltiples espacios y saltos de línea
    sql_query = re.sub(r'\s+', ' ', sql_query).strip()
    sql_query = re.sub(r'\s*\n\s*', '\n', sql_query)
    
    # Si la query cambió, registrar transformación
    if sql_query != original_query:
        print(f"      [_remove_fulltext_search_from_sql] Transformación aplicada:")
        print(f"      Antes:  {original_query[:150]}...")
        print(f"      Después: {sql_query[:150]}...")
    
    return sql_query


def _is_generic_date_query(search_query: str) -> bool:
    """
    Usa el LLM para determinar si la búsqueda es genérica (solo pide programación de un día)
    o específica (busca un programa/contenido en particular).
    Con fallback inteligente basado en heurísticas si el LLM no responde correctamente.
    Usa caché para evitar llamadas redundantes al LLM.
    
    Args:
        search_query: Query de búsqueda sin información de fecha
        
    Returns:
        True si es búsqueda genérica, False si es específica
    """
    global _GENERIC_SEARCH_CACHE
    
    if not search_query or not search_query.strip():
        return True
    
    # Normalizar búsqueda para caché
    cache_key = search_query.strip().lower()
    
    # Verificar caché
    if cache_key in _GENERIC_SEARCH_CACHE:
        result = _GENERIC_SEARCH_CACHE[cache_key]
        print(f"   [_is_generic_date_query] ⚡ Resultado en caché: {'🟢 GENÉRICA' if result else '🔵 ESPECÍFICA'}")
        return result
    
    print(f"   [_is_generic_date_query] Consultando al LLM para clasificar: '{search_query}'")
    
    # Prompt para el LLM - muy simple y claro
    classification_prompt = f"""
    Te voy a consultar una búsqueda relacionada con programación de televisión. Necesito que me ayudes a determinar si la consulta es GENÉRICA (solo pide información general sobre la programación o emisiones de un día) o ESPECÍFICA (busca algo concreto como un programa, película, serie, actor, etc.).
    Clasifica la consulta en dos categorías: GENÉRICA o ESPECÍFICA:

    CONSULTA: "{search_query}"

    GENÉRICA = solo pide programación/emisiones (TVE, programas del día, emisiones)
    ESPECÍFICA = busca algo concreto (un programa, película, serie, actor, nombre específico)

    RESPONDE SOLO LA PALABRA:
    GENERICA o ESPECIFICA"""

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_LLM_MODEL,
                "prompt": classification_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 1500,  # Aumentado para asegurar respuesta
                    "top_p": 0.1  # Más restrictivo para mejor consistencia
                }
            },
            timeout=20
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"   [_is_generic_date_query] Respuesta LLM thinking: {result.get('thinking', '')}")
            classification = result.get("response", "").strip().upper()
            
            print(f"   [_is_generic_date_query] Respuesta LLM: '{classification[:100]}'")
            
            # Extraer solo GENERICA o ESPECIFICA de la respuesta
            if "GENERICA" in classification or "GENÉRICA" in classification:
                print(f"   [_is_generic_date_query] → LLM clasificó como: 🟢 GENÉRICA")
                _GENERIC_SEARCH_CACHE[cache_key] = True
                return True
            elif "ESPECIFICA" in classification or "ESPECÍFICA" in classification:
                print(f"   [_is_generic_date_query] → LLM clasificó como: 🔵 ESPECÍFICA")
                _GENERIC_SEARCH_CACHE[cache_key] = False
                return False
            else:
                # Respuesta vacía o ambigua - usar heurístico
                print(f"   [_is_generic_date_query] ⚠️ Respuesta del LLM no contiene clasificación, usando heurístico")
                result = _is_generic_date_query_heuristic(search_query)
                _GENERIC_SEARCH_CACHE[cache_key] = result
                return result
        else:
            print(f"   [_is_generic_date_query] ⚠️ Error LLM ({response.status_code}), usando heurístico")
            result = _is_generic_date_query_heuristic(search_query)
            _GENERIC_SEARCH_CACHE[cache_key] = result
            return result
            
    except Exception as e:
        print(f"   [_is_generic_date_query] ⚠️ Error: {str(e)}, usando heurístico")
        result = _is_generic_date_query_heuristic(search_query)
        _GENERIC_SEARCH_CACHE[cache_key] = result
        return result


def _is_generic_date_query_heuristic(search_query: str) -> bool:
    """
    Heurístico de fallback para clasificar si la búsqueda es genérica o específica.
    Se usa cuando el LLM no puede responder.
    
    Args:
        search_query: Query de búsqueda
        
    Returns:
        True si es búsqueda genérica, False si es específica
    """
    if not search_query:
        return True
    
    query_lower = search_query.lower()
    
    # Palabras que indican búsqueda GENÉRICA (solo programación/horario)
    generic_keywords = [
        "programación", "programacion",
        "programas", "emisiones", "emitido",
        "horario", "que hay", "qué hay",
        "parrilla", "schedule",
        "del día", "de hoy",
        "qué se emitió", "que se emitio",
        "tv", "tve", "television", "televisión",
        "reemitido", "reemision", "reemisión"
    ]
    
    # Palabras que indican búsqueda ESPECÍFICA (contenido/programa concreto)
    specific_keywords = [
        "bonanza", "mash", "los simpsons", "buffy",
        "telediario", "los viernes al cine",
        "hitchcock", "spielberg", "director",
        "película", "pelicula", "film", "cine",
        "serie", "western", "drama", "comedia",
        "revista literaria", "documental",
        "concierto", "conciertos",
        "anuncio", "anuncios", "publicidad",
        "actriz", "actor", "cantante", "músico", "musico",
        "entrevista", "entrevistas",
        "reportaje", "reportajes"
    ]
    
    # Contar coincidencias
    generic_score = sum(1 for kw in generic_keywords if kw in query_lower)
    specific_score = sum(1 for kw in specific_keywords if kw in query_lower)
    
    print(f"   [_is_generic_date_query_heuristic] generic_score={generic_score}, specific_score={specific_score}")
    
    # Si hay coincidencias específicas, es específica
    if specific_score > generic_score:
        print(f"   [_is_generic_date_query] → Heurístico clasificó como: 🔵 ESPECÍFICA")
        return False
    
    # Si hay coincidencias genéricas, es genérica
    if generic_score > 0:
        print(f"   [_is_generic_date_query] → Heurístico clasificó como: 🟢 GENÉRICA")
        return True
    
    # Por defecto, si tiene pocas palabras es genérica
    word_count = len(query_lower.split())
    if word_count <= 3:
        print(f"   [_is_generic_date_query] → Heurístico (corta) clasificó como: 🟢 GENÉRICA")
        return True
    
    # Por defecto general: ESPECÍFICA (busca algo concreto)
    print(f"   [_is_generic_date_query] → Heurístico (default) clasificó como: 🔵 ESPECÍFICA")
    return False


def normalize_dates_in_sql(sql: str) -> str:
    """Normaliza fechas en SQL a formato YYYY-MM-DD."""
    if not sql:
        return sql

    def _replace_ddmmyyyy(match):
        day, month, year = match.group(1), match.group(2), match.group(3)
        return f"{year}-{month}-{day}"

    def _replace_yyyymmdd(match):
        year, month, day = match.group(1), match.group(2), match.group(3)
        return f"{year}-{month}-{day}"

    sql = re.sub(r"\b(0[1-9]|[12]\d|3[01])[-/](0[1-9]|1[0-2])[-/](19\d{2}|20\d{2})\b", _replace_ddmmyyyy, sql)
    sql = re.sub(r"\b(19\d{2}|20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b", _replace_yyyymmdd, sql)
    return sql


EXCLUDE_FIELDS_FOR_LLM = {
    # IDs y referencias técnicas
    "id", "_id", "doc_id", "document_id", "magazine_id", "page_number", "media_id",

    # Embeddings y vectores (campos numéricos)
    "content_embedding", "embedding", "text_embedding", "embeddings",

    # Scores numéricos
    "_score", "relevance_score", "score", "bm25_score", "vector_score",

    # Metadatos técnicos
    "table_source", "_version", "version", "checksum", "hash",

    # Campos no-texto específicos (date y time se incluyen ahora)
    "channel", "station", "advertiser",
    "section_title", "caption_literal", "duration", "page_count"
}


def _extract_text_fields(result: Dict) -> Dict[str, Any]:
    """
    Extrae SOLO campos de texto de un resultado para enviar al LLM.
    NUNCA incluye: números, embeddings, arrays, timestamps, IDs técnicos, etc.
    
    Args:
        result: Diccionario con los campos del resultado
        
    Returns:
        Diccionario con SOLO campos de texto relevantes (strings no vacíos)
    """
    text_result = {}
    
    for field, value in result.items():
        # ✅ Solo incluir si:
        # 1. El campo NO está en la lista de exclusión
        # 2. El valor ES una cadena (string)
        # 3. La cadena NO está vacía
        # 4. NO es un campo numérico (list, dict, int, float, bool)
        
        if field not in EXCLUDE_FIELDS_FOR_LLM:
            # Rechazar cualquier tipo que NO sea string
            if isinstance(value, str) and value.strip():
                text_result[field] = value
            # Doble verificación: rechazar lists (que podrían contener embeddings)
            elif isinstance(value, (list, dict, int, float, bool)):
                # Ignorar silenciosamente campos numéricos/estructurados
                continue
    
    return text_result


def _extract_summary_fields(result: Dict) -> Dict[str, Any]:
    """
    Extrae campos numericos o mixtos para resultados agregados.
    Convierte valores a string para el prompt del LLM.
    """
    summary_result = {}

    for field, value in result.items():
        if field in EXCLUDE_FIELDS_FOR_LLM:
            continue

        if isinstance(value, (list, dict)) or value is None:
            continue

        if isinstance(value, (str, int, float, bool)):
            value_str = str(value).strip()
            if value_str:
                summary_result[field] = value_str

    return summary_result


def _prepare_results_for_llm(search_results: List[Dict]) -> List[Dict]:
    """
    Prepara los resultados de búsqueda para enviar al LLM,
    extrayendo solo campos de texto relevantes.
    
    Args:
        search_results: Lista de resultados de búsqueda
        
    Returns:
        Lista de resultados con solo campos de texto
    """
    prepared_results = []
    
    for result in search_results:
        text_fields = _extract_text_fields(result)
        
        if text_fields:  # Solo agregar si hay campos de texto
            prepared_results.append(text_fields)
        else:
            summary_fields = _extract_summary_fields(result)
            if summary_fields:
                prepared_results.append(summary_fields)
    
    return prepared_results


def _extract_sql_tables(sql_query: str) -> List[str]:
    """Extrae nombres de tablas desde FROM y JOIN en una consulta SQL."""
    if not sql_query:
        return []

    tables = []
    sql_upper = sql_query.upper()
    
    # Buscar en FROM
    from_pattern = r"FROM\s+([a-zA-Z0-9_\.]+)(?:\s+(?:AS\s+)?([a-zA-Z0-9_]+))?(?:\s|,|WHERE|JOIN|INNER|LEFT|RIGHT|ON|$)"
    for match in re.finditer(from_pattern, sql_query, flags=re.IGNORECASE):
        table_name = match.group(1).split(".")[-1]  # Quitar schema si existe
        tables.append(table_name)
    
    # Buscar en JOIN
    join_pattern = r"(?:INNER\s+|LEFT\s+|RIGHT\s+|FULL\s+)?JOIN\s+([a-zA-Z0-9_\.]+)(?:\s+(?:AS\s+)?([a-zA-Z0-9_]+))?"
    for match in re.finditer(join_pattern, sql_query, flags=re.IGNORECASE):
        table_name = match.group(1).split(".")[-1]  # Quitar schema si existe
        tables.append(table_name)
    
    # Limpiar duplicados e ignorar alias comunes
    tables = list(dict.fromkeys(tables))  # Mantiene orden sin duplicados
    
    return tables


def get_query_embedding(query_text: str) -> Optional[List[float]]:
    """
    Genera el embedding de una consulta usando Ollama
    
    Args:
        query_text: Texto de la consulta
        
    Returns:
        Lista de floats representando el embedding o None si hay error
    """
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={
                "model": OLLAMA_EMBEDDING_MODEL,
                "prompt": query_text
            },
            timeout=30
        )
        if response.status_code == 200:
            return response.json().get("embedding")
        else:
            print(f"⚠️ No se pudo generar embedding: {response.text}")
            return None
    except Exception as e:
        print(f"⚠️ Error generando embedding: {str(e)}")
        return None


def get_clip_text_embedding(query_text: str) -> Optional[List[float]]:
    """
    Genera embedding CLIP para una descripción textual usando VIVOClient.
    """
    if not VIVOEMBCLIENT_AVAILABLE or VIVO_CLIENT is None:
        print("⚠️ VIVOClient no disponible para embeddings de texto")
        return None
    try:
        text = query_text.strip()
        if not text:
            return None
        embedding_model = os.getenv("IMAGE_TEXT_EMBEDDING_MODEL", "clip")
        result = VIVO_CLIENT.embed(text, model=embedding_model)
        if not result or not isinstance(result, list) or len(result) == 0:
            print("⚠️ Respuesta vacía de VIVOClient para texto")
            return None
        first_result = result[0]
        if not isinstance(first_result, dict):
            print("⚠️ Formato de respuesta inesperado en VIVOClient")
            return None
        embedding = first_result.get("embedding")
        return embedding
    except Exception as e:
        print(f"⚠️ Error generando embedding CLIP de texto: {str(e)}")
        return None


def execute_cratedb_query(query: str) -> Optional[Dict]:
    """
    Ejecuta una consulta contra CrateDB
    
    Args:
        query: Query SQL a ejecutar
        
    Returns:
        Diccionario con resultados o None si hay error
    """
    try:
        response = requests.post(
            CRATEDB_URL,
            json={"stmt": query},
            auth=HTTPBasicAuth(CRATEDB_USERNAME, CRATEDB_PASSWORD),
            timeout=60
        )
        if response.status_code != 200:
            print(f"❌ Error ejecutando query: {response.text}")
            return None
        return response.json()
    except Exception as e:
        print(f"❌ Error de conexión: {str(e)}")
        return None


@tool
def image_text_search(query: str, limit: int = None) -> str:
    """
    Busca imágenes similares a una descripción textual usando embeddings CLIP.
    
    Args:
        query: Descripción textual de las imágenes a buscar
        limit: (IGNORADO) Se usa siempre el límite configurado globalmente (60).
    
    Returns:
        JSON con resultados de búsqueda
    """
    print("\n" + "-"*80)
    print("🖼️ IMAGE TEXT SEARCH TOOL - INICIO")
    print("-"*80)
    print(f"📝 Descripción: {query}")
    
    # SIEMPRE usar el límite configurado globalmente, ignorar el parámetro limit
    limit = get_sql_results_limit()
    print(f"📊 Límite (usando configurado): {limit}")

    # Asegurar límites razonables
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    embedding = get_clip_text_embedding(query)
    if not embedding:
        return json.dumps({
            "success": False,
            "error": "No se pudo generar embedding CLIP para la descripción",
            "results": []
        })

    embedding_str = str(embedding)
    table_name = f"{COLLECTION_NAME}_image_embeddings"

    sql_query = f"""
    SELECT
        id,
        magazine_id,
        page_number,
        src,
        bbox,
        description,
        caption_literal,
        _score as similarity
    FROM {table_name}
    WHERE KNN_MATCH(image_embedding, {embedding_str}, {limit})
    OR MATCH(description, '{query}')
    OR MATCH(caption_literal, '{query}')
    ORDER BY _score DESC
    LIMIT {limit}
    """

    add_executed_sql_query(table_name, sql_query)
    response = execute_cratedb_query(sql_query)

    if not response:
        return json.dumps({
            "success": False,
            "error": "Error ejecutando consulta de imágenes",
            "results": []
        })

    rows = response.get("rows", [])
    cols = response.get("cols", [])
    results = []

    for row in rows:
        row_dict = dict(zip(cols, row))
        src_path = row_dict.get("src", "")
        if src_path:
            if "pngprocessed/" in src_path:
                relative_path = src_path.split("pngprocessed/", 1)[1]
            else:
                relative_path = src_path
            row_dict["png_url"] = f"{PNG_BASE_URL}/{relative_path}"
            row_dict["image_path"] = relative_path

        if not row_dict.get("title"):
            row_dict["title"] = row_dict.get("description") or row_dict.get("caption_literal") or "Imagen"

        row_dict["table_source"] = "image_embeddings"
        results.append(row_dict)

    return json.dumps({
        "success": True,
        "results": results,
        "num_results": len(results)
    })


def _add_document_urls_to_results(results: List[Dict]) -> List[Dict]:
    """
    Agrega URLs de documentos PNG a los resultados de búsqueda basándose en magazine_id y page_number.
    
    Args:
        results: Lista de resultados de búsqueda
        
    Returns:
        Lista de resultados con URLs agregadas
    """
    def _extract_exemplar_id(raw_magazine_id: object) -> Optional[str]:
        if raw_magazine_id is None:
            return None
        magazine_id_str = str(raw_magazine_id).strip()
        if not magazine_id_str:
            return None
        # Preferir patrón ejemplar_### dentro del magazine_id
        match = re.search(r"ejemplar_(\d+)", magazine_id_str, re.IGNORECASE)
        if match:
            return match.group(1)
        # Si ya es numérico, usarlo directamente
        if magazine_id_str.isdigit():
            return magazine_id_str
        return None

    for result in results:
        # Si ya tiene png_url (de image_embeddings o src), no modificar
        if result.get("png_url") or result.get("src"):
            continue
        
        # Si tiene magazine_id y page_number, construir URL
        magazine_id = result.get("magazine_id")
        page_number = result.get("page_number")
        exemplar_id = _extract_exemplar_id(magazine_id)

        if exemplar_id and page_number is not None:
            # Formato: /ejemplar_{magazine_id}/ejemplar_{magazine_id}_pagina_{page_number}.png
            relative_path = f"ejemplar_{exemplar_id}/ejemplar_{exemplar_id}_pagina_{page_number}.png"
            result["png_url"] = f"{PNG_BASE_URL}/{relative_path}"
            result["document_path"] = relative_path
    
    return results


def _get_result_text_for_reranking(result: Dict) -> str:
    """Extrae y combina campos de texto para el reranker."""
    parts = []

    # Incluir canal y sección si están disponibles, ya que pueden ser relevantes para el contexto
    if result.get("channel"):
        parts.append(str(result["channel"]))
    if result.get("section_title"):
        parts.append(str(result["section_title"]))  
    # Incluir fecha si está disponible, ya que es un factor importante para la relevancia en este dominio
    if result.get("date"):
        parts.append(str(result["date"]))
    # programacion tv
    if result.get("block_name"):
        parts.append(str(result["block_name"]))
    # Prioridad a título y descripción
    if result.get("title"):
        parts.append(str(result["title"]))
    if result.get("main_title"):
        parts.append(str(result["main_title"]))
    
    # Contenido principal
    if result.get("content_description"):
        parts.append(str(result["content_description"]))
    if result.get("brief_summary"):
        parts.append(str(result["brief_summary"]))
    if result.get("ad_copy"):
        parts.append(str(result["ad_copy"]))
    if result.get("caption_literal"):
        parts.append(str(result["caption_literal"]))
    if result.get("description"):
        parts.append(str(result["description"]))   
    if result.get("section_title"):
        parts.append(str(result["section_title"]))
    if result.get("full_text"):
        parts.append(str(result["full_text"]))
    
        
    return " ".join(parts)


def _rerank_results(query: str, results: List[Dict], limit: int) -> tuple:
    """
    Reordena los resultados usando el reranker de VIVO en lotes para no saturar la GPU.
    
    Returns:
        tuple: (new_results, reranking_time)
    """
    if not results or not VIVO_CLIENT:
        return results, 0.0

    print(f"   🔄 Ejecutando Reranking para {len(results)} documentos...")
    start_rerank = time.time()

    try:
        documents = [_get_result_text_for_reranking(r) for r in results]
        reranker_model = os.getenv("RERANKER_MODEL", "qwen3rerank")

        # Procesamiento por lotes para no saturar la GPU
        batch_size =5
        all_reranked_items = []

        print(f"   📦 Procesando en lotes de {batch_size} documentos...")

        for i in range(0, len(documents), batch_size):
            chunk_documents = documents[i:i + batch_size]
            num_lotes = (len(documents) + batch_size - 1) // batch_size
            
            print(f"      - Lote {i//batch_size + 1}/{num_lotes}: procesando documentos {i} a {i + len(chunk_documents) - 1}")

            try:
                if hasattr(VIVO_CLIENT, 'rerank'):
                    reranked_chunk = VIVO_CLIENT.rerank(
                        query=query, 
                        documents=chunk_documents, 
                        model=reranker_model
                    )
                    for item in reranked_chunk:
                        original_index = i + item['index']
                        all_reranked_items.append({'original_index': original_index, 'score': item['score']})

                elif hasattr(VIVO_CLIENT, 'base_url'):
                    # Fallback a llamada HTTP
                    response = requests.post(
                        f"{VIVO_CLIENT.base_url}/rerank",
                        json={
                            "query": query, "documents": chunk_documents,
                            "top_k": len(chunk_documents), "model": reranker_model
                        },
                        timeout=15
                    )
                    if response.status_code == 200:
                        for item in response.json().get('results', []):
                            original_index = i + item['index']
                            all_reranked_items.append({'original_index': original_index, 'score': item['score']})
                    else:
                        raise Exception(f"HTTP Error {response.status_code}")

            except Exception as batch_error:
                print(f"   ⚠️ Error procesando lote {i//batch_size + 1}: {batch_error}. Se asignará score alto a este lote.")
                for j in range(len(chunk_documents)):
                    all_reranked_items.append({'original_index': i + j, 'score': 0.9})

        if not all_reranked_items:
             print("   ⚠️ No se obtuvieron resultados del reranking.")
             return results

        # Ordenar todos los items de todos los lotes por su nuevo score
        all_reranked_items.sort(key=lambda x: x['score'], reverse=True)

        # Construir la lista final de resultados en el nuevo orden
        new_results = []
        print(f"\n   📊 Reranking Analysis (Top {min(len(all_reranked_items), 10)}):")
        print(f"   {'Orig':<5} {'New':<5} {'Diff':<5} {'Score':<8} {'Title'}")
        print(f"   {'-'*5} {'-'*5} {'-'*5} {'-'*8} {'-'*40}")

        for i, item in enumerate(all_reranked_items):
            idx = item['original_index']
            score = item['score']
            
            res = results[idx]
            res['relevance_score'] = score
            res['rerank_score'] = score
            new_results.append(res)

            if i < 10:
                orig_rank = idx + 1
                new_rank = i + 1
                diff = orig_rank - new_rank
                diff_str = f"{diff:+d}"
                title = str(res.get('title', 'No title'))
                if len(title) > 40:
                    title = title[:37] + "..."
                print(f"   {orig_rank:<5} {new_rank:<5} {diff_str:<5} {score:.4f}   {title}")

        print(f"   ✅ Reranking por lotes completado en {time.time() - start_rerank:.2f}s")
        reranking_time = time.time() - start_rerank
        return new_results, reranking_time

    except Exception as e:
        print(f"   ⚠️ Error en reranking: {e}")
        import traceback
        traceback.print_exc()

    # Si algo falla, devolver resultados originales
    reranking_time = time.time() - start_rerank
    return results, reranking_time


@tool
def hybrid_search(
    query: str,
    limit: int = 20,
    table_names: Optional[List[str]] = None,
    search_type: str = "hybrid"
) -> str:
    """
    Herramienta de búsqueda híbrida que combina BM25 y búsqueda vectorial.
    Busca en múltiples tablas de la base de datos de TeleRadio y combina resultados.
    
    Args:
        query: Consulta del usuario
        limit: Número máximo de resultados total (default: 20)
        table_names: Lista de nombres de tablas a consultar (default: se clasifican automáticamente)
        search_type: Tipo de búsqueda ("hybrid", "bm25", "vector")
        
    Returns:
        String JSON con resultados y metadatos
    """
    # Capturar tiempo de inicio
    search_start_time = time.time()
    
    print("\n" + "-"*80)
    print("🔍 HYBRID SEARCH TOOL - INICIO")
    print("-"*80)
    print(f"📝 Búsqueda: {query}")
    print(f"🔎 Tipo: {search_type}")

    configured_limit = get_sql_results_limit()
    if configured_limit:
        if limit != configured_limit:
            print(f"📊 Límite solicitado: {limit} → Usando configurado: {configured_limit}")
        limit = configured_limit
    else:
        print(f"📊 Límite de resultados: {limit}")
    
    print()
    
    # Limpiar queries anteriores
    clear_executed_sql_queries()
    
    # Si no se especifican tablas, clasificar automáticamente
    if not table_names:
        table_names = classify_query_tables(query)
    
    # Deduplicar table_names manteniendo el orden
    seen = set()
    unique_table_names = []
    for table in table_names:
        if table not in seen:
            seen.add(table)
            unique_table_names.append(table)
    
    if len(unique_table_names) < len(table_names):
        print(f"⚠️ Tablas duplicadas detectadas y eliminadas: {len(table_names)} → {len(unique_table_names)}")
    
    table_names = unique_table_names
    print(f"📋 Tablas a consultar: {', '.join(table_names)}\n")
    
    # Validar que las tablas existan en la configuración
    valid_tables = SEARCH_CONFIG["tables"].keys()
    table_names = [t for t in table_names if t in valid_tables]
    
    if not table_names:
        table_names = [SEARCH_CONFIG["default_table"]]
    
    all_results = []
    executed_queries = []
    search_classification = "unknown"  # 🆕 Inicializar por defecto
    
    # Buscar en cada tabla
    for table_name in table_names:
        table_config = SEARCH_CONFIG["tables"][table_name]
        search_fields = table_config["search_fields"]
        embedding_field = table_config.get("embedding_field", SEARCH_CONFIG["embedding_field"])
        date_field = table_config.get("date_field")
        
        # Extraer fecha y limpiar query para búsqueda de texto
        date_filter_sql = _extract_date_filter(query, date_field) if date_field else ""
        search_query = _remove_date_from_query(query)
        
        print(f"   📝 Query original: '{query}'")
        if date_filter_sql:
            print(f"   ✅ Filtro de fecha extraído: {date_filter_sql}")
        if search_query:
            print(f"   🔍 Términos de búsqueda (después de quitar fecha): '{search_query}'")
        else:
            print(f"   ℹ️  Sin términos de búsqueda (solo fecha)")
        print()
        
        # Determinar si es búsqueda genérica o específica
        print(f"   🔎 CLASIFICANDO TIPO DE BÚSQUEDA:")
        is_generic = _is_generic_date_query(search_query)
        search_classification = 'generic' if is_generic else 'specific'
        
        if search_query:
            if is_generic:
                print(f"   ✅ Resultado: 🟢 BÚSQUEDA GENÉRICA")
                print(f"      → Solo se aplicarán filtros de fecha/canal")
                print(f"      → NO se usará MATCH fulltext")
            else:
                print(f"   ✅ Resultado: 🔵 BÚSQUEDA ESPECÍFICA")
                print(f"      → Se aplicará MATCH fulltext en campos de texto")
        print()
        
        # Si es búsqueda genérica con fecha, no hacer MATCH
        if is_generic and date_filter_sql:
            search_query = ""  # Forzar a no usar MATCH
        
        # Campos base a seleccionar (siempre disponibles)
        base_fields = "id, magazine_id, page_number, title"
        # Añadir campos según tabla - NORMALIZADO para compatibilidad UNION
        # Todas las tablas devuelven las mismas columnas (con NULL/'' donde no apliquen)
        if table_name == "teleradio_content_editorial":
            select_fields = "id, magazine_id, page_number, publication_date as date, main_title as title, full_text, brief_summary, '' as content_description, '' as channel, '' as station, '' as time, '' as advertiser"
        elif table_name == "teleradio_content_tv_schedule":
            select_fields = "id, magazine_id, page_number, date, title, '' as full_text, '' as brief_summary, content_description, channel, '' as station, time, '' as advertiser, day_of_week"
        elif table_name == "teleradio_content_radio_schedule":
            select_fields = "id, magazine_id, page_number, date, title, '' as full_text, '' as brief_summary, content_description, '' as channel, station, time, '' as advertiser, day_of_week"
        elif table_name == "teleradio_content_advertising":
            select_fields = "id, magazine_id, page_number, publication_date as date, '' as title, '' as full_text, '' as brief_summary, ad_copy as content_description, '' as channel, '' as station, '' as time, advertiser"
        elif table_name == "teleradio_content_image_embeddings":
            select_fields = "id, magazine_id, page_number, CURRENT_TIMESTAMP as date, description as title, '' as full_text, description as brief_summary, '' as content_description, '' as channel, '' as station, '' as time, '' as advertiser"
        else:  # teleradio_content_others
            select_fields = "id, magazine_id, page_number, publication_date as date, title, content as full_text, description as brief_summary, '' as content_description, '' as channel, '' as station, '' as time, '' as advertiser"
        
        # Generar query SQL según tipo de búsqueda
        if search_type == "bm25":
            # Solo búsqueda de texto con BM25
            if search_query.strip():
                match_conditions = " OR ".join([f"MATCH({field}, '{search_query}')" for field in search_fields])
                where_clause = match_conditions
                if date_filter_sql:
                    where_clause = f"({where_clause}) AND ({date_filter_sql})"
            else:
                # Solo búsqueda por fecha
                where_clause = date_filter_sql if date_filter_sql else "1=1"
            
            sql_query = f"""
            SELECT {select_fields},
                   _score as relevance_score, '{table_config['name']}' as table_source
            FROM {table_name}
            WHERE {where_clause}
            ORDER BY _score DESC
            LIMIT {limit}
            """
        elif search_type == "vector":
            # Solo búsqueda vectorial
            if not embedding_field:
                if search_query.strip():
                    match_conditions = " OR ".join([f"MATCH({field}, '{search_query}')" for field in search_fields])
                    where_clause = match_conditions
                    if date_filter_sql:
                        where_clause = f"({where_clause}) AND ({date_filter_sql})"
                else:
                    where_clause = date_filter_sql if date_filter_sql else "1=1"

                sql_query = f"""
                SELECT {select_fields},
                       _score as relevance_score, '{table_config['name']}' as table_source
                FROM {table_name}
                WHERE {where_clause}
                ORDER BY _score DESC
                LIMIT {limit}
                """
            else:
                embedding = get_query_embedding(search_query)
                if not embedding and search_query.strip():
                    return json.dumps({
                        "success": False,
                        "error": "No se pudo generar el embedding para búsqueda vectorial",
                        "results": []
                    })

                if embedding:
                    embedding_str = str(embedding)
                    where_clause = f"KNN_MATCH({embedding_field}, {embedding_str}, {limit})"
                    if date_filter_sql:
                        where_clause = f"({where_clause}) AND ({date_filter_sql})"
                else:
                    # Sin search_query, solo filtro de fecha
                    where_clause = date_filter_sql if date_filter_sql else "1=1"

                sql_query = f"""
                SELECT {select_fields},
                       _score as relevance_score, '{table_config['name']}' as table_source
                FROM {table_name}
                WHERE {where_clause}
                ORDER BY _score DESC
                LIMIT {limit}
                """
        else:  # hybrid (default)
            # Búsqueda híbrida: BM25 + KNN
            if not embedding_field:
                search_type = "bm25"
            if search_query.strip() and search_type == "hybrid":
                embedding = get_query_embedding(search_query)
            else:
                embedding = None
            
            if not embedding and search_query.strip():
                # Fallback a solo BM25
                match_conditions = " OR ".join([f"MATCH({field}, '{search_query}')" for field in search_fields])
                where_clause = match_conditions
                if date_filter_sql:
                    where_clause = f"({where_clause}) AND ({date_filter_sql})"
                sql_query = f"""
                SELECT {select_fields},
                       _score as relevance_score, '{table_config['name']}' as table_source
                FROM {table_name}
                WHERE {where_clause}
                ORDER BY _score DESC
                LIMIT {limit}
                """
            elif embedding and search_query.strip():
                embedding_str = str(embedding)
                match_conditions = " OR ".join([f"MATCH({field}, '{search_query}')" for field in search_fields])
                where_clause = f"{match_conditions} OR KNN_MATCH({embedding_field}, {embedding_str}, 20)"
                if date_filter_sql:
                    where_clause = f"({where_clause}) AND ({date_filter_sql})"
                sql_query = f"""
                SELECT {select_fields},
                       _score as relevance_score, '{table_config['name']}' as table_source
                FROM {table_name}
                WHERE {where_clause}
                ORDER BY _score DESC
                LIMIT {limit}
                """
            else:
                # Sin search_query, solo filtro de fecha
                where_clause = date_filter_sql if date_filter_sql else "1=1"
                sql_query = f"""
                SELECT {select_fields},
                       _score as relevance_score, '{table_config['name']}' as table_source
                FROM {table_name}
                WHERE {where_clause}
                ORDER BY _score DESC
                LIMIT {limit}
                """
        
        # Ejecutar query
        result = execute_cratedb_query(sql_query)
        executed_queries.append({"table": table_config['name'], "sql": sql_query})
        
        if result:
            # Formatear resultados
            cols = result.get("cols", [])
            rows = result.get("rows", [])
            
            # Procesar lote de esta tabla para aplicar RRF
            # Los resultados ya vienen ordenados por _score DESC desde la base de datos
            current_table_results = []
            
            for row in rows:
                result_dict = dict(zip(cols, row))
                current_table_results.append(result_dict)
                
            # Agregar resultados a la lista global
            for item in current_table_results:
                # Guardar score original para referencia
                item["_raw_score"] = item.get("relevance_score", 0)
                all_results.append(item)
    
    # Medir tiempo de BD (antes de reranking)
    db_search_time = time.time() - search_start_time
    
    # Ordenar preliminarmente por score original para que el análisis de reranking tenga sentido
    all_results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
    
    # Aplicar Reranking si hay cliente disponible
    reranking_time = 0.0
    if VIVO_CLIENT and all_results:
        # Usamos la query original para el reranker (mejor contexto semántico)
        all_results, reranking_time = _rerank_results(query, all_results, limit)
    
    # Ordenar todos los resultados por puntuación de relevancia
    all_results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
    
    # Limitar al número de resultados solicitado
    all_results = all_results[:limit]
    
    # Agregar URLs de documentos a los resultados
    all_results = _add_document_urls_to_results(all_results)
    
    # Guardar los resultados en contexto global para construir el prompt
    set_last_search_context(query, all_results)
    
    # Guardar las queries SQL ejecutadas en variable global
    set_last_executed_sql_queries(executed_queries)
    
    # Calcular tiempo total de búsqueda (BD + reranking)
    total_search_time = db_search_time + reranking_time
    
    print(f"✅ Búsqueda completada")
    print(f"📊 Resultados encontrados: {len(all_results)}")
    print(f"📝 Queries SQL ejecutadas: {len(executed_queries)}")
    if reranking_time > 0:
        print(f"⏱️ Tiempo de búsqueda: BD {db_search_time:.2f}s + Reranking {reranking_time:.2f}s = Total {total_search_time:.2f}s")
    else:
        print(f"⏱️ Tiempo de búsqueda: {total_search_time:.2f}s")
    print("-"*80 + "\n")
    
    return json.dumps({
        "success": True,
        "query": query,
        "search_type": search_type,
        "search_classification": search_classification,
        "tables_searched": [SEARCH_CONFIG["tables"][t]["name"] for t in table_names],
        "num_tables": len(table_names),
        "executed_queries": executed_queries,
        "num_results": len(all_results),
        "search_time": total_search_time,
        "db_search_time": db_search_time,
        "reranking_time": reranking_time,
        "results": all_results
    }, ensure_ascii=False)


@tool
def custom_sql_search(sql_query: str, search_text: str = "") -> str:
    """
    Herramienta para ejecutar consultas SQL personalizadas en las tablas de TeleRadio.
    FUERZA automáticamente búsqueda fulltext en todos los campos definidos para cada tabla.
    
    TABLAS AUTORIZADAS Y SUS COLUMNAS:

    🔸 teleradio_content_editorial:
       Columnas: id, magazine_id, article_id, page_number, section_title, main_title, full_text, brief_summary, media_description, caption_literal, linked_article_id, publication_date
    
    🔸 teleradio_content_tv_schedule:
       Columnas: id, magazine_id, channel, block_name, page_number, date, time, title, is_color, sponsor, content_description, media_description, caption_literal, linked_article_id, day_of_week
    
    🔸 teleradio_content_radio_schedule:
       Columnas: id, magazine_id, station, station_information, page_number, date, time, title, content_description, linked_article_id, day_of_week
    
    🔸 teleradio_content_advertising:
       Columnas: id, magazine_id, advertiser, ad_copy, page_number
    
    🔸 teleradio_content_others:
       Columnas: id, magazine_id, title, description, content, page_number
    
    Args:
        sql_query: Consulta SQL personalizada (solo SELECT)
        search_text: Texto de búsqueda opcional para aplicar MATCH en todos los campos fulltext
        
    Returns:
        String JSON con resultados y metadatos
    """
    print("\n" + "="*100)
    print("🗃️ CUSTOM SQL SEARCH TOOL - INICIO")
    print("="*100)
    print(f"📝 SQL ORIGINAL DEL LLM:")
    print(sql_query)
    print(f"\n📊 search_text recibido: '{search_text}'")
    
    sql_query = normalize_dates_in_sql(sql_query)
    print(f"📝 SQL NORMALIZADO (fechas YYYY-MM-DD):\n{sql_query}\n")

    # 🆕 Inicializar search_classification
    search_classification = "unknown"

    # Normalizar SELECT * en UNION para evitar desajustes de columnas
    def _normalize_union_selects(query: str) -> str:
        if "union" not in query.lower():
            return query

        select_fields_by_table = {
            "teleradio_content_editorial": "id, magazine_id, page_number, publication_date as date, main_title as title, full_text, brief_summary, '' as content_description, '' as channel, '' as station, '' as time, '' as advertiser, day_of_week",
            "teleradio_content_tv_schedule": "id, magazine_id, page_number, date, title, '' as full_text, '' as brief_summary, content_description, channel, '' as station, time, '' as advertiser, day_of_week",
            "teleradio_content_radio_schedule": "id, magazine_id, page_number, date, title, '' as full_text, '' as brief_summary, content_description, '' as channel, station, time, '' as advertiser, day_of_week",
            "teleradio_content_advertising": "id, magazine_id, page_number, publication_date as date, '' as title, '' as full_text, '' as brief_summary, ad_copy as content_description, '' as channel, '' as station, '' as time, advertiser",
            "teleradio_content_others": "id, magazine_id, page_number, publication_date as date, title, content as full_text, description as brief_summary, '' as content_description, '' as channel, '' as station, '' as time, '' as advertiser",
            "teleradio_content_image_embeddings": "id, magazine_id, page_number, CURRENT_TIMESTAMP as date, description as title, '' as full_text, description as brief_summary, '' as content_description, '' as channel, '' as station, '' as time, '' as advertiser",
        }

        pattern = r"SELECT\s+\*\s+FROM\s+([a-zA-Z0-9_\.]+)"

        def replacer(match):
            table_name = match.group(1)
            fields = select_fields_by_table.get(table_name.lower())
            if not fields:
                return match.group(0)
            return f"SELECT {fields} FROM {table_name}"

        normalized_query = re.sub(pattern, replacer, query, flags=re.IGNORECASE)
        if normalized_query != query:
            print("🧩 SQL NORMALIZADO PARA UNION (SELECT * ajustado):")
            print(normalized_query)
        return normalized_query

    sql_query = _normalize_union_selects(sql_query)

    # 🆕 ASEGURAR QUE SIEMPRE HAYA page_number y magazine_id en el SELECT
    def _ensure_required_fields_in_select(query: str) -> str:
        """
        Asegura que SELECT siempre incluya page_number y magazine_id.
        Si están ausentes, los agrega al principio del SELECT.
        """
        required_fields = ["magazine_id", "page_number"]
        modified = False
        
        def fix_select(match):
            nonlocal modified
            distinct_word = match.group(1) or ""
            select_clause = match.group(2).strip()
            select_upper = select_clause.upper()
            
            # Verificar si faltan campos requeridos en este SELECT
            missing_fields = []
            for field in required_fields:
                if not re.search(r'\b' + field + r'\b', select_upper, re.IGNORECASE):
                    missing_fields.append(field)
            
            if not missing_fields:
                return match.group(0)  # No hay campos faltantes (retorna con FROM incluido)
            
            # Si SELECT es *, no modificar
            if select_clause.strip() == "*":
                return match.group(0)
            
            # Agregar campos faltantes (IMPORTANTE: agregar " FROM" al final)
            fields_to_add = ", ".join(missing_fields)
            modified = True
            return f"SELECT {distinct_word}{fields_to_add}, {select_clause} FROM"
        
        new_query = re.sub(r"SELECT\s+(DISTINCT\s+)?(.*?)\s+FROM", fix_select, query, flags=re.IGNORECASE | re.DOTALL)
        
        if modified:
            print(f"✅ SELECT modificado para incluir campos requeridos: {required_fields}")
        else:
            print(f"✅ SELECT ya incluye todos los campos requeridos")
        
        return new_query
    
    sql_query = _ensure_required_fields_in_select(sql_query)

    # Validación básica de seguridad
    print("🔒 VALIDACIÓN DE SEGURIDAD:")
    sql_upper = sql_query.upper()
    dangerous_keywords = ["DROP", "DELETE", "TRUNCATE", "INSERT", "UPDATE", "ALTER", "CREATE"]
    
    for keyword in dangerous_keywords:
        if keyword in sql_upper:
            print(f"   ❌ RECHAZADO: Operación '{keyword}' no permitida\n")
            return json.dumps({
                "success": False,
                "error": f"Operación no permitida: {keyword}",
                "results": []
            })
    print("   ✅ Seguridad: OK (sin operaciones peligrosas)\n")

    # Validación de tablas permitidas
    print("📊 VALIDACIÓN DE TABLAS PERMITIDAS:")
    allowed_tables = {t.lower() for t in SEARCH_CONFIG["tables"].keys()}
    print(f"   📋 Tablas permitidas: {', '.join(sorted(allowed_tables))}\n")
    
    used_tables = {t.lower() for t in _extract_sql_tables(sql_query)}
    print(f"   🔍 Tablas detectadas en SQL: {', '.join(sorted(used_tables)) if used_tables else 'NINGUNA'}\n")
    
    if not used_tables:
        print("   ❌ ERROR: La consulta SQL debe incluir una tabla válida en FROM o JOIN.")
        print("   ✓ Tablas válidas: " + ", ".join(sorted(allowed_tables)))
        print("="*80 + "\n")
        return json.dumps({
            "success": False,
            "error": f"La consulta SQL debe incluir una tabla válida en FROM o JOIN.\nTablas permitidas: {', '.join(sorted(allowed_tables))}",
            "results": []
        })
    
    if not used_tables.issubset(allowed_tables):
        invalid_tables = sorted(used_tables - allowed_tables)
        print(f"   ❌ ERROR: Tabla(s) no permitida(s): {', '.join(invalid_tables)}")
        print(f"   ✓ Tablas válidas: {', '.join(sorted(allowed_tables))}")
        print("="*80 + "\n")
        return json.dumps({
            "success": False,
            "error": f"❌ Tabla(s) no permitida(s): {', '.join(invalid_tables)}\n✓ Tablas permitidas: {', '.join(sorted(allowed_tables))}",
            "results": []
        })
    
    print("   ✅ Validación de tablas: OK\n")
    
    # 🆕 AGREGAR AUTOMÁTICAMENTE MATCH FULLTEXT EN TODOS LOS CAMPOS DEFINIDOS
    print("🔍 FORZANDO BÚSQUEDA FULLTEXT EN CAMPOS DEFINIDOS:")
    
    # Extraer la tabla principal (la primera en FROM)
    from_pattern = r"FROM\s+([a-zA-Z0-9_\.]+)(?:\s+(?:AS\s+)?([a-zA-Z0-9_]+))?(?:\s|,|WHERE|JOIN|INNER|LEFT|RIGHT|ON|$)"
    from_match = re.search(from_pattern, sql_query, flags=re.IGNORECASE)
    
    if from_match:
        primary_table = from_match.group(1).split(".")[-1].lower()
        print(f"   📌 Tabla principal detectada: {primary_table}")
        
        # Obtener los search_fields de la tabla principal
        table_config = SEARCH_CONFIG["tables"].get(primary_table)
        if table_config:
            search_fields = table_config.get("search_fields", [])
            print(f"   🔎 Campos fulltext definidos: {', '.join(search_fields)}")
            
            # EXTRAER VALORES DE BÚSQUEDA DE LA QUERY ACTUAL
            # Separar búsquedas de FECHA de búsquedas de TEXTO
            search_terms = set()
            date_filter = None
            date_field = table_config.get("date_field")
            
            # Función auxiliar para normalizar fechas a YYYY-MM-DD
            def normalize_date_value(value: str) -> str:
                """Convierte fechas a formato YYYY-MM-DD si es posible"""
                # YYYY-MM-DD o YYYY/MM/DD
                match = re.search(r"^(19\d{2}|20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])$", value)
                if match:
                    return match.group(1) + "-" + match.group(2) + "-" + match.group(3)
                
                # DD-MM-YYYY o DD/MM/YYYY
                match = re.search(r"^(0[1-9]|[12]\d|3[01])[-/](0[1-9]|1[0-2])[-/](19\d{2}|20\d{2})$", value)
                if match:
                    return match.group(3) + "-" + match.group(2) + "-" + match.group(1)
                
                # Solo año YYYY
                match = re.search(r"^(19\d{2}|20\d{2})$", value)
                if match:
                    return match.group(1) + "-01-01"
                
                return None  # No es una fecha
            
            def is_date_like(value: str) -> bool:
                """Detecta si un valor parece ser una fecha"""
                # Patrones de fecha
                date_patterns = [
                    r"^\d{4}-\d{2}-\d{2}$",  # YYYY-MM-DD
                    r"^\d{4}/\d{2}/\d{2}$",  # YYYY/MM/DD
                    r"^\d{2}-\d{2}-\d{4}$",  # DD-MM-YYYY
                    r"^\d{2}/\d{2}/\d{4}$",  # DD/MM/YYYY
                    r"^\d{4}$",              # YYYY
                    r"\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b",
                    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",
                ]
                return any(re.search(pattern, value, re.IGNORECASE) for pattern in date_patterns)
            
            def is_structured_field(field_name: str) -> bool:
                """Detecta si un campo es estructurado (no para fulltext search)"""
                structured_fields = [
                    'channel', 'station', 'advertiser', 'date', 'time',
                    'id', 'magazine_id', 'page_number', 'is_color'
                ]
                return field_name.lower() in structured_fields
            
            # Verificar si ya existe un filtro de fecha en el WHERE original
            existing_date_filter = None
            if date_field:
                date_pattern = rf"\b{date_field}\s*=\s*'([^']+)'"
                date_match = re.search(date_pattern, sql_query, re.IGNORECASE)
                if date_match:
                    existing_date_filter = date_match.group(1)
                    print(f"   📅 Filtro de fecha existente detectado: {date_field} = '{existing_date_filter}'")
            
            # Patrón: campo = 'valor' (capturando también el nombre del campo)
            # Mejorado para manejar paréntesis: busca en cualquier parte, no solo después de WHERE/AND
            value_pattern = r"\b([a-zA-Z_]+)\s*=\s*'([^']+)'"
            for match in re.finditer(value_pattern, sql_query, flags=re.IGNORECASE):
                field_name = match.group(1)
                value = match.group(2)
                
                if value.strip():
                    # Ignorar campos estructurados
                    if is_structured_field(field_name):
                        print(f"   ⊝ Campo estructurado ignorado: {field_name} = '{value}'")
                        continue
                    
                    if is_date_like(value):
                        normalized_date = normalize_date_value(value)
                        if normalized_date and not existing_date_filter:
                            date_filter = normalized_date
                            print(f"   📅 Fecha extraída y normalizada: '{value}' → '{normalized_date}'")
                    else:
                        search_terms.add(value)
                        print(f"   ✓ Término de búsqueda extraído: '{value}'")
            
            # Patrón: campo LIKE '%valor%' (capturando también el nombre del campo)
            # Mejorado para manejar paréntesis
            like_pattern = r"\b([a-zA-Z_]+)\s+LIKE\s+'%([^%]+)%'"
            for match in re.finditer(like_pattern, sql_query, flags=re.IGNORECASE):
                field_name = match.group(1)
                value = match.group(2)
                
                if value.strip():
                    # Ignorar campos estructurados
                    if is_structured_field(field_name):
                        print(f"   ⊝ Campo estructurado ignorado (LIKE): {field_name} LIKE '%{value}%'")
                        continue
                    
                    if is_date_like(value):
                        normalized_date = normalize_date_value(value)
                        if normalized_date and not existing_date_filter:
                            date_filter = normalized_date
                            print(f"   📅 Fecha extraída (LIKE) y normalizada: '{value}' → '{normalized_date}'")
                    else:
                        search_terms.add(value)
                        print(f"   ✓ Término de búsqueda (LIKE) extraído: '{value}'")
            
            # Agregar el search_text del parámetro si se proporciona
            if search_text and search_text.strip():
                if is_date_like(search_text):
                    normalized_date = normalize_date_value(search_text)
                    if normalized_date and not existing_date_filter:
                        date_filter = normalized_date
                        print(f"   📅 Parámetro search_text es fecha, normalizado: '{search_text}' → '{normalized_date}'")
                else:
                    search_terms.add(search_text)
                    print(f"   ✓ Parámetro search_text agregado como término: '{search_text}'")
            
            # Si ya existe un filtro de fecha, usarlo
            if existing_date_filter and not date_filter:
                date_filter = existing_date_filter
            
            # Verificar si es búsqueda genérica
            combined_search_terms = " ".join(search_terms)
            is_generic = _is_generic_date_query(combined_search_terms)
            search_classification = 'generic' if is_generic else 'specific'
            
            print(f"\n   🔍 ANÁLISIS DE BÚSQUEDA:")
            print(f"      - Términos extraídos: {search_terms if search_terms else '(ninguno)'}")
            print(f"      - Términos combinados: '{combined_search_terms}'")
            print(f"      - Fecha detectada: {date_filter if date_filter else '(ninguna)'}")
            print(f"      - Fecha existente en WHERE: {existing_date_filter if existing_date_filter else '(ninguna)'}")
            print(f"      - WHERE en query: {'Sí' if 'WHERE' in sql_query.upper() else 'No'}")
            print(f"      - Tipo de búsqueda: {'🟢 GENÉRICA' if is_generic else '🔵 ESPECÍFICA'}")
            print(f"      - Tiene MATCH en SQL?: {'⚠️ SÍ' if 'MATCH(' in sql_query.upper() else '✅ NO'}")
            print(f"      - Tiene KNN_MATCH en SQL?: {'⚠️ SÍ' if 'KNN_MATCH(' in sql_query.upper() else '✅ NO'}")
            
            # Limpiar términos si es búsqueda genérica
            if is_generic:
                print(f"   ℹ️  Búsqueda GENÉRICA → NO se agregará MATCH fulltext")
                search_terms.clear()
            elif search_terms:
                print(f"   🎯 Búsqueda ESPECÍFICA → Se aplicará MATCH fulltext")
            
            # Construir condiciones WHERE
            where_conditions = []
            
            # CASO 1: Búsqueda genérica con WHERE existente que incluye fecha
            # NO modificar la query original
            if is_generic and "WHERE" in sql_query.upper() and (existing_date_filter or date_filter):
                print(f"\n   ✅ ✅ ✅ EJECUTANDO CASO 1: Búsqueda genérica con WHERE completo")
                print(f"      → NO se modificará la query original (solo limpieza de MATCH si está)")
                print(f"      → is_generic={is_generic}, WHERE={'SÍ' if 'WHERE' in sql_query.upper() else 'NO'}, existing_date_filter={existing_date_filter}, date_filter={date_filter}")
                
                # CRÍTICO: Si el LLM generó MATCH/KNN_MATCH en búsqueda genérica, quitarlos
                if "MATCH(" in sql_query.upper() or "KNN_MATCH(" in sql_query.upper():
                    print(f"      ⚠️ ⚠️ ⚠️  Detectado MATCH/KNN_MATCH en búsqueda genérica - ELIMINANDO")
                    print(f"      SQL ANTES de limpieza:\n{sql_query}\n")
                    
                    sql_query = _remove_fulltext_search_from_sql(sql_query)
                    
                    # Validar que el SQL esté bien formado después de la limpieza
                    if re.search(r'\bWHERE\s+(ORDER|GROUP|LIMIT|$)', sql_query, flags=re.IGNORECASE):
                        print(f"      ⚠️ WHERE quedó vacío después de limpiar MATCH - eliminando WHERE completo")
                        sql_query = re.sub(r'\bWHERE\s+', '', sql_query, flags=re.IGNORECASE)
                    
                    # Validar que no haya AND/OR colgando
                    if re.search(r'\bWHERE\s+(AND|OR)\s+', sql_query, flags=re.IGNORECASE):
                        print(f"      ⚠️ WHERE tiene AND/OR al inicio - limpiando")
                        sql_query = re.sub(r'\bWHERE\s+(AND|OR)\s+', 'WHERE ', sql_query, flags=re.IGNORECASE)
                    
                    print(f"      SQL DESPUÉS de limpieza:\n{sql_query}\n")
                    print(f"      ✅ MATCH/KNN_MATCH ELIMINADOS")
                else:
                    print(f"      ✅ No hay MATCH/KNN_MATCH - SQL está limpio")
                
                print()  # Salto de línea final
                # where_conditions ya está vacío, no hacer nada más
                
            # CASO 2: Necesitamos agregar fecha (no existe en WHERE)
            elif date_filter and date_field and not existing_date_filter:
                print(f"\n   ✅ ✅ ✅ EJECUTANDO CASO 2: Agregar filtro de fecha")
                date_condition = f"{date_field} = '{date_filter}'"
                where_conditions.append(date_condition)
                print(f"      → Fecha agregada: {date_condition}")
                
                # Si es genérica, asegurar que no hay MATCH del LLM
                if is_generic and ("MATCH(" in sql_query.upper() or "KNN_MATCH(" in sql_query.upper()):
                    print(f"      ⚠️ ⚠️ ⚠️  Búsqueda genérica con MATCH/KNN_MATCH detectado - ELIMINANDO")
                    print(f"      SQL ANTES de limpieza:\n{sql_query}\n")
                    sql_query = _remove_fulltext_search_from_sql(sql_query)
                    
                    # Validar que el SQL esté bien formado después de la limpieza
                    if re.search(r'\bWHERE\s+(ORDER|GROUP|LIMIT|$)', sql_query, flags=re.IGNORECASE):
                        print(f"      ⚠️ WHERE quedó vacío después de limpiar MATCH - eliminando WHERE completo")
                        sql_query = re.sub(r'\bWHERE\s+', '', sql_query, flags=re.IGNORECASE)
                    
                    if re.search(r'\bWHERE\s+(AND|OR)\s+', sql_query, flags=re.IGNORECASE):
                        print(f"      ⚠️ WHERE tiene AND/OR al inicio - limpiando")
                        sql_query = re.sub(r'\bWHERE\s+(AND|OR)\s+', 'WHERE ', sql_query, flags=re.IGNORECASE)
                    
                    print(f"      SQL DESPUÉS de limpieza:\n{sql_query}\n")
                    print(f"      ✅ MATCH/KNN_MATCH ELIMINADOS")
                
                print()  # Salto de línea
                
            # CASO 3: Búsqueda específica con términos
            elif search_terms:
                print(f"\n   ⚙️  CASO: Búsqueda específica con términos")
                print(f"      → Total de términos: {len(search_terms)}")
                
                # Crear condiciones MATCH para cada término
                all_match_conditions = []
                for term in search_terms:
                    term_matches = [f"MATCH({field}, '{term}')" for field in search_fields]
                    all_match_conditions.extend(term_matches)
                
                combined_matches = " OR ".join(all_match_conditions)
                
                # Agrupar las condiciones MATCH
                if len(all_match_conditions) > 1:
                    where_conditions.append(f"({combined_matches})")
                else:
                    where_conditions.append(combined_matches)
                
                print(f"      → MATCH conditions agregadas\n")
            else:
                print(f"\n   ℹ️  CASO: Sin modificaciones necesarias")
                
                # Si es genérica, asegurar que no hay MATCH del LLM
                if is_generic and ("MATCH(" in sql_query.upper() or "KNN_MATCH(" in sql_query.upper()):
                    print(f"      ⚠️ ⚠️ ⚠️  Búsqueda genérica con MATCH/KNN_MATCH detectado - ELIMINANDO")
                    print(f"      SQL ANTES de limpieza:\n{sql_query}\n")
                    sql_query = _remove_fulltext_search_from_sql(sql_query)
                    
                    # Validar que el SQL esté bien formado después de la limpieza
                    if re.search(r'\bWHERE\s+(ORDER|GROUP|LIMIT|$)', sql_query, flags=re.IGNORECASE):
                        print(f"      ⚠️ WHERE quedó vacío después de limpiar MATCH - eliminando WHERE completo")
                        sql_query = re.sub(r'\bWHERE\s+', '', sql_query, flags=re.IGNORECASE)
                    
                    if re.search(r'\bWHERE\s+(AND|OR)\s+', sql_query, flags=re.IGNORECASE):
                        print(f"      ⚠️ WHERE tiene AND/OR al inicio - limpiando")
                        sql_query = re.sub(r'\bWHERE\s+(AND|OR)\s+', 'WHERE ', sql_query, flags=re.IGNORECASE)
                    
                    print(f"      SQL DESPUÉS de limpieza:\n{sql_query}\n")
                    print(f"      ✅ MATCH/KNN_MATCH ELIMINADOS")
                
                print()  # Salto de línea
            
            # Aplicar todas las condiciones WHERE
            if where_conditions:
                combined_where = " AND ".join(where_conditions)
                print(f"\n   ⚙️  Condiciones adicionales a agregar: {combined_where}")
                
                if "WHERE" in sql_query.upper():
                    # Hay WHERE existente - envolver con paréntesis y combinar
                    print("   ⚙️  Query con WHERE detectada - combinando condiciones")
                    
                    where_pattern = r"(WHERE\s+)(.*?)(\s+(?:ORDER|GROUP|LIMIT|$)|\Z)"
                    
                    def where_replacer(match):
                        where_keyword = match.group(1)
                        where_condition = match.group(2)
                        rest = match.group(3) if match.group(3) else ""
                        return f"{where_keyword}({where_condition}) AND {combined_where}{rest}"
                    
                    sql_query = re.sub(where_pattern, where_replacer, sql_query, flags=re.IGNORECASE | re.DOTALL)
                else:
                    # No hay WHERE - crear uno nuevo
                    print("   ⚙️  Sin WHERE detectado - creando WHERE con condiciones")
                    
                    if re.search(r"(ORDER|GROUP|LIMIT)", sql_query, flags=re.IGNORECASE):
                        sql_query = re.sub(
                            r"(\s+)(ORDER|GROUP|LIMIT)",
                            f" WHERE {combined_where} \\1\\2",
                            sql_query,
                            flags=re.IGNORECASE
                        )
                    else:
                        sql_query = f"{sql_query.rstrip()} WHERE {combined_where}"
            else:
                print(f"   ℹ️  Sin términos de búsqueda detectados - se ejecutará la query original")
    
    print()
    

    # 🆕 ASEGURAR QUE SIEMPRE SE EXTRAE _score Y SE ORDENA POR _score DESC
    print("📊 OPTIMIZANDO QUERY PARA EXTRACCIÓN DE _score:")
    
    try:
        sql_upper_for_score = sql_query.upper()
        has_aggregation = any(token in sql_upper_for_score for token in [
            "COUNT(", "SUM(", "AVG(", "MIN(", "MAX(", "GROUP BY"
        ])

        if has_aggregation:
            print("   ℹ️ Query con agregacion detectada - se omite _score y ORDER BY _score")
            raise RuntimeError("skip_score_optimization")

        # 1. Agregar _score al SELECT si no está
        if "_score" not in sql_query.upper():
            # Si SELECT * está presente, NO agregar _score (CrateDB lo incluirá automáticamente)
            if "SELECT *" not in sql_query.upper():
                print("   ➕ Agregando _score al SELECT")
                
                # Encontrar la posición de FROM y agregar _score antes
                from_pos = sql_query.upper().find(" FROM ")
                if from_pos != -1:
                    select_part = sql_query[:from_pos].rstrip()
                    rest_of_query = sql_query[from_pos:]
                    
                    # Asegurar que no termina con coma
                    if select_part.rstrip().endswith(','):
                        select_part = select_part.rstrip(', ').rstrip()
                    
                    sql_query = f"{select_part}, _score{rest_of_query}"
                    print("   ✅ _score agregado al SELECT")
        
        # 2. Agregar ORDER BY _score DESC si no existe ORDER BY
        if "ORDER BY" not in sql_query.upper():
            print("   ➕ Agregando ORDER BY _score DESC")
            sql_query = f"{sql_query.rstrip()}\nORDER BY _score DESC"
            print("   ✅ ORDER BY _score DESC agregado")
        elif "_score" not in sql_query.upper().split("ORDER BY")[-1]:
            # Si hay ORDER BY pero no ordena por _score, reemplazarlo por _score DESC
            print("   🔄 Modificando ORDER BY para ordenar por _score DESC")
            
            # Encontrar la última ocurrencia de ORDER BY
            order_by_pos = sql_query.upper().rfind("ORDER BY")
            if order_by_pos != -1:
                # Extraer lo que viene antes de ORDER BY
                before_order = sql_query[:order_by_pos].rstrip()
                sql_query = f"{before_order}\nORDER BY _score DESC"
                print("   ✅ ORDER BY modificado a _score DESC")
    
    except RuntimeError as e:
        if str(e) != "skip_score_optimization":
            print(f"   ⚠️ Error optimizando query: {str(e)}")
            print("   ℹ️ Continuando con query original")
    except Exception as e:
        print(f"   ⚠️ Error optimizando query: {str(e)}")
        print("   ℹ️ Continuando con query original")

    # 🆕 FORZAR LÍMITE CONFIGURADO
    configured_limit = get_sql_results_limit()
    print(f"📊 FORZANDO LÍMITE CONFIGURADO ({configured_limit} resultados):")

    if "LIMIT" not in sql_query.upper():
        print(f"   ➕ No hay LIMIT en la query - agregando LIMIT {configured_limit}")
        sql_query = f"{sql_query.rstrip()}\nLIMIT {configured_limit}"
    else:
        # Extraer el valor del LIMIT actual
        limit_pattern = r"LIMIT\s+(\d+)"
        match = re.search(limit_pattern, sql_query, flags=re.IGNORECASE)
        if match:
            current_limit = int(match.group(1))
            if current_limit != configured_limit:
                print(f"   🔄 LIMIT actual: {current_limit} → Cambiado a: {configured_limit}")
                sql_query = re.sub(limit_pattern, f"LIMIT {configured_limit}", sql_query, flags=re.IGNORECASE)
            else:
                print(f"   ✅ LIMIT actual: {current_limit} (igual al configurado)")
    

    print(f"📝 SQL FINAL:\n{sql_query}\n")
    
    # Normalizar fechas a YYYY-MM-DD format
    print("🔍 NORMALIZANDO FORMATO DE FECHAS...")
    sql_query = normalize_dates_in_sql(sql_query)
    print(f"✅ Fechas normalizadas al formato YYYY-MM-DD\n")
    
    # LOG FINAL COMPLETO
    print("\n" + "="*100)
    print("🎯 🎯 🎯 SQL FINAL QUE SE VA A EJECUTAR EN CRATEDB:")
    print("="*100)
    print(sql_query)
    print("="*100)
    print(f"⚠️ Verificar que NO haya MATCH: {'❌ SÍ HAY' if 'MATCH(' in sql_query.upper() else '✅ NO HAY'}")
    print(f"⚠️ Verificar que NO haya KNN_MATCH: {'❌ SÍ HAY' if 'KNN_MATCH(' in sql_query.upper() else '✅ NO HAY'}")
    print("="*100 + "\n")
    
    # Ejecutar query
    print("⚙️ EJECUTANDO QUERY EN CRATEDB...")
    search_start_time = time.time()  # 🆕 Iniciar medición de tiempos
    result = execute_cratedb_query(sql_query)
    db_search_time = time.time() - search_start_time  # 🆕 Tiempo de ejecución en BD
    
    if not result:
        print("   ❌ ERROR: Error ejecutando la consulta SQL")
        print("="*80 + "\n")
        return json.dumps({
            "success": False,
            "error": "Error ejecutando la consulta SQL",
            "results": [],
            "db_search_time": db_search_time,
            "reranking_time": 0.0,
            "search_time": db_search_time
        })

    # Formatear resultados
    cols = result.get("cols", [])
    rows = result.get("rows", [])
    
    formatted_results = []
    for row in rows:
        result_dict = dict(zip(cols, row))
        formatted_results.append(result_dict)
    
    print(f"   ✅ Query ejecutada exitosamente")
    print(f"   📊 Columnas: {', '.join(cols)}")
    print(f"   📈 Filas retornadas: {len(formatted_results)}\n")
    
    # Guardar los resultados de búsqueda en contexto global
    # Asegurar que existe un campo 'relevance_score' a partir de posibles '_score' u otros
    for item in formatted_results:
        item['relevance_score'] = item.get('relevance_score', item.get('_score', 0))

    # 🆕 Si hay cliente de reranking, aplicarlo y medir tiempo
    reranking_time = 0.0
    try:
        if VIVO_CLIENT and formatted_results:
            rerank_query = search_text.strip() if search_text and search_text.strip() else sql_query
            print(f"   🔄 Ejecutando reranking para custom_sql_search (query para reranker: '{rerank_query[:80]}')")
            rerank_start = time.time()  # 🆕 Iniciar medición de reranking
            formatted_results, reranking_time = _rerank_results(rerank_query, formatted_results, get_sql_results_limit())
            # Asegurar orden por relevance_score después de reranking
            formatted_results.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
    except Exception as e:
        print(f"   ⚠️ Error ejecutando reranking en custom_sql_search: {e}")

    # Agregar URLs de documentos
    formatted_results = _add_document_urls_to_results(formatted_results)

    last_query, _ = get_last_search_context()
    if last_query:
        set_last_search_context(last_query, formatted_results)
    else:
        set_last_search_context("búsqueda personalizada", formatted_results)
    
    # Guardar la query SQL ejecutada en variable global
    clear_executed_sql_queries()
    add_executed_sql_query("custom_query", sql_query)
    
    # 🆕 Calcular tiempo total
    total_search_time = db_search_time + reranking_time
    if reranking_time > 0:
        print(f"⏱️ Tiempo de búsqueda: BD {db_search_time:.2f}s + Reranking {reranking_time:.2f}s = Total {total_search_time:.2f}s")
    else:
        print(f"⏱️ Tiempo de búsqueda: {total_search_time:.2f}s")
    
    print("✅ CUSTOM SQL SEARCH TOOL - COMPLETADO")
    print("="*80 + "\n")
    
    return json.dumps({
        "success": True,
        "sql_query": sql_query,
        "search_text": search_text,
        "search_classification": search_classification,
        "num_results": len(formatted_results),
        "results": formatted_results,
        "search_time": total_search_time,
        "db_search_time": db_search_time,
        "reranking_time": reranking_time
    }, ensure_ascii=False)


def _add_png_links_to_response(response_text: str, sources: List[Dict]) -> str:
    """
    Reemplaza las menciones de documentos en la respuesta con enlaces PNG.
    Busca patrones como 'documento 1', 'Documento 2', 'Documentos 3, 5, 7...' y los reemplaza.
    Mantiene la palabra documento/s pero elimina los números.
    
    Args:
        response_text: Texto de respuesta generado por el LLM
        sources: Lista de fuentes con información de documentos y URLs
        
    Returns:
        Texto con enlaces PNG y información de revista
    """
    if not sources:
        return response_text
    
    def extract_revista_id(magazine_id: str) -> str:
        """
        Extrae el número de revista del magazine_id.
        Formato esperado: "TELE radio_1965-07-12_ejemplar_394_analysis.json"
        Retorna: "revista#394"
        """
        if not magazine_id:
            return ""
        
        # Buscar patrón ejemplar_XXX y extraer el número
        match = re.search(r'ejemplar_(\d+)', magazine_id)
        if match:
            return f"revista#{match.group(1)}"
        
        # Si no tiene formato ejemplar, retornar el magazine_id tal cual
        return magazine_id
    
    # Crear mapa de número de documento a información completa
    doc_to_info = {}
    print(f"📋 _add_png_links_to_response: Procesando {len(sources)} fuentes")
    for source in sources:
        doc_num = source.get("document")
        png_url = source.get("png_url")
        magazine_id = source.get("magazine_id")
        date = source.get("date")
        
        # print(f"   Fuente doc_num={doc_num}, png_url={png_url[:50] if png_url else 'None'}..., magazine_id={magazine_id}, date={date}")
        
        if doc_num and png_url:
            doc_to_info[doc_num] = {
                "url": png_url,
                "magazine_id": extract_revista_id(magazine_id),  # Convertir a revista#XXX
                "date": date
            }
        #     print(f"      ✅ Agregado a doc_to_info[{doc_num}]")
        # elif doc_num:
        #     print(f"      ⚠️ Sin PNG URL para documento {doc_num}")
    
    # Buscar y reemplazar menciones de documentos
    # Patrón 1: "Documentos X, Y, Z..." (plural con lista de números)
    # Captura "Documentos" seguido de números separados por comas, "y", o espacios
    pattern_plural = r'\b([Dd]ocumentos)\s+([\d,\sy\-]+)(?!\s*\[)'
    
    def replacer_plural(match):
        doc_word = match.group(1)  # "documentos" o "Documentos"
        numbers_text = match.group(2)  # "3, 5, 7, 8, 9, 10, 12..."
        
        try:
            # Extraer todos los números de la lista
            numbers = re.findall(r'\d+', numbers_text)
            
            # Construir el resultado con enlaces (mantener palabra documentos, quitar números)
            result_parts = [doc_word]
            for i, num_str in enumerate(numbers):
                doc_num = int(num_str)
                if doc_num in doc_to_info:
                    info = doc_to_info[doc_num]
                    # Construir información adicional (revista#XXX y fecha)
                    extra_info = []
                    if info.get("magazine_id"):
                        extra_info.append(info["magazine_id"])
                    if info.get("date"):
                        extra_info.append(info["date"])
                    
                    extra_str = f" ({', '.join(extra_info)})" if extra_info else ""
                    
                    # Agregar enlace sin número de documento
                    if i > 0:
                        result_parts.append(",")
                    result_parts.append(f" [🗄️]({info['url']}){extra_str}")
                else:
                    # Número sin enlace (mantener referencia básica)
                    if i > 0:
                        result_parts.append(",")
                    result_parts.append(f" [doc {num_str}]")
            
            return "".join(result_parts)
        except Exception as e:
            # Si hay error en el procesamiento, devolver el texto original
            print(f"⚠️ Error procesando lista de documentos: {e}")
            return match.group(0)
    
    try:
        # Primero procesar listas plurales
        response_with_links = re.sub(pattern_plural, replacer_plural, response_text)
    except Exception as e:
        print(f"⚠️ Error en patrón plural: {e}")
        response_with_links = response_text
    
    # Patrón 2: "documento X" singular (case insensitive)
    # Usamos negative lookahead para no procesar si ya hay un enlace después
    pattern_singular = r'\b([Dd]ocumento)\s+(\d+)(?!\s*\[)'
    
    def replacer_singular(match):
        doc_word = match.group(1)  # "documento" o "Documento"
        doc_num = int(match.group(2))
        
        if doc_num in doc_to_info:
            info = doc_to_info[doc_num]
            # Construir información adicional (revista#XXX y fecha)
            extra_info = []
            if info.get("magazine_id"):
                extra_info.append(info["magazine_id"])
            if info.get("date"):
                extra_info.append(info["date"])
            
            extra_str = f" ({', '.join(extra_info)})" if extra_info else ""
            
            # Reemplazar "documento X" con "documento [enlace]"
            return f"{doc_word} [🗄️]({info['url']}){extra_str}"
        else:
            return match.group(0)  # Dejar sin cambios si no hay URL
    
    try:
        response_with_links = re.sub(pattern_singular, replacer_singular, response_with_links)
    except Exception as e:
        print(f"⚠️ Error en patrón singular: {e}")
    
    print(f"📋 doc_to_info final: {len(doc_to_info)} fuentes con URL")
    if response_with_links != response_text:
        print(f"✅ Se agregaron enlaces PNG a la respuesta")
    else:
        print(f"⚠️ No se agregaron enlaces PNG (respuesta sin cambios)")
    
    return response_with_links


def generate_response_internal(
    user_query: str,
    search_results: List[Dict],
    additional_context: str = "",
    llm_backend: str = "ollama",
    llm_model: Optional[str] = None,
    stream: bool = False,
    stream_handler=None,
    allow_llm_without_docs: bool = False,
) -> Dict[str, Any]:
    """
    Función interna para generar respuestas usando LLM basadas en los resultados de búsqueda.
    NO es una tool de LangChain, sino una función interna llamada por el grafo.
    
    Args:
        user_query: Pregunta original del usuario
        search_results: Resultados de la búsqueda (SOLO están incluidos campos de texto)
        additional_context: Contexto adicional opcional
        
    Returns:
        Diccionario con la respuesta generada en español
    """
    # Capturar tiempo de inicio de generación de respuesta
    response_start_time = time.time()
    
    print("\n" + "="*80)
    print("🔴 GENERATE_RESPONSE EJECUTÁNDOSE")
    print("="*80)
    print(f"   Resultados recibidos: {len(search_results)}")
    print(f"   Streaming: {'Sí' if stream else 'No'} (stream_handler {'presente' if stream_handler else 'ausente'})")
    # Depuración: mostrar backend/model recibido
    # Provisional: desactivar streaming globalmente hasta nueva orden
    try:
        # Si hay una variable de entorno DISABLE_STREAMING=1, forzamos stream=False
        if os.getenv("DISABLE_STREAMING", "1") == "1":
            if stream:
                print("⚠️ Streaming temporalmente desactivado por DISABLE_STREAMING=1; usando modo bloqueante")
            stream = False
    except Exception:
        stream = False
    print(f"   DEBUG llm_backend (raw): {llm_backend!r}, llm_model: {llm_model!r}")
    print(f"   DEBUG stream flag: {stream!r}, stream_handler set?: {stream_handler is not None}")
    # Normalizar posibles valores (aceptar 'gemini', 'ollama' o variantes)
    if isinstance(llm_backend, str):
        lb = llm_backend.strip().lower()
        if lb.startswith('gem'):
            llm_backend = 'gemini'
        elif lb.startswith('olla') or lb.startswith('env') or 'ollama' in lb:
            llm_backend = 'ollama'
        else:
            llm_backend = lb
    else:
        llm_backend = 'ollama'
    print(f"   DEBUG llm_backend (normalized): {llm_backend}")
    # Modelo de reranking configurado (para mostrar en el pie de respuesta)
    reranker_model = os.getenv("RERANKER_MODEL", "qwen3rerank")
    # Sanitizar `search_results` y envolver el procesamiento en try/except
    try:
        if search_results is None:
            search_results = []
        elif not isinstance(search_results, list):
            search_results = list(search_results)
    except Exception:
        search_results = []


    orig_len = len(search_results)
    # Reemplazar entradas no-dict por diccionarios vacíos para evitar AttributeError
    cleaned_results = []
    invalid_count = 0
    for i, r in enumerate(search_results):
        if isinstance(r, dict):
            cleaned_results.append(r)
        else:
            invalid_count += 1
            print(f"   ⚠️ Entrada inválida en search_results índice {i}: {type(r)} - será ignorada")
    search_results = cleaned_results
    if invalid_count:
        print(f"   ⚠️ Se filtraron {invalid_count} entradas no válidas de search_results")

    # Aplica reranking solo si los resultados no tienen ya relevance_score
    # (hybrid_search y custom_sql_search ya rerankean internamente; image_text_search no)
    already_reranked = any(
        isinstance(r, dict) and r.get('relevance_score') is not None
        for r in search_results
    )
    if not already_reranked:
        try:
            if search_results:
                print(f"   🔄 Aplicando reranking (resultados sin score previo, modelo: {reranker_model})")
                search_results, _ = _rerank_results(user_query, search_results, len(search_results))
        except Exception as e:
            print(f"⚠️ Error aplicando reranking previo al filtrado: {e}")
    else:
        print(f"   ℹ️ Reranking omitido: los resultados ya tienen relevance_score asignado")

    # Obtener umbral de score configurado (0.0 - 1.0)
    llm_threshold = get_llm_score_threshold()

    # Seleccionar únicamente los documentos cuyo score de reranking esté por encima del umbral
    filtered_results = [r for r in search_results if r.get('relevance_score', 0) >= llm_threshold]

    print(f"📊 Total resultados recibidos: {len(search_results)}")
    print(f"📊 Umbral LLM: {llm_threshold:.2f} -> Documentos que cumplen: {len(filtered_results)}")

    # Si no hay documentos por encima del umbral, decidir comportamiento según flag
    if not filtered_results and not allow_llm_without_docs:
        print("   ⚠️ No hay documentos que superen el umbral de relevancia para enviar al LLM. Generando respuesta automática.")
        prompt = "No se encontraron documentos relevantes para la consulta tras aplicar el filtrado de relevancia."
        set_last_prompt(prompt)
        response_time = time.time() - response_start_time
        return {
            "success": True,
            "response": "No se han encontrado documentos relevantes para tu consulta. Intenta reformularla o ampliar los términos de búsqueda para obtener resultados.",
            "sources": [],
            "search_results": search_results,
            "prompt_used": prompt,
            "response_time": response_time,
            "reranker_model": reranker_model,
        }
    else:
        if not filtered_results and allow_llm_without_docs:
            print("   ℹ️ No hay documentos por encima del umbral, pero se permite llamar al LLM para generar ayuda/información del sistema.")
            original_results = []
            text_results = []
        # En el caso contrario (hay filtered_results), el flujo continúa normalmente

    # Filtrar solo campos de texto para enviar al LLM
    text_results = _prepare_results_for_llm(filtered_results)

    print(f"📊 Text results (filtrados): {len(text_results)}")

    # Guardar los resultados originales (filtrados) para preservar png_url y otros metadatos
    original_results = filtered_results
    
    def _call_llm(prompt: str) -> str:
        # Helper wrapper to call the selected backend
        backend = (llm_backend or "ollama").lower()
        if backend in ("gemini", "google"):
            model_to_use = llm_model or os.getenv("GEMINI_MODEL") or "gemini-2.0-flash"
        else:
            model_to_use = llm_model or os.getenv("OLLAMA_LLM_MODEL") or OLLAMA_LLM_MODEL
        try:
            return call_llm_prompt(prompt, backend=backend, model=model_to_use)
        except Exception as e:
            print(f"⚠️ Error llamando al LLM ({backend}): {e}")
            raise

    # Preparar contexto para el LLM
    context_parts = []
    sources = []
    
    for idx, result in enumerate(original_results, 1):
        # Defensive: skip entries that are None or not dicts
        if not isinstance(result, dict):
            print(f"   ⚠️ Ignorando entrada no válida en original_results en posición {idx}: {type(result)}")
            continue

        magazine_id = result.get("magazine_id", "N/A")
        page_number = result.get("page_number", "N/A")
        title = result.get("title", "N/A")
        date = result.get("date", None)  # Obtener fecha si existe
        entry_time = result.get("time", None)  # Obtener hora si existe
        score = result.get("relevance_score", 0)
        
        # Construir contenido con campos de texto
        text_fields = text_results[idx - 1] if idx - 1 < len(text_results) else {}
        
        content_lines = []
        # Evitar duplicar campos ya incluidos en el encabezado del documento
        skip_fields = {"date", "title", "time"}
        # Coerce header values to strings safely
        title_str = str(title) if title is not None else ""
        date_str = str(date) if date is not None else ""
        entry_time_str = str(entry_time) if entry_time is not None else ""
        header_values = {
            title_str.strip().lower(),
            date_str.strip().lower(),
            entry_time_str.strip().lower(),
        }
        for field, value in text_fields.items():
            if not value or field in skip_fields:
                continue
            value_norm = str(value).strip().lower()
            if value_norm in header_values:
                # Evita repetir valores ya presentes en el encabezado
                continue
            # Capitalizar el nombre del campo
            field_name = field.replace("_", " ").title()
            content_lines.append(f"{field_name}: {value}")
        
        content_text = "\n".join(content_lines) if content_lines else "Contenido no disponible"
        
        # Siempre agregar el documento (incluso sin contenido, para preservar la fecha)
        doc_number = len(context_parts) + 1  # Usar el número real de documentos agregados
        
        # Construir información del documento
        doc_info = f"[Documento {doc_number}]\n"
        doc_info += f"Relevancia: {score:.4f}\n"
        doc_info += f"Ejemplar: {magazine_id}\n"
        doc_info += f"Página: {page_number}\n"
        if date:  # Incluir fecha si existe (IMPORTANTE para contexto)
            doc_info += f"Fecha: {date}\n"
        if entry_time:  # Incluir hora si existe
            doc_info += f"Hora: {entry_time}\n"
        doc_info += f"Título: {title}\n"
        doc_info += f"---\n"
        doc_info += f"{content_text}\n"
        
        context_parts.append(doc_info)
        
        # Agregar fecha y hora a las fuentes si existen
        source_info = {
            "document": doc_number,
            "magazine_id": magazine_id,
            "page_number": page_number,
            "title": title
        }
        # Incluir valores de relevancia para que la UI pueda mostrarlos
        source_info["score"] = score
        source_info["relevance_score"] = score
        if result.get("similarity") is not None:
            source_info["similarity"] = result.get("similarity")
        if result.get("png_url"):
            source_info["png_url"] = result.get("png_url")
        if date:
            source_info["date"] = date
        if entry_time:
            source_info["time"] = entry_time
        
        # Construir URL del PNG si no está ya establecida
        if not source_info.get("png_url"):
            if magazine_id != "N/A" and page_number != "N/A":
                # Usar magazine_id directamente: /ejemplar_{magazine_id}/ejemplar_{magazine_id}_pagina_{page_number}.png
                try:
                    magazine_id_str = str(magazine_id)
                    match = re.search(r"ejemplar_(\d+)", magazine_id_str, re.IGNORECASE)
                    if match:
                        magazine_id_str = match.group(1)
                    page_num_str = str(page_number)
                    png_url = f"{PNG_BASE_URL}/ejemplar_{magazine_id_str}/ejemplar_{magazine_id_str}_pagina_{page_num_str}.png"
                    source_info["png_url"] = png_url
                    print(f"   ✅ URL construida para doc {doc_number}: {png_url}")
                except Exception as e:
                    print(f"   ⚠️ Error construyendo PNG URL para magazine_id={magazine_id}, page_number={page_number}: {e}")
            else:
                print(f"   ⚠️ No se puede construir URL: magazine_id={magazine_id}, page_number={page_number}")
        
        sources.append(source_info)
    
    context = "\n".join(context_parts)
    
    if additional_context:
        context = f"{additional_context}\n\n{context}"
    
    print(f"📊 Context length: {len(context)} caracteres")
    print(f"📊 Context parts: {len(context_parts)}")
    print(f"📊 Sources: {len(sources)}")
    
    # Prompt para el LLM
    prompt = f"""Eres TELERÍN, un asistente inteligente de búsqueda de información sobre la revista TeleRadio (1957-1965). Tu nombre es TELERÍN y ayudas a los usuarios a encontrar información en la base de datos histórica de TeleRadio. Basándote en los siguientes documentos, responde a la pregunta del usuario.

INSTRUCCIÓN DE IDIOMA (MUY IMPORTANTE):
⚠️ RESPONDE SIEMPRE EN ESPAÑOL a menos que el usuario pida explícitamente otro idioma en su pregunta.
Si la pregunta es en español, la respuesta DEBE estar en español.
Si la pregunta está en otro idioma o pide explícitamente otra lengua, entonces responde en ese idioma.

DOCUMENTOS:
{context}

PREGUNTA DEL USUARIO: {user_query}

INSTRUCCIONES ADICIONALES:
- Responde de forma clara y concisa
- IMPORTANTE: SIEMPRE cita los documentos usando el formato exacto "documento N" (donde N es el número) cuando menciones información de ellos
- Ejemplo correcto: "Según el documento 1, el programa..."
- Ejemplo correcto: "En el documento 2 se menciona..."
- NO uses formatos como "ejemplar_109", "página 15" o "la revista" sin indicar el número de documento
- SIEMPRE incluye el número de documento al citar información
- Cuando cites un documento, menciona explícitamente su Relevancia entre paréntesis (ej: "Documento 1 (Relevancia: 0.85)...")
- Indica el ejemplar y página después de citar el documento si es relevante
- Si la información en los documentos no es suficiente, indícalo
- Mantén un tono profesional y útil
- La respuesta debe estar EN ESPAÑOL

- OBLIGATORIO: Identifica y nombra TODAS las apariciones de la consulta del usuario dentro de los documentos.
    - Para cada documento citado, incluye las frases o fragmentos textuales donde aparece la consulta (o sus variantes relevantes), entrecomilladas.
    - Indica el número de documento antes de cada fragmento citado. Si no hay apariciones, indícalo explícitamente.

- 📺 IMPORTANTE PARA PROGRAMACIÓN DE TV/RADIO:
    - Si los documentos incluyen horarios (campo "Hora"), SIEMPRE presenta los programas ordenados cronológicamente en formato 24h (00:00 a 23:59)
    - Si los documentos muestran horarios en formato 12h ("mañana/tarde", "a.m./p.m." o rangos 0:00-12:00), conviértelos SIEMPRE a 24h antes de ordenar y presentar
    - Los programas deben listarse en orden de hora de inicio: primero 00:xx, luego 01:xx... hasta 23:xx
    - Nunca pongas programas de madrugada (0:xx, 1:xx, 2:xx, etc.) al principio: van DESPUÉS de los programas de la noche (23:xx)
    - El orden correcto es: 00:00-06:59 (madrugada) → 07:00-12:59 (mañana) → 13:00-18:59 (tarde) → 19:00-23:59 (noche) → 00:00... (siguiente día)
    - Cuando listes múltiples programas, agrúpalos por bloques horarios o enuméralos manteniendo el orden chronológico
    - Ejemplo válido: "13:30 - Programa A", "19:45 - Programa B", "23:15 - Programa C", "00:30 - Programa D (madrugada siguiente)"

RESPUESTA:"""
    
    # If allow_llm_without_docs is True, use a simplified prompt that DOES NOT
    # instruct the model to cite documents. This is used for system/help queries
    # where there are no documents to cite and we want a plain help response.
    if allow_llm_without_docs:
        prompt_no_docs = f"""Eres TELERÍN, un asistente de búsqueda de la revista TeleRadio (1957-1965).
Responde en español de forma clara y concisa a la pregunta del usuario. No cites documentos ni inventes números de documento; proporciona una respuesta práctica y, cuando corresponda, ejemplos y sugerencias de siguiente paso.

Contexto adicional (si existe):
{additional_context}

PREGUNTA DEL USUARIO: {user_query}

INSTRUCCIONES:
- Responde en español
- Sé breve y usa viñetas cuando sea útil
- No incluyas referencias a "documento N" ni exigencias de cita
- Si no puedes responder con certeza, indícalo claramente

RESPUESTA:"""
        prompt = prompt_no_docs

    # Guardar el prompt en la variable global
    set_last_prompt(prompt)
    print(f"\n✅ PROMPT GUARDADO EN VARIABLE GLOBAL ({len(prompt)} caracteres)")
    
    try:
        # Seleccionar backend y modelo
        backend = (llm_backend or "ollama").lower()
        if backend == "gemini":
            # Preferir el cliente oficial google.genai si está disponible
            gemini_key = os.getenv("GEMINI_API_KEY")
            # Si se selecciona Gemini por UI y no se especifica modelo, usar gemini-3-flash-preview
            model_to_use = os.getenv("GEMINI_MODEL") or "gemini-3-flash-preview"

            if not gemini_key:
                raise Exception("GEMINI_API_KEY no está configurado en variables de entorno")

            if GENAI_AVAILABLE:
                try:
                    client = genai.Client(api_key=gemini_key)
                    print(f"📡 Enviando solicitud a Gemini via google.genai (modelo: {model_to_use})")
                    # If streaming requested, try client streaming first if available
                    # Try various genai streaming APIs in order of likelihood
                    if stream:
                        try:
                            # Newer genai clients expose models.generate_content_stream
                            if hasattr(client, 'models') and hasattr(client.models, 'generate_content_stream'):
                                print("🔁 STREAMING: using genai.models.generate_content_stream...")
                                generated_text = ""
                                try:
                                    stream_iter = client.models.generate_content_stream(
                                        model=model_to_use,
                                        contents=[{"role": "user", "parts": [{"text": prompt}]}]
                                    )
                                    # Emit sources metadata once before streaming chunks
                                    if stream_handler:
                                        try:
                                            stream_handler({"type": "sources", "payload": sources})
                                        except Exception:
                                            pass

                                    for chunk in stream_iter:
                                        try:
                                            text_part = getattr(chunk, 'text', None) or (chunk.get('text') if isinstance(chunk, dict) else None)
                                        except Exception:
                                            text_part = None
                                        if text_part:
                                            generated_text += str(text_part)
                                            if stream_handler:
                                                try:
                                                    stream_handler({"type": "chunk", "text": str(text_part)})
                                                except Exception as e:
                                                    print(f"⚠️ STREAM HANDLER ERROR (genai.models): {e}")
                                                    pass
                                except Exception as e_stream:
                                    print(f"⚠️ genai.models.generate_content_stream failed: {e_stream}")
                                    # fall through to other streaming attempts
                            # legacy: client.responses.stream (older/other genai variants)
                            elif hasattr(client, 'responses') and hasattr(client.responses, 'stream'):
                                print("🔁 STREAMING: attempting genai client.responses.stream...")
                                stream_generator = client.responses.stream(
                                    model=model_to_use,
                                    input=prompt
                                )
                                generated_text = ""
                                # Emit sources metadata before streaming chunks
                                if stream_handler:
                                    try:
                                        stream_handler({"type": "sources", "payload": sources})
                                    except Exception:
                                        pass

                                for event in stream_generator:
                                    chunk = None
                                    try:
                                        chunk = getattr(event, 'text', None) or (event.get('text') if isinstance(event, dict) else None)
                                    except Exception:
                                        chunk = None
                                    if chunk:
                                        print(f"🔁 STREAMING: genai chunk received (len={len(str(chunk))})")
                                        generated_text += str(chunk)
                                        if stream_handler:
                                            try:
                                                print("🔁 STREAMING: invoking stream_handler for genai chunk")
                                                stream_handler({"type": "chunk", "text": str(chunk)})
                                            except Exception as e:
                                                print(f"⚠️ STREAM HANDLER ERROR (genai): {e}")
                                                pass
                            else:
                                # No streaming API on this client; raise to trigger fallback to HTTP or blocking
                                raise AttributeError("No genai streaming API available on client")
                        except Exception as e:
                            print(f"⚠️ Streaming via genai failed: {e}")
                            # If client streaming failed, try HTTP streaming endpoint if configured
                            gemini_api = os.getenv("GEMINI_API_URL")
                            gemini_key_http = os.getenv("GEMINI_API_KEY")
                            if stream and gemini_api and gemini_key_http:
                                try:
                                    print("🔁 STREAMING: attempting Gemini HTTP streaming fallback")
                                    resp = requests.post(
                                        gemini_api,
                                        headers={"Authorization": f"Bearer {gemini_key_http}", "Content-Type": "application/json"},
                                        json={"model": model_to_use, "prompt": prompt, "stream": True},
                                        timeout=120,
                                        stream=True,
                                    )
                                    generated_text = ""
                                    if resp.status_code == 200:
                                        # Emit sources metadata before streaming chunks
                                        if stream_handler:
                                            try:
                                                stream_handler({"type": "sources", "payload": sources})
                                            except Exception:
                                                pass

                                        for line in resp.iter_lines(decode_unicode=True):
                                            if not line:
                                                continue
                                            text = line.strip()
                                            print(f"🔁 STREAMING: Gemini HTTP chunk (fallback): {text[:120]}")
                                            try:
                                                j = json.loads(text)
                                                part = j.get('response') or j.get('text') or j.get('output') or None
                                                if part:
                                                    generated_text += str(part)
                                                    if stream_handler:
                                                        try:
                                                            stream_handler({"type": "chunk", "text": str(part)})
                                                        except Exception:
                                                            pass
                                            except Exception:
                                                generated_text += text
                                                if stream_handler:
                                                    try:
                                                        stream_handler({"type": "chunk", "text": text})
                                                    except Exception:
                                                        pass
                                    else:
                                        print(f"❌ Error Gemini streaming fallback: {resp.status_code} - {resp.text}")
                                except Exception as e2:
                                    print(f"⚠️ Gemini HTTP streaming fallback failed: {e2}")
                                    # fall back to blocking below
                            # fall through to blocking call below
                            response_obj = client.models.generate_content(
                                model=model_to_use,
                                contents=[{"role": "user", "parts": [{"text": prompt}]}],
                            )
                            generated_text = getattr(response_obj, 'text', None) or (response_obj.get('text') if isinstance(response_obj, dict) else None) or ""
                    else:
                        response_obj = client.models.generate_content(
                            model=model_to_use,
                            contents=[
                                {
                                    "role": "user",
                                    "parts": [
                                        {"text": prompt}
                                    ]
                                }
                            ],
                        )

                        # Extraer texto de la respuesta en varias posibles estructuras
                        generated_text = None
                        try:
                            generated_text = getattr(response_obj, "text", None)
                        except Exception:
                            generated_text = None

                        if not generated_text and isinstance(response_obj, dict):
                            generated_text = response_obj.get("text") or response_obj.get("response") or ""

                        # Intentar extraer de candidatos si existe esa estructura
                        if not generated_text:
                            try:
                                candidates = getattr(response_obj, "candidates", None)
                                if candidates and len(candidates) > 0:
                                    first = candidates[0]
                                    generated_text = getattr(first, "content", None)
                                    if isinstance(generated_text, list) and len(generated_text) > 0:
                                        part = generated_text[0]
                                        if isinstance(part, dict):
                                            generated_text = part.get("text") or part.get("content") or str(part)
                                        else:
                                            generated_text = str(part)
                                    else:
                                        generated_text = str(first)
                            except Exception:
                                generated_text = str(response_obj)

                        if generated_text is None:
                            generated_text = ""

                except Exception as e:
                    print(f"❌ Error Gemini (google.genai): {e}")
                    response_time = time.time() - response_start_time
                    return {
                        "success": False,
                        "error": f"Error llamando a Gemini (google.genai): {e}",
                        "response": "No se pudo generar una respuesta en este momento.",
                        "search_results": original_results if 'original_results' in locals() else [],
                        "prompt_used": prompt,
                        "response_time": response_time,
                        "reranker_model": reranker_model,
                        "llm_backend": backend,
                        "llm_model": model_to_use
                    }

            else:
                # Fallback: usar endpoint HTTP si está disponible
                gemini_api = os.getenv("GEMINI_API_URL")
                gemini_key = os.getenv("GEMINI_API_KEY")
                model_to_use = llm_model or os.getenv("GEMINI_MODEL") or "gemini-3-flash-preview"

                if not gemini_api or not gemini_key:
                    # Si no hay configuración de Gemini y no está disponible google.genai,
                    # hacer fallback silencioso a Ollama en lugar de lanzar excepción.
                    print("⚠️ GEMINI no configurado (ni gemini_api/gemini_key ni google.genai). Haciendo fallback a Ollama.")
                    # Preparar request para Ollama usando el mismo prompt
                    model_to_use_ollama = llm_model or os.getenv("OLLAMA_LLM_MODEL") or OLLAMA_LLM_MODEL
                    api_url = f"{OLLAMA_BASE_URL}/api/generate"
                    print(f"📡 (Fallback) Enviando solicitud a: {api_url} (modelo: {model_to_use_ollama})")
                    response = requests.post(
                        api_url,
                        json={
                            "model": model_to_use_ollama,
                            "prompt": prompt,
                            "stream": False,
                            "options": {
                                "temperature": 0.7,
                                "top_p": 0.9
                            }
                        },
                        timeout=120
                    )

                    if response.status_code == 200:
                        generated_text = response.json().get("response", "")
                        # Ajustar llm info para el retorno
                        backend = "ollama"
                        model_to_use = model_to_use_ollama
                    else:
                        print(f"❌ Error en fallback Ollama: {response.status_code} - {response.text}")
                        response_time = time.time() - response_start_time
                        return {
                            "success": False,
                            "error": f"Error generando respuesta (fallback Ollama): {response.text}",
                            "response": "No se pudo generar una respuesta en este momento.",
                            "search_results": original_results if 'original_results' in locals() else [],
                            "prompt_used": prompt,
                            "response_time": response_time,
                            "reranker_model": reranker_model,
                            "llm_backend": "ollama",
                            "llm_model": model_to_use_ollama
                        }
                else:
                    print(f"📡 Enviando solicitud a Gemini: {gemini_api} (modelo: {model_to_use})")
                    if stream:
                        print("🔁 STREAMING: Gemini HTTP streaming requested")
                    # If streaming requested, try streaming via HTTP streaming
                    try:
                        if stream:
                            resp = requests.post(
                                gemini_api,
                                headers={"Authorization": f"Bearer {gemini_key}", "Content-Type": "application/json"},
                                json={"model": model_to_use, "prompt": prompt, "stream": True},
                                timeout=120,
                                stream=True,
                            )
                            generated_text = ""
                            if resp.status_code == 200:
                                # Emit sources metadata before streaming chunks
                                if stream_handler:
                                    try:
                                        stream_handler({"type": "sources", "payload": sources})
                                    except Exception:
                                        pass

                                for line in resp.iter_lines(decode_unicode=True):
                                    if not line:
                                        continue
                                    text = line.strip()
                                    print(f"🔁 STREAMING: Gemini HTTP chunk: {text[:80]}")
                                    # Try to parse JSON line
                                    try:
                                        j = json.loads(text)
                                        part = j.get('response') or j.get('text') or j.get('output') or None
                                        if part:
                                            print(f"🔁 STREAMING: Gemini parsed part (len={len(str(part))})")
                                            generated_text += str(part)
                                            if stream_handler:
                                                try:
                                                    print("🔁 STREAMING: invoking stream_handler for Gemini chunk")
                                                    stream_handler({"type": "chunk", "text": str(part)})
                                                except Exception as e:
                                                    print(f"⚠️ STREAM HANDLER ERROR (gemini http): {e}")
                                                    pass
                                    except Exception:
                                        # Not JSON - treat as raw chunk
                                        generated_text += text
                                        if stream_handler:
                                            try:
                                                print("🔁 STREAMING: invoking stream_handler for Gemini raw chunk")
                                                stream_handler({"type": "chunk", "text": text})
                                            except Exception as e:
                                                print(f"⚠️ STREAM HANDLER ERROR (gemini raw): {e}")
                                                pass
                            else:
                                print(f"❌ Error Gemini streaming: {resp.status_code} - {resp.text}")
                                response_time = time.time() - response_start_time
                                return {
                                    "success": False,
                                    "error": f"Error llamando a Gemini: {resp.status_code} {resp.text}",
                                    "response": "No se pudo generar una respuesta en este momento.",
                                    "search_results": original_results if 'original_results' in locals() else [],
                                    "prompt_used": prompt,
                                    "response_time": response_time,
                                    "reranker_model": reranker_model,
                                    "llm_backend": backend,
                                    "llm_model": model_to_use
                                }
                        else:
                            response = requests.post(
                                gemini_api,
                                headers={"Authorization": f"Bearer {gemini_key}", "Content-Type": "application/json"},
                                json={"model": model_to_use, "prompt": prompt},
                                timeout=120
                            )

                            if response.status_code == 200:
                                resp_json = response.json()
                                # Gemini APIs pueden devolver campos distintos; intentar varias claves
                                generated_text = resp_json.get("response") or resp_json.get("text") or resp_json.get("output") or ""
                            else:
                                print(f"❌ Error Gemini: {response.status_code} - {response.text}")
                                response_time = time.time() - response_start_time
                                return {
                                    "success": False,
                                    "error": f"Error llamando a Gemini: {response.status_code} {response.text}",
                                    "response": "No se pudo generar una respuesta en este momento.",
                                    "search_results": original_results if 'original_results' in locals() else [],
                                    "prompt_used": prompt,
                                    "response_time": response_time,
                                    "reranker_model": reranker_model,
                                    "llm_backend": backend,
                                    "llm_model": model_to_use
                                }
                    except Exception as e:
                        print(f"❌ Error Gemini HTTP/stream: {e}")
                        response_time = time.time() - response_start_time
                        return {
                            "success": False,
                            "error": f"Error llamando a Gemini: {e}",
                            "response": "No se pudo generar una respuesta en este momento.",
                            "search_results": original_results if 'original_results' in locals() else [],
                            "prompt_used": prompt,
                            "response_time": response_time,
                            "reranker_model": reranker_model,
                            "llm_backend": backend,
                            "llm_model": model_to_use
                        }

        else:
            # Default: Ollama (u otro servicio que exponga la misma API)
            model_to_use = llm_model or os.getenv("OLLAMA_LLM_MODEL") or OLLAMA_LLM_MODEL
            api_url = f"{OLLAMA_BASE_URL}/api/generate"
            print(f"📡 Enviando solicitud a: {api_url} (modelo: {model_to_use})")
            try:
                if stream:
                    print("🔁 STREAMING: Ollama HTTP streaming requested")
                    resp = requests.post(
                        api_url,
                        json={
                            "model": model_to_use,
                            "prompt": prompt,
                            "stream": True,
                            "options": {
                                "temperature": 0.7,
                                "top_p": 0.9
                            }
                        },
                        timeout=120,
                        stream=True,
                    )

                    generated_text = ""
                    if resp.status_code == 200:
                            # Assemble partial JSON fragments (Ollama can split JSON across chunks)
                            chunks_seen = False
                            buffer = ""

                            def _extract_json_objects(buf: str):
                                objs = []
                                cur = None
                                depth = 0
                                escape = False
                                in_string = False
                                start_idx = None
                                for i, ch in enumerate(buf):
                                    if cur is None and ch == '{' and not in_string:
                                        start_idx = i
                                        cur = []
                                        depth = 0
                                    if cur is not None:
                                        cur.append(ch)
                                        if ch == '"' and not escape:
                                            in_string = not in_string
                                        if ch == '{' and not in_string:
                                            depth += 1
                                        elif ch == '}' and not in_string:
                                            depth -= 1
                                        if ch == '\\' and not escape:
                                            escape = True
                                        else:
                                            escape = False
                                        if depth == 0:
                                            # complete object
                                            objs.append(''.join(cur))
                                            cur = None
                                            start_idx = None
                                # remaining buffer
                                rem = ''
                                if cur is not None and start_idx is not None:
                                    rem = ''.join(cur)
                                else:
                                    # if we had no current open object, any trailing chars after last obj
                                    # that are not part of an object should be returned as remainder
                                    # find last closing brace
                                    last_close = -1
                                    for i, ch in enumerate(buf):
                                        if ch == '}' and not in_string:
                                            last_close = i
                                    if last_close != -1 and last_close + 1 < len(buf):
                                        rem = buf[last_close+1:]
                                    else:
                                        rem = '' if not objs else ''
                                return objs, rem

                            # Try iter_lines first
                            try:
                                for line in resp.iter_lines(decode_unicode=False):
                                    if not line:
                                        continue
                                    chunks_seen = True
                                    # line may be bytes
                                    if isinstance(line, bytes):
                                        text = line.decode('utf-8', errors='replace')
                                    else:
                                        text = str(line)
#                                    print(f"🔁 STREAMING: Ollama HTTP chunk: {repr(text)[:120]}")
                                    buffer += text
                                    objs, buffer = _extract_json_objects(buffer)
                                    if objs:
                                        for obj_str in objs:
                                            try:
                                                j = json.loads(obj_str)
                                                part = j.get('response') or j.get('text') or None
                                                if part:
                                                    generated_text += str(part)
                                                    if stream_handler:
                                                        try:
                                                            stream_handler({"type": "chunk", "text": str(part)})
                                                        except Exception as e:
                                                            print(f"⚠️ STREAM HANDLER ERROR (ollama): {e}")
                                                            pass
                                            except Exception:
                                                # if parsing fails, send raw
                                                generated_text += obj_str
                                                if stream_handler:
                                                    try:
                                                        stream_handler({"type": "chunk", "text": obj_str})
                                                    except Exception:
                                                        pass
                                    else:
                                        # no full JSON object yet; optionally stream raw fragment
                                        if stream_handler:
                                            try:
                                                stream_handler({"type": "chunk", "text": text})
                                            except Exception:
                                                pass
                            except Exception:
                                pass

                            # If no line-delimited chunks were yielded, try raw chunked reads
                            if not chunks_seen:
                                try:
                                    for chunk_bytes in resp.iter_content(chunk_size=512):
                                        if not chunk_bytes:
                                            continue
                                        chunks_seen = True
                                        text = chunk_bytes.decode('utf-8', errors='replace')
#                                        print(f"🔁 STREAMING: Ollama raw bytes chunk (len={len(text)})")
                                        buffer += text
                                        objs, buffer = _extract_json_objects(buffer)
                                        if objs:
                                            for obj_str in objs:
                                                try:
                                                    j = json.loads(obj_str)
                                                    part = j.get('response') or j.get('text') or None
                                                    if part:
                                                        generated_text += str(part)
                                                        if stream_handler:
                                                            try:
                                                                stream_handler({"type": "chunk", "text": str(part)})
                                                            except Exception as e:
                                                                print(f"⚠️ STREAM HANDLER ERROR (ollama raw-json): {e}")
                                                                pass
                                                except Exception:
                                                    generated_text += obj_str
                                                    if stream_handler:
                                                        try:
                                                            stream_handler({"type": "chunk", "text": obj_str})
                                                        except Exception:
                                                            pass
                                        else:
                                            if stream_handler:
                                                try:
                                                    stream_handler({"type": "chunk", "text": text})
                                                except Exception as e:
                                                    print(f"⚠️ STREAM HANDLER ERROR (ollama raw): {e}")
                                                    pass
                                except Exception as e_chunk:
                                    print(f"⚠️ Error reading Ollama chunked response: {e_chunk}")
                    else:
                        print(f"❌ Error Ollama streaming: {resp.status_code} - {resp.text}")
                        response_time = time.time() - response_start_time
                        return {
                            "success": False,
                            "error": f"Error generando respuesta: {resp.text}",
                            "response": "No se pudo generar una respuesta en este momento.",
                            "search_results": original_results if 'original_results' in locals() else [],
                            "prompt_used": prompt,
                            "response_time": response_time,
                            "reranker_model": reranker_model,
                            "llm_backend": backend,
                            "llm_model": model_to_use
                        }
                else:
                    response = requests.post(
                        api_url,
                        json={
                            "model": model_to_use,
                            "prompt": prompt,
                            "stream": False,
                            "options": {
                                "temperature": 0.7,
                                "top_p": 0.9
                            }
                        },
                        timeout=120
                    )

                    if response.status_code == 200:
                        generated_text = response.json().get("response", "")
                    else:
                        print(f"❌ Error en respuesta: {response.status_code}")
                        response_time = time.time() - response_start_time
                        return {
                            "success": False,
                            "error": f"Error generando respuesta: {response.text}",
                            "response": "No se pudo generar una respuesta en este momento.",
                            "search_results": original_results if 'original_results' in locals() else [],
                            "prompt_used": prompt,
                            "response_time": response_time,
                            "reranker_model": reranker_model,
                            "llm_backend": backend,
                            "llm_model": model_to_use
                        }

            except Exception as e:
                error_msg = str(e)
                print(f"❌ Error Ollama HTTP/stream: {error_msg}")
                response_time = time.time() - response_start_time
                
                # 🆕 Detectar si es un timeout
                is_timeout = any(keyword in error_msg.lower() for keyword in ["timeout", "timed out", "read timed out", "httpconnectionpool"])
                
                if is_timeout:
                    user_message = "⏱️ **Tiempo de espera agotado (Timeout)**\n\nEl modelo tardó demasiado tiempo en responder. Esto puede ocurrir si el servidor está sobrecargado.\n\n💡 **Sugerencias:**\n- Intenta de nuevo en unos momentos\n- Si el problema persiste, prueba con una consulta más simple\n- Verifica que el servidor de LLM esté disponible"
                else:
                    user_message = "No se pudo generar una respuesta en este momento."
                
                return {
                    "success": False,
                    "error": f"Error llamando a {backend}: {error_msg}",
                    "response": user_message,
                    "search_results": original_results if 'original_results' in locals() else [],
                    "prompt_used": prompt,
                    "response_time": response_time,
                    "reranker_model": reranker_model,
                    "llm_backend": backend,
                    "llm_model": model_to_use
                }

        # Post-process generated text
        print(f"✅ Respuesta generada ({len(generated_text)} caracteres)")
        generated_text_with_links = _add_png_links_to_response(generated_text, sources)
        if generated_text_with_links != generated_text:
            print(f"✅ Enlaces PNG agregados a la respuesta")

        # Calcular tiempo de generación de respuesta
        response_time = time.time() - response_start_time

        # If streaming, send a final structured message so the UI can replace the
        # incremental text with the consolidated response containing links and metadata.
        if stream_handler:
            try:
                stream_handler({
                    "type": "final",
                    "response": generated_text_with_links,
                    "sources": sources,
                    "response_time": response_time,
                    "sql_queries": get_last_executed_sql_queries()
                })
            except Exception:
                pass

        print(f"⏱️ Tiempo de generación: {response_time:.2f}s")

        return {
            "success": True,
            "response": generated_text_with_links,
            "sources": sources,
            "search_results": original_results,
            "num_sources": len(sources),
            "prompt_used": prompt,
            "response_time": response_time,
            "reranker_model": reranker_model,
            "llm_backend": backend,
            "llm_model": model_to_use
        }
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Excepción: {error_msg}")
        response_time = time.time() - response_start_time
        
        # 🆕 Detectar si es un timeout
        is_timeout = any(keyword in error_msg.lower() for keyword in ["timeout", "timed out", "read timed out", "httpconnectionpool"])
        
        if is_timeout:
            user_message = "⏱️ **Tiempo de espera agotado (Timeout)**\n\nEl modelo tardó demasiado tiempo en responder. Esto puede ocurrir si el servidor está sobrecargado.\n\n💡 **Sugerencias:**\n- Intenta de nuevo en unos momentos\n- Si el problema persiste, prueba con una consulta más simple\n- Verifica que el servidor de LLM esté disponible"
        else:
            user_message = "No se pudo generar una respuesta en este momento."
        
        return {
            "success": False,
            "error": f"Error llamando al LLM: {error_msg}",
            "response": user_message,
            "search_results": original_results if 'original_results' in locals() else [],
            "prompt_used": prompt if 'prompt' in locals() else "",
            "response_time": response_time,
            "reranker_model": reranker_model,
            "llm_backend": (llm_backend or "ollama"),
            "llm_model": (llm_model or os.getenv('OLLAMA_LLM_MODEL') or OLLAMA_LLM_MODEL)
        }


@tool
def check_schedule_coverage(year: int, month: int, day: int = 0) -> str:
    """
    Comprueba si hay cobertura de programación de TV en un mes/año o en un día concreto.

    Úsala cuando el usuario pregunte:
    - Si hubo algún día sin programación en un mes/año concreto.
    - Qué días faltaron en la parrilla de un mes.
    - Si hay programación registrada para una fecha exacta.
    - Cuántos días cubrió la programación en un período.

    Args:
        year:  Año a consultar (p.e. 1963).
        month: Mes a consultar, 1-12 (p.e. 4).
        day:   Día concreto del mes, 1-31. Si es 0 (por defecto) analiza el mes completo.

    Returns:
        JSON con los resultados: días presentes, días ausentes y, si se pidió un día
        concreto, la lista de programas de ese día.
    """
    import calendar as _calendar
    from datetime import date as _date

    table = f"{COLLECTION_NAME}_tv_schedule"

    # ── Modo día concreto ─────────────────────────────────────────────────────
    if day and day > 0:
        try:
            target = _date(year, month, day)
        except ValueError as exc:
            return json.dumps({"success": False, "error": str(exc)})

        day_str = target.isoformat()
        sql = f"""
            SELECT id, date, title, channel, time, day_of_week, content_description
            FROM "{table}"
            WHERE date = '{day_str}'
            ORDER BY time, channel, title
            LIMIT 500
        """
        res = execute_cratedb_query(sql)
        if res is None:
            return json.dumps({"success": False, "error": "Error consultando CrateDB"})

        rows = res.get("rows", [])
        day_names = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        programas = [
            {
                "titulo":      (r[2] or "").strip(),
                "canal":       (r[3] or "").strip(),
                "hora":        (r[4] or "").strip(),
                "dia_semana":  (r[5] or "").strip(),
                "descripcion": (r[6] or "").strip(),
            }
            for r in rows
        ]
        return json.dumps({
            "success":        True,
            "modo":           "dia",
            "fecha":          day_str,
            "dia_semana":     day_names[target.weekday()],
            "hay_programacion": len(programas) > 0,
            "total_programas":  len(programas),
            "programas":        programas,
        }, ensure_ascii=False)

    # ── Modo mes completo ─────────────────────────────────────────────────────
    prefix = f"{year}-{month:02d}-"
    _, last_day_num = _calendar.monthrange(year, month)

    sql = f"""
        SELECT DISTINCT date
        FROM "{table}"
        WHERE date LIKE '{prefix}%'
        ORDER BY date
    """
    res = execute_cratedb_query(sql)
    if res is None:
        return json.dumps({"success": False, "error": "Error consultando CrateDB"})

    present: set[_date] = set()
    for row in res.get("rows", []):
        raw = row[0]
        if not raw:
            continue
        try:
            present.add(_date.fromisoformat(str(raw).strip()))
        except ValueError:
            pass

    all_days = [_date(year, month, d) for d in range(1, last_day_num + 1)]
    day_names_short = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    missing = [
        {"fecha": d.isoformat(), "dia_semana": day_names_short[d.weekday()]}
        for d in all_days if d not in present
    ]
    present_list = sorted(d.isoformat() for d in present)

    return json.dumps({
        "success":            True,
        "modo":               "mes",
        "periodo":            f"{year}-{month:02d}",
        "total_dias_mes":     last_day_num,
        "dias_con_programacion": len(present),
        "dias_sin_programacion": len(missing),
        "hay_dias_sin_cobertura": len(missing) > 0,
        "fechas_presentes":   present_list,
        "dias_ausentes":      missing,
    }, ensure_ascii=False)


# Lista de herramientas LangChain disponibles
SEARCH_TOOLS = [hybrid_search, custom_sql_search, image_text_search, check_schedule_coverage]
