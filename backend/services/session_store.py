"""
Almacén de sesiones conversacionales: L1 RAM + L2 CrateDB.

Cada usuario autenticado tiene su propio ConversationMemory.

  • L1: dict en RAM del proceso actual (acceso O(1), no persiste entre reinicios
        ni se comparte entre workers/procesos distintos).
  • L2: tabla CrateDB ``telerin_sessions`` (persiste entre reinicios y se
        comparte entre todos los workers/réplicas del servicio).

Flujo de lectura:
    get_session(id)  → L1 hit → devuelve inmediato
                     → L1 miss → carga de CrateDB → guarda en L1 → devuelve
                     → no existe en CrateDB → crea instancia vacía

Flujo de escritura (tras cada turno completado):
    save_session(id, memory) → serializa to_dict() → upsert en CrateDB
    (se llama desde chat.py via loop.run_in_executor para no bloquear el
    event loop de asyncio)

Flujo de limpieza:
    clear_session(id) → elimina de L1 + DELETE en CrateDB
                        (nueva conversación limpia para el usuario)
"""
from __future__ import annotations

import json
import time
import threading
from threading import Lock
from typing import Dict, Optional

import requests
from requests.auth import HTTPBasicAuth

from backend.compat.memory import ConversationMemory
from backend.config import (
    CONVERSATION_MEMORY_CONFIG,
    CRATEDB_URL, CRATEDB_USERNAME, CRATEDB_PASSWORD,
    SESSION_MAX_IDLE_HOURS, SESSION_MAX_IDLE_DAYS,
    SESSION_CLEANUP_INTERVAL_SECONDS,
)

# ── L1 cache (por proceso) ────────────────────────────────────────────────────────
_sessions: Dict[str, ConversationMemory] = {}
_last_access: Dict[str, float] = {}   # session_id → epoch seconds del último acceso
_lock = Lock()


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


def ensure_sessions_table() -> None:
    """Crea la tabla ``telerin_sessions`` en CrateDB si no existe.

    Llamado desde main.py en el evento startup, igual que ensure_users_table.
    """
    try:
        _cratedb("""
            CREATE TABLE IF NOT EXISTS telerin_sessions (
                session_id   TEXT PRIMARY KEY,
                user_id      TEXT,
                data         TEXT NOT NULL,
                turn_count   INTEGER DEFAULT 0,
                last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("✅ [session_store] Tabla telerin_sessions lista")
    except Exception as exc:
        print(f"⚠️ [session_store] No se pudo crear telerin_sessions: {exc}")


# ── API pública ────────────────────────────────────────────────────────────

def get_session(session_id: str) -> ConversationMemory:
    """Devuelve (o crea) la ConversationMemory para un session_id.

    Orden de búsqueda:
      1. L1 cache en RAM del proceso actual        → O(1)
      2. L2 CrateDB (persistente, multi-proceso)   → ~1-5 ms
      3. Nueva instancia vacía                     → primera conversación
    """
    with _lock:
        if session_id in _sessions:
            _last_access[session_id] = time.monotonic()
            return _sessions[session_id]

    # Intentar cargar desde CrateDB (fuera del lock para no bloquear otros hilos)
    mem = _load_from_cratedb(session_id)
    if mem is None:
        mem = ConversationMemory(
            max_history=CONVERSATION_MEMORY_CONFIG.get("max_history", 100),
            context_window=CONVERSATION_MEMORY_CONFIG.get("context_window", 5),
        )

    with _lock:
        # Doble check: otro hilo pudo haber creado la sesión mientras cargábamos
        if session_id not in _sessions:
            _sessions[session_id] = mem
        _last_access[session_id] = time.monotonic()
        return _sessions[session_id]


def save_session(session_id: str, memory: Optional[ConversationMemory] = None) -> None:
    """Persiste la sesión del usuario en CrateDB (L2).

    Diseñado para ejecutarse en un ThreadPoolExecutor vía
    ``loop.run_in_executor(None, lambda mem=memory: save_session(id, mem))``
    para no bloquear el event loop de asyncio.

    Si CrateDB no está disponible, registra una advertencia y continúa
    (degradación elegante: la sesión sigue viva en L1 RAM).
    """
    with _lock:
        mem = memory if memory is not None else _sessions.get(session_id)
        if session_id in _sessions:
            _last_access[session_id] = time.monotonic()

    if mem is None:
        return

    try:
        data_json = json.dumps(mem.to_dict(), ensure_ascii=False)
        turn_count = len(mem)

        # Intentar INSERT; si ya existe la fila, hacer UPDATE
        try:
            _cratedb(
                "INSERT INTO telerin_sessions "
                "(session_id, user_id, data, turn_count, last_updated) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                [session_id, session_id, data_json, turn_count],
            )
        except Exception:
            # La fila ya existe (duplicate key) → actualizar
            _cratedb(
                "UPDATE telerin_sessions "
                "SET data = ?, turn_count = ?, last_updated = CURRENT_TIMESTAMP "
                "WHERE session_id = ?",
                [data_json, turn_count, session_id],
            )

        print(f"💾 [session_store] Sesión {session_id[:8]}… guardada ({turn_count} turnos)")

    except Exception as exc:
        print(f"⚠️ [session_store] No se pudo guardar sesión {session_id[:8]}…: {exc}")


def clear_session(session_id: str) -> None:
    """Elimina la sesión de L1 RAM y de CrateDB (nueva conversación limpia).

    Se elimina completamente (en lugar de llamar a .clear() sobre el mismo
    objeto) para evitar la race condition en la que un hilo de background
    todavía tiene referencia al mismo ConversationMemory y le añade turnos
    viejos después del borrado.
    """
    with _lock:
        _sessions.pop(session_id, None)
        _last_access.pop(session_id, None)
    try:
        _cratedb("DELETE FROM telerin_sessions WHERE session_id = ?", [session_id])
    except Exception as exc:
        print(f"⚠️ [session_store] No se pudo borrar sesión {session_id[:8]}… de CrateDB: {exc}")


def delete_session(session_id: str) -> None:
    """Elimina completamente una sesión del store (alias de clear_session)."""
    clear_session(session_id)


def list_active_sessions() -> list[str]:
    """Lista los session_ids activos en el cache L1 de este proceso."""
    with _lock:
        return list(_sessions.keys())

# ── Limpieza de sesiones inactivas ─────────────────────────────────────────────────────

def evict_stale_l1(max_idle_hours: int | None = None) -> int:
    """Expulsa del cache L1 (RAM) las sesiones sin acceso durante más de
    ``max_idle_hours`` horas. Devuelve el número de sesiones eliminadas.

    Las sesiones eliminadas de RAM siguen disponibles en CrateDB; se
    reconstruirán la próxima vez que el usuario conecte.
    """
    hours = max_idle_hours if max_idle_hours is not None else SESSION_MAX_IDLE_HOURS
    cutoff = time.monotonic() - hours * 3600
    stale: list[str] = []

    with _lock:
        for sid, last in list(_last_access.items()):
            if last < cutoff:
                stale.append(sid)
        for sid in stale:
            _sessions.pop(sid, None)
            _last_access.pop(sid, None)

    if stale:
        print(f"🧹 [session_store] L1 evict: {len(stale)} sesiones inactivas ({hours}h) eliminadas de RAM")
    return len(stale)


def evict_stale_cratedb(max_idle_days: int | None = None) -> int:
    """Borra de CrateDB las sesiones cuyo ``last_updated`` supera
    ``max_idle_days`` días. Devuelve el número de filas eliminadas.

    Esta operación es idempotente y segura de lanzar periódicamente.
    Si CrateDB no está disponible se registra una advertencia y se sigue.
    """
    days = max_idle_days if max_idle_days is not None else SESSION_MAX_IDLE_DAYS
    cutoff_ms = int((time.time() - days * 86400) * 1000)  # CrateDB usa ms epoch
    try:
        result = _cratedb(
            "DELETE FROM telerin_sessions WHERE last_updated < ?",
            [cutoff_ms],
        )
        deleted = result.get("rowcount", 0)
        if deleted:
            print(f"🧹 [session_store] CrateDB evict: {deleted} sesiones inactivas (>{days}d) eliminadas")
        return deleted
    except Exception as exc:
        print(f"⚠️ [session_store] No se pudo limpiar CrateDB: {exc}")
        return 0


def _cleanup_loop(interval: int) -> None:
    """Bucle daemon que ejecuta evict_stale_l1 + evict_stale_cratedb
    cada ``interval`` segundos indefinidamente.
    """
    while True:
        time.sleep(interval)
        try:
            evict_stale_l1()
        except Exception as exc:
            print(f"⚠️ [session_store] Error en evict L1: {exc}")
        try:
            evict_stale_cratedb()
        except Exception as exc:
            print(f"⚠️ [session_store] Error en evict CrateDB: {exc}")


def start_cleanup_scheduler() -> None:
    """Arranca el hilo daemon de limpieza de sesiones inactivas.

    Llamado desde main.py en el evento startup. El hilo es daemon, por lo
    que se detiene automáticamente cuando el proceso principal termina.
    Solo hay que llamar a esta función una vez por proceso.
    """
    interval = SESSION_CLEANUP_INTERVAL_SECONDS
    t = threading.Thread(
        target=_cleanup_loop,
        args=(interval,),
        daemon=True,
        name="session-cleanup",
    )
    t.start()
    print(
        f"⏰ [session_store] Scheduler de limpieza arrancado "
        f"(L1={SESSION_MAX_IDLE_HOURS}h, CrateDB={SESSION_MAX_IDLE_DAYS}d, "
        f"intervalo={interval}s)"
    )

# ── Helpers privados ───────────────────────────────────────────────────────

def _load_from_cratedb(session_id: str) -> Optional[ConversationMemory]:
    """Carga una sesión desde CrateDB. Devuelve None si no existe o hay error."""
    try:
        result = _cratedb(
            "SELECT data FROM telerin_sessions WHERE session_id = ? LIMIT 1",
            [session_id],
        )
        rows = result.get("rows", [])
        if not rows:
            return None
        data = json.loads(rows[0][0])
        mem = ConversationMemory.from_dict(data)
        print(f"📂 [session_store] Sesión {session_id[:8]}… restaurada desde CrateDB ({len(mem)} turnos)")
        return mem
    except Exception as exc:
        print(f"⚠️ [session_store] Error cargando sesión {session_id[:8]}…: {exc}")
        return None
