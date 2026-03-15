"""
Logger JSONL rotativo con compresión gzip para consultas y respuestas del agente.

Escribe en dos destinos en paralelo:
  - Fichero local (gzip-rotating) — legado, útil para diagnóstico local.
  - CrateDB tabla ``telerin_query_log`` — persistente y multi-instancia.

La escritura en CrateDB se realiza en un hilo de background para no añadir
latencia al flujo principal. Si CrateDB no está disponible, el log en fichero
sigue funcionando (degradación elegante).
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

import requests
from requests.auth import HTTPBasicAuth

from backend.config import (
    LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP,
    CRATEDB_URL, CRATEDB_USERNAME, CRATEDB_PASSWORD,
)

# ── File handler (legado) ──────────────────────────────────────────────────

class CompressingRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler que comprime el fichero rotado a .gz."""
    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        super().doRollover()
        try:
            rotated = f"{self.baseFilename}.1"
            if os.path.exists(rotated):
                gz_name = rotated + ".gz"
                with open(rotated, "rb") as f_in:
                    with gzip.open(gz_name, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                os.remove(rotated)
        except Exception:
            pass


_query_logger = logging.getLogger("telerin_v2_logger")
if not _query_logger.handlers:
    _query_logger.setLevel(logging.INFO)
    fh = CompressingRotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter("%(message)s"))
    _query_logger.addHandler(fh)


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


def ensure_query_log_table() -> None:
    """Crea la tabla ``telerin_query_log`` en CrateDB si no existe.

    Llamado desde main.py en el evento startup.
    """
    try:
        _cratedb("""
            CREATE TABLE IF NOT EXISTS telerin_query_log (
                id                    TEXT PRIMARY KEY,
                ts                    TEXT,
                type_                 TEXT,
                session_id            TEXT,
                username              TEXT,
                query                 TEXT,
                response              TEXT,
                search_time           DOUBLE,
                response_time         DOUBLE,
                query_type            TEXT,
                search_classification TEXT,
                sql_queries           TEXT
            )
        """)
        print("✅ [query_logger] Tabla telerin_query_log lista")
    except Exception as exc:
        print(f"⚠️ [query_logger] No se pudo crear telerin_query_log: {exc}")


def _insert_to_cratedb(entry: dict) -> None:
    """Inserta una entrada en telerin_query_log. Silencia cualquier error."""
    try:
        _cratedb(
            "INSERT INTO telerin_query_log "
            "(id, ts, type_, session_id, username, query, response, "
            "search_time, response_time, query_type, search_classification, sql_queries) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                entry["id"],
                entry["ts"],
                entry["type_"],
                entry.get("session_id"),
                entry.get("username"),
                entry.get("query"),
                entry.get("response"),
                float(entry.get("search_time") or 0.0),
                float(entry.get("response_time") or 0.0),
                entry.get("query_type"),
                entry.get("search_classification"),
                entry.get("sql_queries"),
            ],
        )
    except Exception:
        pass  # CrateDB no disponible — fichero local sigue activo


def _fire_cratedb(entry: dict) -> None:
    """Lanza la inserción en CrateDB en un hilo daemon (fire-and-forget)."""
    threading.Thread(target=_insert_to_cratedb, args=(entry,), daemon=True).start()


# ── API pública ────────────────────────────────────────────────────────────

def log_user_query(query: str, session_id: str | None = None, username: str | None = None) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    payload = {
        "timestamp": ts,
        "type": "user",
        "session_id": session_id,
        "username": username,
        "query": query,
    }
    try:
        _query_logger.info(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass
    _fire_cratedb({
        "id": str(uuid.uuid4()),
        "ts": ts,
        "type_": "user",
        "session_id": session_id,
        "username": username,
        "query": query,
        "response": None,
        "search_time": 0.0,
        "response_time": 0.0,
        "query_type": None,
        "search_classification": None,
        "sql_queries": None,
    })


def log_assistant_response(
    query: str,
    response: str | None,
    session_id: str | None = None,
    username: str | None = None,
    extra: dict | None = None,
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    payload = {
        "timestamp": ts,
        "type": "assistant",
        "session_id": session_id,
        "username": username,
        "query": query,
        "response": response,
    }
    if extra:
        payload.update(extra)
    try:
        _query_logger.info(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass

    sql_qs = extra.get("sql_queries") if extra else None
    _fire_cratedb({
        "id": str(uuid.uuid4()),
        "ts": ts,
        "type_": "assistant",
        "session_id": session_id,
        "username": username,
        "query": query,
        "response": (response or "")[:2000],  # truncar para no saturar CrateDB
        "search_time": float((extra or {}).get("search_time") or 0.0),
        "response_time": float((extra or {}).get("response_time") or 0.0),
        "query_type": (extra or {}).get("query_type"),
        "search_classification": (extra or {}).get("search_classification"),
        "sql_queries": json.dumps(sql_qs, ensure_ascii=False) if sql_qs else None,
    })
