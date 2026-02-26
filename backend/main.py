"""
FastAPI main — TeleRadio Multi-Agent Backend (telerin.v2)
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.config import CORS_ORIGINS
from backend.services.auth import ensure_users_table
from backend.routers import auth, chat, feedback, image, session

app = FastAPI(
    title="TeleRadio Multi-Agent API",
    description="Backend FastAPI para el sistema multi-agente TELERÍN (telerin.v2)",
    version="2.0.0",
)

# ── CORS ───────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(feedback.router)
app.include_router(image.router)
app.include_router(session.router)

# ── Imágenes estáticas ─────────────────────────────────────────────────────
_PNG_DIR = os.getenv("PNG_BASE_DIR", "").strip()
if _PNG_DIR and os.path.isdir(_PNG_DIR):
    app.mount("/images", StaticFiles(directory=_PNG_DIR), name="images")
    print(f"📁 Sirviendo imágenes desde {_PNG_DIR} en /images")
else:
    print(f"⚠️  PNG_BASE_DIR no configurado o no existe: '{_PNG_DIR}'")


# ── Startup ────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def on_startup():
    ensure_users_table()
    print("✅ TeleRadio Backend v2.0 arrancado")


# ── Health ─────────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


# ── Entrypoint ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
