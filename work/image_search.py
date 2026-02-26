import os
import base64
import requests
from requests.auth import HTTPBasicAuth
import streamlit as st
from PIL import Image
from typing import List, Tuple, Optional

from tools import _rerank_results, VIVO_CLIENT, VIVOEMBCLIENT_AVAILABLE

from config import (
    IMAGE_BASE_URL,
    IMAGE_DESCRIPTION_MODEL,
    IMAGE_EMBEDDING_MODEL,
    COLLECTION_NAME,
    PNG_BASE_URL,
)



def get_image_embedding(image_data: bytes) -> Optional[List[float]]:
    """
    Genera el embedding de una imagen usando VIVOClient
    """
    if not VIVOEMBCLIENT_AVAILABLE or VIVO_CLIENT is None:
        st.error("VIVOClient no está disponible. Instala vivoembclient para usar búsqueda de imágenes.")
        return None

    try:
        base64_image = base64.b64encode(image_data).decode('utf-8')
        if image_data[:4] == b'\x89PNG':
            mime_type = 'image/png'
        elif image_data[:3] == b'\xff\xd8\xff':
            mime_type = 'image/jpeg'
        else:
            mime_type = 'image/jpeg'

        b64_img = f"data:{mime_type};base64,{base64_image}"
        embedding_model = IMAGE_EMBEDDING_MODEL #os.getenv("IMAGE_EMBEDDING_MODEL", "qwen3VL2B")
        print(f"Generando embedding con VIVOClient usando modelo {embedding_model}")

        result = VIVO_CLIENT.embed(b64_img, model=embedding_model)

        if not result:
            st.error("Respuesta vacía de VIVOClient")
            return None
        if not isinstance(result, list) or len(result) == 0:
            st.error(f"Formato de respuesta inesperado: {type(result)}")
            return None

        first_result = result[0]
        if not isinstance(first_result, dict):
            st.error(f"Resultado no es un diccionario: {type(first_result)}")
            return None

        embedding = first_result.get("embedding")
        if embedding:
            print(f"Embedding generado exitosamente, dimensión: {len(embedding)}")
            return embedding
        else:
            st.error("No se encontró campo 'embedding' en la respuesta")
            return None

    except Exception as e:
        st.error(f"Error al procesar imagen con VIVOClient: {str(e)}")
        print(f"Error detallado: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_image_description(image_data: bytes) -> str:
    """
    Obtiene una descripción de la imagen usando un modelo multimodal (Ollama)
    """
    try:
        base64_image = base64.b64encode(image_data).decode('utf-8')
        ollama_url = IMAGE_BASE_URL # os.getenv("IMAGE_BASE_URL", "http://gtc2pc9.cps.unizar.es:11434")
        model = IMAGE_DESCRIPTION_MODEL #os.getenv("IMAGE_DESCRIPTION_MODEL", "qwen3-vl:8b")

        print(f"Solicitando descripción de imagen a Ollama: {ollama_url}")

        response = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model,
                "prompt": "Genera un título corto y descriptivo para esta imagen en español (máximo 10 palabras). Sé específico sobre el contenido principal.",
                "images": [base64_image],
                "stream": False
            },
            timeout=60,
        )

        if response.status_code == 200:
            res = response.json()
            description = res.get("response", "")
            print(f"Descripción obtenida: {description[:100]}...")
            return description
        else:
            print(f"Error en Ollama: {response.status_code} - {response.text}")
            return ""

    except Exception as e:
        print(f"Error al obtener descripción de imagen: {str(e)}")
        import traceback
        traceback.print_exc()
        return ""


def search_similar_images(embedding: List[float], limit: int = 60, rerank_query: Optional[str] = None) -> Tuple[List[dict], Optional[str]]:
    """
    Busca imágenes similares en CrateDB usando el embedding. Devuelve (results, sql_query).
    """
    print(f"🔍 [search_similar_images] Límite recibido: {limit}")
    try:
        cratedb_url = os.getenv("CRATEDB_URL")
        cratedb_username = os.getenv("CRATEDB_USERNAME")
        cratedb_password = os.getenv("CRATEDB_PASSWORD", "")
        png_base_url = os.getenv("PNG_BASE_URL", "http://signal4.cps.unizar.es/rtve/teleradio/png")
        collection_name = os.getenv("COLLECTION_NAME", "teleradio_content")

        embedding_str = str(embedding)
        table_name = f"{collection_name}_image_embeddings"

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
            OR MATCH(description, '{rerank_query}')
            OR MATCH(caption_literal, '{rerank_query}')
        ORDER BY _score DESC
        LIMIT {limit}
        """

        print(f"📊 [SQL] LIMIT usado en query: {limit}")
        print(f"📊 [SQL] Query generada:\n{sql_query}")

        response = requests.post(
            cratedb_url,
            json={"stmt": sql_query},
            auth=HTTPBasicAuth(cratedb_username, cratedb_password),
            timeout=60,
        )

        if response.status_code == 200:
            result = response.json()
            rows = result.get("rows", [])
            cols = result.get("cols", [])

            results = []
            for row in rows:
                row_dict = dict(zip(cols, row))
                if row_dict.get("src"):
                    src_path = row_dict["src"]
                    if "pngprocessed/" in src_path:
                        relative_path = src_path.split("pngprocessed/", 1)[1]
                    else:
                        relative_path = src_path
                    row_dict["png_url"] = f"{png_base_url}/{relative_path}"
                    row_dict["image_path"] = relative_path
                results.append(row_dict)

            final_results = results[:limit]

            try:
                if rerank_query and _rerank_results:
                    print("   🔄 Rerankeando resultados de imagen usando descripción proporcionada...")
                    final_results = _rerank_results(rerank_query, final_results, limit)
            except Exception as e:
                print(f"   ⚠️ Error durante reranking en search_similar_images: {e}")

            return final_results, sql_query
        else:
            st.error(f"Error en consulta a CrateDB: {response.text}")
            return [], sql_query

    except Exception as e:
        st.error(f"Error buscando imágenes similares: {str(e)}")
        return [], None


def render_image_results(results: List[dict], title: str):
    if not results:
        return

    st.markdown(f"### 🖼️ {title}")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Imágenes encontradas", len(results))
    with col2:
        # numero de imágenes con relevancia por encima del umbral (ej. 0.5)
        high_relevance_count = sum(1 for r in results if r.get('relevance_score', 0) > 0.5)
        st.metric("Imágenes con alta relevancia", high_relevance_count)
#        avg_relevance = sum(r.get('relevance_score', 0) for r in results) / len(results) if results else 0
#        st.metric("Relevancia promedio", f"{avg_relevance:.2%}")
    with col3:
        max_relevance = max((r.get('relevance_score', 0) for r in results), default=0)
        st.metric("Máxima relevancia", f"{max_relevance:.2%}")

# Ordenar resultados por relevancia antes de mostrar
    results = sorted(results, key=lambda r: r.get('relevance_score', 0), reverse=True)

    st.markdown("---")
    num_cols = 20
    for row_idx in range(0, len(results), num_cols):
        cols = st.columns(num_cols)
        for col_idx in range(num_cols):
            result_idx = row_idx + col_idx
            if result_idx < len(results):
                result = results[result_idx]
                with cols[col_idx]:
                    with st.popover(f"#{result_idx+1} ({result.get('relevance_score', 0):.2%})"):
                        if result.get('png_url'):
                            try:
                                pngpath = result['png_url']
                                image = Image.open(requests.get(pngpath, stream=True).raw)
                                st.image(image, width=250)
                            except Exception:
                                st.warning("⚠️ Error al cargar")
                        st.markdown(f"**{result.get('magazine_id', 'N/A')}**  \n📖 Pág: {result.get('page_number', 'N/A')}  \n[🔗 PNG]({result.get('png_url', '#')})")
                        if result.get('description'):
                            st.caption(f"📝 {result.get('description')}")
                        if result.get('caption_literal'):
                            st.caption(f"🗒️ {result.get('caption_literal')}")
