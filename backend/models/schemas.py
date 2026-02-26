"""
Schemas Pydantic compartidos por los routers del backend.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ── Auth ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class UserCreate(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    role: str = "user"  # "user" | "admin"


class UserUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    password: Optional[str] = None  # si se envía, se cambia la contraseña


class UserPublic(BaseModel):
    id: str
    username: str
    email: Optional[str] = None
    role: str
    created_at: Optional[str] = None


# ── Chat ───────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str
    session_id: str
    llm_backend: str = "ollama"
    llm_model: Optional[str] = None
    sql_limit: int = Field(default=60, ge=1, le=200)
    llm_score_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    image_context: Optional[str] = None  # descripción de imagen para enriquecer query


class ChatMessage(BaseModel):
    role: str       # "user" | "assistant"
    content: str


class SourceInfo(BaseModel):
    document: Optional[str] = None
    magazine_id: Optional[str] = None
    page_number: Optional[Any] = None
    title: Optional[str] = None
    date: Optional[str] = None
    score: Optional[float] = None
    similarity: Optional[float] = None
    relevance: Optional[float] = None
    png_url: Optional[str] = None


class SqlQueryInfo(BaseModel):
    table: Optional[str] = None
    sql: Optional[str] = None


class ChatResponse(BaseModel):
    success: bool
    response: Optional[str] = None
    sources: List[SourceInfo] = []
    sql_queries: List[SqlQueryInfo] = []
    prompt_used: Optional[str] = None
    query_type: Optional[str] = None
    search_classification: Optional[str] = None
    enhanced_query: Optional[str] = None
    is_contextual_follow_up: bool = False
    search_time: float = 0.0
    db_search_time: float = 0.0
    reranking_time: float = 0.0
    response_time: float = 0.0
    elapsed_time: float = 0.0
    error: Optional[str] = None


# ── Session ────────────────────────────────────────────────────────────────

class SessionContext(BaseModel):
    num_turns: int
    context_summary: Dict[str, Any] = {}
    global_entities: Dict[str, Any] = {}
    recent_searches: List[Dict[str, Any]] = []
    last_turn: Optional[Dict[str, Any]] = None


# ── Image ──────────────────────────────────────────────────────────────────

class ImageSearchResult(BaseModel):
    id: Optional[str] = None
    magazine_id: Optional[str] = None
    page_number: Optional[Any] = None
    src: Optional[str] = None
    png_url: Optional[str] = None
    description: Optional[str] = None
    caption_literal: Optional[str] = None
    similarity: Optional[float] = None
    relevance_score: Optional[float] = None


class ImageSearchResponse(BaseModel):
    results: List[ImageSearchResult]
    sql_query: Optional[str] = None
    description: Optional[str] = None


class ImageAnalyzeRequest(BaseModel):
    description: str
    results: List[Dict[str, Any]]
    session_id: str
    llm_backend: str = "ollama"
    llm_model: Optional[str] = None
