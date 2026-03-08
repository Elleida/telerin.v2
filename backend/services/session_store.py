"""
Almacén de sesiones conversacionales en memoria (por user_id).

Cada usuario autenticado tiene su propio ConversationMemory.
Las sesiones se crean bajo demanda y sobreviven mientras el proceso esté activo.
Para persistencia cross-restart se puede serializar a CrateDB en el futuro.
"""
from __future__ import annotations

import os
from threading import Lock
from typing import Dict

from backend.compat.memory import ConversationMemory
from backend.config import CONVERSATION_MEMORY_CONFIG

_sessions: Dict[str, ConversationMemory] = {}
_lock = Lock()


def get_session(session_id: str) -> ConversationMemory:
    """Devuelve (o crea) la ConversationMemory para un session_id."""
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = ConversationMemory(
                max_history=CONVERSATION_MEMORY_CONFIG.get("max_history", 100),
                context_window=CONVERSATION_MEMORY_CONFIG.get("context_window", 5),
            )
        return _sessions[session_id]


def clear_session(session_id: str) -> None:
    """Elimina la sesión existente para que get_session cree un objeto nuevo y limpio.
    
    Se elimina (en lugar de llamar a .clear() sobre el mismo objeto) para evitar la
    race condition en la que un hilo de background de la query anterior todavía tiene
    referencia al mismo objeto ConversationMemory y le puede añadir turnos viejos
    después del borrado.
    """
    with _lock:
        _sessions.pop(session_id, None)


def delete_session(session_id: str) -> None:
    """Elimina completamente una sesión del store."""
    with _lock:
        _sessions.pop(session_id, None)


def list_active_sessions() -> list[str]:
    with _lock:
        return list(_sessions.keys())
