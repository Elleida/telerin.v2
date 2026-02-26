"""
Logger JSONL rotativo con compresión gzip para consultas y respuestas del agente.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from backend.config import LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP


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


def log_user_query(query: str, session_id: str | None = None, username: str | None = None) -> None:
    try:
        _query_logger.info(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "user",
            "session_id": session_id,
            "username": username,
            "query": query,
        }, ensure_ascii=False))
    except Exception:
        pass


def log_assistant_response(
    query: str,
    response: str | None,
    session_id: str | None = None,
    username: str | None = None,
    extra: dict | None = None,
) -> None:
    try:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "assistant",
            "session_id": session_id,
            "username": username,
            "query": query,
            "response": response,
        }
        if extra:
            payload.update(extra)
        _query_logger.info(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass
