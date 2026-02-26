"""
Router GET /api/models — devuelve modelos disponibles en Ollama.
"""
from __future__ import annotations

import os
from typing import List

import requests
from fastapi import APIRouter
from pydantic import BaseModel

from backend.dependencies import get_current_user
from fastapi import Depends

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelInfo(BaseModel):
    name: str
    size: int = 0


class ModelsResponse(BaseModel):
    ollama: List[ModelInfo]


@router.get("", response_model=ModelsResponse)
async def get_models(current_user: dict = Depends(get_current_user)):
    """Retorna la lista de modelos disponibles en el servidor Ollama configurado."""
    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        resp = requests.get(f"{ollama_base}/api/tags", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            models = [
                ModelInfo(name=m["name"], size=m.get("size", 0))
                for m in data.get("models", [])
            ]
            return ModelsResponse(ollama=models)
    except Exception as e:
        print(f"⚠️ No se pudo obtener modelos de Ollama: {e}")

    return ModelsResponse(ollama=[])
