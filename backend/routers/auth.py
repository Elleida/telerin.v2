"""
Router de autenticación: /api/auth/*
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status

from backend.config import JWT_EXPIRE_HOURS
from backend.dependencies import get_current_admin, get_current_user
from backend.models.schemas import LoginRequest, TokenResponse, UserCreate, UserPublic
from backend.services.auth import (
    authenticate_user,
    create_access_token,
    create_user,
    delete_user,
    ensure_users_table,
    list_users,
    _truncate_password,
)
import traceback

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.on_event("startup")  # type: ignore[attr-defined]  # compat
def _startup():
    ensure_users_table()


# ── Login ──────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    user = authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(
        {"sub": user["id"], "username": user["username"], "role": user["role"]},
        expires_delta=timedelta(hours=JWT_EXPIRE_HOURS),
    )
    return TokenResponse(access_token=token, username=user["username"])


# ── Perfil propio ──────────────────────────────────────────────────────────

@router.get("/me", response_model=UserPublic)
async def me(current_user: dict = Depends(get_current_user)):
    return UserPublic(**{k: v for k, v in current_user.items() if k != "hashed_password"})


# ── CRUD usuarios (solo admin) ─────────────────────────────────────────────

@router.get("/users", response_model=list[UserPublic])
async def get_users(_: dict = Depends(get_current_admin)):
    users = list_users()
    return [UserPublic(**{k: v for k, v in u.items() if k != "hashed_password"}) for u in users]


@router.post("/users", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def create_new_user(body: UserCreate, _: dict = Depends(get_current_admin)):
    try:
        user = create_user(body.username, body.password, body.email, body.role)
        return UserPublic(**user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user(user_id: str, _: dict = Depends(get_current_admin)):
    ok = delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")


# ── Bootstrap: crear primer admin ─────────────────────────────────────────

@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
async def bootstrap(body: UserCreate):
    """
    Crea el primer usuario admin.
    Solo funciona si NO existe ningún usuario en la tabla.
    Proteger en producción (ej. desactivar tras primer uso vía env var).
    """
    users = list_users()
    if users:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ya existen usuarios. Usa el panel de admin para crear más.",
        )
    try:
        # Ensure password is safely truncated to bcrypt limit before hashing
        safe_pw = _truncate_password(body.password)
        user = create_user(body.username, safe_pw, body.email, role="admin")
        return {"message": "Admin creado", "username": user["username"]}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except Exception as exc:
        # Log stacktrace for debugging unexpected errors (temporary)
        print("Exception in /api/auth/bootstrap:")
        traceback.print_exc()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
