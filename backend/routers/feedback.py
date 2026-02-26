"""
Router de feedback: POST /api/feedback
Guarda valoraciones de respuestas (👍/👎) en feedback.log (JSON Lines).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.dependencies import get_current_user

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

# Ruta del fichero de log — configurable vía variable de entorno
_LOG_PATH = Path(os.getenv("FEEDBACK_LOG_PATH", "/app/feedback.log"))

# Máx. caracteres de respuesta a guardar en el log
_MAX_RESPONSE_CHARS = 1000


class FeedbackRequest(BaseModel):
    session_id: str = Field(default="")
    query: str
    response: str
    rating: str = Field(pattern="^(up|down)$")
    comment: str = Field(default="")
    db_search_time: float = Field(default=0.0)
    reranking_time: float = Field(default=0.0)
    response_time: float = Field(default=0.0)


class FeedbackResponse(BaseModel):
    ok: bool


@router.post("", response_model=FeedbackResponse)
async def post_feedback(
    body: FeedbackRequest,
    current_user: dict = Depends(get_current_user),
):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user": current_user.get("username", "unknown"),
        "session": body.session_id,
        "rating": body.rating,
        "query": body.query,
        "response": body.response[:_MAX_RESPONSE_CHARS],
        "comment": body.comment,
        "timings": {
            "db_search_s": round(body.db_search_time, 3),
            "reranking_s": round(body.reranking_time, 3),
            "response_s": round(body.response_time, 3),
            "total_s": round(body.db_search_time + body.reranking_time + body.response_time, 3),
        },
    }

    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"📝 Feedback {'👍' if body.rating == 'up' else '👎'} guardado: \"{body.query[:60]}\"")
    except Exception as exc:
        print(f"⚠️ Error guardando feedback: {exc}")
        return FeedbackResponse(ok=False)

    return FeedbackResponse(ok=True)
