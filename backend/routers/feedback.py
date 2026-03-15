"""
Router de feedback: POST /api/feedback
Guarda valoraciones de respuestas (👍/👎) en:
  - feedback.log (JSON Lines) — legado / diagnóstico local.
  - CrateDB tabla ``telerin_feedback`` — persistente y multi-instancia.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.config import CRATEDB_URL, CRATEDB_USERNAME, CRATEDB_PASSWORD
from backend.dependencies import get_current_user

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

# Ruta del fichero de log — configurable vía variable de entorno
# Fallback a /tmp/feedback.log si el path principal no es escribible
_LOG_PATH = Path(os.getenv("FEEDBACK_LOG_PATH", "/app/logs/feedback.log"))
_FALLBACK_LOG_PATH = Path("/tmp/feedback.log")

# Máx. caracteres de respuesta a guardar en el log
_MAX_RESPONSE_CHARS = 1000


# ── CrateDB helpers ────────────────────────────────────────────────────────

def _cratedb(stmt: str, args: list | None = None) -> dict:
    payload: dict = {"stmt": stmt}
    if args:
        payload["args"] = args
    resp = requests.post(
        CRATEDB_URL,
        json=payload,
        auth=HTTPBasicAuth(CRATEDB_USERNAME, CRATEDB_PASSWORD) if CRATEDB_PASSWORD else None,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def ensure_feedback_table() -> None:
    """Crea la tabla ``telerin_feedback`` en CrateDB si no existe.

    Llamado desde main.py en el evento startup.
    """
    try:
        _cratedb("""
            CREATE TABLE IF NOT EXISTS telerin_feedback (
                id               TEXT PRIMARY KEY,
                ts               TEXT,
                username         TEXT,
                session_id       TEXT,
                rating           TEXT,
                query            TEXT,
                response         TEXT,
                comment          TEXT,
                num_sources      INTEGER,
                llm_model        TEXT,
                prompt_tokens    INTEGER,
                response_tokens  INTEGER,
                db_search_s      DOUBLE,
                reranking_s      DOUBLE,
                response_s       DOUBLE,
                total_s          DOUBLE
            )
        """)
        print("✅ [feedback] Tabla telerin_feedback lista")
    except Exception as exc:
        print(f"⚠️ [feedback] No se pudo crear telerin_feedback: {exc}")


# ── Schemas ────────────────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    session_id: str = Field(default="")
    query: str
    response: str
    rating: str = Field(pattern="^(up|down)$")
    comment: str = Field(default="")
    db_search_time: float = Field(default=0.0)
    reranking_time: float = Field(default=0.0)
    response_time: float = Field(default=0.0)
    num_sources: int = Field(default=0)
    llm_model: str = Field(default="")
    prompt_tokens: int = Field(default=0)
    response_tokens: int = Field(default=0)


class FeedbackResponse(BaseModel):
    ok: bool


# ── Endpoint ────────────────────────────────────────────────────────────────

@router.post("", response_model=FeedbackResponse)
async def post_feedback(
    body: FeedbackRequest,
    current_user: dict = Depends(get_current_user),
):
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    username = current_user.get("username", "unknown")
    total_s = round(body.db_search_time + body.reranking_time + body.response_time, 3)

    entry = {
        "ts": ts,
        "user": username,
        "session": body.session_id,
        "rating": body.rating,
        "query": body.query,
        "response": body.response[:_MAX_RESPONSE_CHARS],
        "comment": body.comment,
        "num_sources": body.num_sources,
        "llm_model": body.llm_model,
        "prompt_tokens": body.prompt_tokens,
        "response_tokens": body.response_tokens,
        "timings": {
            "db_search_s": round(body.db_search_time, 3),
            "reranking_s": round(body.reranking_time, 3),
            "response_s": round(body.response_time, 3),
            "total_s": total_s,
        },
    }

    # ── Escritura en CrateDB (primaria) ────────────────────────────────────
    cratedb_ok = False
    try:
        _cratedb(
            "INSERT INTO telerin_feedback "
            "(id, ts, username, session_id, rating, query, response, comment, "
            "num_sources, llm_model, prompt_tokens, response_tokens, "
            "db_search_s, reranking_s, response_s, total_s) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                str(uuid.uuid4()),
                ts,
                username,
                body.session_id,
                body.rating,
                body.query,
                body.response[:_MAX_RESPONSE_CHARS],
                body.comment,
                body.num_sources,
                body.llm_model,
                body.prompt_tokens,
                body.response_tokens,
                round(body.db_search_time, 3),
                round(body.reranking_time, 3),
                round(body.response_time, 3),
                total_s,
            ],
        )
        print(f"📝 Feedback {'👍' if body.rating == 'up' else '👎'} guardado en CrateDB: \"{body.query[:60]}\"")
        cratedb_ok = True
    except Exception as exc:
        print(f"⚠️ No se pudo guardar feedback en CrateDB: {exc}")

    # ── Escritura en fichero (fallback / legado) ────────────────────────────
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    for path in [_LOG_PATH, _FALLBACK_LOG_PATH]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
            if not cratedb_ok:
                print(f"📝 Feedback {'👍' if body.rating == 'up' else '👎'} guardado en {path}: \"{body.query[:60]}\"")
            break
        except Exception as exc:
            print(f"⚠️ No se pudo escribir en {path}: {exc}")

    return FeedbackResponse(ok=True)
