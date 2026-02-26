"""
Router de búsqueda y análisis de imágenes: /api/image/*
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from backend.compat.image_search import (
    get_image_embedding,
    get_image_description,
    search_similar_images,
)
from backend.compat.tools import (
    get_clip_text_embedding,
    generate_response_internal,
    _rerank_results,
)

from backend.dependencies import get_current_user
from backend.models.schemas import ImageAnalyzeRequest, ImageSearchResponse

router = APIRouter(prefix="/api/image", tags=["image"])


@router.post("/search", response_model=ImageSearchResponse)
async def image_search(
    file: Optional[UploadFile] = File(default=None),
    text_query: Optional[str] = Form(default=None),
    max_results: int = Form(default=60),
    _: dict = Depends(get_current_user),
):
    """
    Busca imágenes similares. Acepta:
      - file: imagen subida (PNG/JPG)
      - text_query: descripción textual
    """
    embedding = None
    description: str | None = None

    if file is not None:
        image_bytes = await file.read()
        description = get_image_description(image_bytes)
        embedding = get_image_embedding(image_bytes)
    elif text_query:
        description = text_query
        embedding = get_clip_text_embedding(text_query)
    else:
        raise HTTPException(status_code=400, detail="Proporciona una imagen o una descripción de texto")

    if not embedding:
        raise HTTPException(status_code=422, detail="No se pudo generar embedding")

    raw = search_similar_images(
        embedding,
        limit=max_results,
        rerank_query=description,
    )
    # search_similar_images may return (results, sql_query) but if _rerank_results
    # wasn't unpacked inside it, results itself may be a tuple (list, float).
    # Normalize defensively.
    if isinstance(raw, tuple) and len(raw) == 2:
        results, sql_query = raw
    else:
        results, sql_query = raw, None
    # Unwrap inner tuple from un-unpacked _rerank_results: (list, elapsed_float)
    if isinstance(results, tuple):
        results = results[0] if results else []

    return ImageSearchResponse(
        results=results or [],
        sql_query=sql_query,
        description=description,
    )


@router.post("/describe")
async def image_describe(
    file: UploadFile = File(...),
    _: dict = Depends(get_current_user),
):
    """Devuelve la descripción generada por el modelo multimodal para una imagen."""
    image_bytes = await file.read()
    description = get_image_description(image_bytes)
    if not description:
        raise HTTPException(status_code=422, detail="No se pudo generar descripción")
    return {"description": description}


@router.post("/analyze")
async def image_analyze(
    body: ImageAnalyzeRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Genera una respuesta LLM a partir de una descripción y resultados de búsqueda de imagen.
    Equivale al botón 'Analizar resultados con el agente conversacional' de app.py.
    """
    # Rerankear antes de generar respuesta (idéntico a app.py)
    results = body.results
    try:
        need_rerank = not any(
            r.get("relevance_score") is not None or r.get("rerank_score") is not None
            for r in results
            if isinstance(r, dict)
        )
        if need_rerank:
            reranked, _ = _rerank_results(body.description, results, len(results))
            if reranked:
                results = reranked
    except Exception:
        pass

    result = generate_response_internal(
        user_query=body.description,
        search_results=results,
        additional_context="Resultados de búsqueda por imagen similar (usar description y caption si existen).",
        llm_backend=body.llm_backend,
        llm_model=body.llm_model,
    )

    return {
        "response":   result.get("response"),
        "sources":    result.get("sources", []),
        "prompt_used": result.get("prompt_used"),
        "error":      result.get("error"),
    }
