"""
Router de gestión de sesión conversacional: /api/session/*
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.dependencies import get_current_user
from backend.models.schemas import SessionContext
from backend.services.session_store import clear_session, get_session

router = APIRouter(prefix="/api/session", tags=["session"])


@router.get("/context", response_model=SessionContext)
async def get_context(current_user: dict = Depends(get_current_user)):
    """Devuelve el contexto conversacional del usuario autenticado."""
    session_id = current_user["id"]
    memory = get_session(session_id)

    context = memory.extract_context() if len(memory) > 0 else {}
    last_turn = memory.get_last_turn() if len(memory) > 0 else None

    return SessionContext(
        num_turns=len(memory),
        context_summary=context.get("context_summary", {}),
        global_entities=context.get("global_entities", {}),
        recent_searches=context.get("recent_searches", []),
        last_turn=last_turn,
    )


@router.delete("/clear", status_code=204)
async def clear(current_user: dict = Depends(get_current_user)):
    """Limpia la memoria conversacional del usuario."""
    clear_session(current_user["id"])
