"""
Dependencias FastAPI reutilizables (auth, current_user, etc.).
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.services.auth import decode_token, get_user_by_id

_bearer_scheme = HTTPBearer(auto_error=True)


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> dict:
    """Extrae y verifica el JWT; devuelve el dict de usuario."""
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido o expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_token(token)
    if payload is None:
        raise credentials_exception

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise credentials_exception

    user = get_user_by_id(user_id)
    if user is None:
        raise credentials_exception

    return user


async def get_current_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Se requiere rol admin")
    return current_user
