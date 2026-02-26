"""
Gestión de usuarios en CrateDB + JWT auth.

Tabla CrateDB: telerin_users
  id            TEXT PRIMARY KEY,
  username      TEXT,
  email         TEXT,
  hashed_password TEXT,
  role          TEXT,
  created_at    TIMESTAMP
"""
from __future__ import annotations

import uuid
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth
from jose import jwt, JWTError
from passlib.context import CryptContext

from backend.config import (
    CRATEDB_URL, CRATEDB_USERNAME, CRATEDB_PASSWORD,
    JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRE_HOURS,
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# bcrypt 4.x+ raises ValueError for passwords >72 bytes even during passlib's
# internal "wrap bug" detection test (which intentionally passes 73 bytes).
# Monkeypatch bcrypt.hashpw to silently truncate to 72 bytes so that passlib
# initialises correctly. Our own passwords are truncated before hashing anyway.
try:
    import bcrypt as _bcrypt_mod

    # Provide __about__ metadata if missing (older passlib expects it)
    if not hasattr(_bcrypt_mod, "__about__"):
        class _About:
            __version__ = "unknown"
        _bcrypt_mod.__about__ = _About()

    # Wrap hashpw to truncate passwords >72 bytes (bcrypt hard limit)
    _orig_hashpw = _bcrypt_mod.hashpw

    def _safe_hashpw(password: bytes, salt: bytes) -> bytes:
        if isinstance(password, str):
            password = password.encode("utf-8")
        if len(password) > 72:
            password = password[:72]
        return _orig_hashpw(password, salt)

    _bcrypt_mod.hashpw = _safe_hashpw
except Exception:
    pass


def _truncate_password(pw: str) -> str:
    """Trunca la contraseña a 72 bytes usando UTF-8 (bcrypt limit).
    Decodifica ignorando bytes parciales para mantener un str válido.
    """
    if not isinstance(pw, str):
        pw = str(pw)
    b = pw.encode("utf-8")[:72]
    return b.decode("utf-8", errors="ignore")

# ── Helpers CrateDB ────────────────────────────────────────────────────────

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


def ensure_users_table() -> None:
    """Crea la tabla de usuarios si no existe."""
    _cratedb("""
        CREATE TABLE IF NOT EXISTS telerin_users (
            id            TEXT PRIMARY KEY,
            username      TEXT NOT NULL,
            email         TEXT,
            hashed_password TEXT NOT NULL,
            role          TEXT DEFAULT 'user',
            created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # índice único sobre username
    try:
        _cratedb("CREATE INDEX IF NOT EXISTS idx_telerin_users_username ON telerin_users (username)")
    except Exception:
        pass  # puede que no soporte esta sintaxis en CrateDB; ignorar


# ── CRUD usuarios ──────────────────────────────────────────────────────────

def get_user_by_username(username: str) -> Optional[dict]:
    try:
        result = _cratedb(
            "SELECT id, username, email, hashed_password, role, created_at FROM telerin_users WHERE username = ? LIMIT 1",
            [username],
        )
        rows = result.get("rows", [])
        if not rows:
            return None
        cols = result["cols"]
        return dict(zip(cols, rows[0]))
    except Exception:
        return None


def get_user_by_id(user_id: str) -> Optional[dict]:
    try:
        result = _cratedb(
            "SELECT id, username, email, hashed_password, role, created_at FROM telerin_users WHERE id = ? LIMIT 1",
            [user_id],
        )
        rows = result.get("rows", [])
        if not rows:
            return None
        cols = result["cols"]
        return dict(zip(cols, rows[0]))
    except Exception:
        return None


def create_user(username: str, password: str, email: str | None = None, role: str = "user") -> dict:
    existing = get_user_by_username(username)
    if existing:
        raise ValueError(f"El usuario '{username}' ya existe")

    user_id = str(uuid.uuid4())
    safe_pw = _truncate_password(password)
    hashed = pwd_context.hash(safe_pw)
    _cratedb(
        "INSERT INTO telerin_users (id, username, email, hashed_password, role) VALUES (?, ?, ?, ?, ?)",
        [user_id, username, email, hashed, role],
    )
    return {"id": user_id, "username": username, "email": email, "role": role}


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(_truncate_password(plain), hashed)


def authenticate_user(username: str, password: str) -> Optional[dict]:
    user = get_user_by_username(username)
    if not user:
        return None
    if not verify_password(password, user["hashed_password"]):
        return None
    return user


def list_users() -> list[dict]:
    try:
        result = _cratedb(
            "SELECT id, username, email, role, created_at FROM telerin_users ORDER BY created_at DESC"
        )
        cols = result.get("cols", [])
        return [dict(zip(cols, row)) for row in result.get("rows", [])]
    except Exception:
        return []


def delete_user(user_id: str) -> bool:
    try:
        _cratedb("DELETE FROM telerin_users WHERE id = ?", [user_id])
        return True
    except Exception:
        return False


# ── JWT ────────────────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=JWT_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


def update_password(user_id: str, new_password: str) -> bool:
    """Actualiza la contraseña de un usuario dado su ID."""
    hashed = pwd_context.hash(_truncate_password(new_password))
    try:
        _cratedb(
            "UPDATE telerin_users SET hashed_password = ? WHERE id = ?",
            [hashed, user_id],
        )
        return True
    except Exception:
        return False
