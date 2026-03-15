"""
Router de chat vía WebSocket: ws://host/ws/chat

Protocolo de mensajes (JSON):

  Cliente → Servidor:
    { "type": "auth",  "token": "<jwt>" }          # primera vez
    { "type": "chat",  "query": "...",
      "llm_backend": "ollama"|"gemini",
      "llm_model": null|"nombre",
      "sql_limit": 60,
      "llm_score_threshold": 0.5,
      "image_context": null|"descripción" }

  Servidor → Cliente:
    { "type": "status",   "label": "🔎 Buscando..." }
    { "type": "chunk",    "text": "..." }           # streaming token
    { "type": "final",    "response": "...",
                          "sources": [...],
                          "sql_queries": [...],
                          "prompt_used": "...",
                          "query_type": "...",
                          "enhanced_query": null|"...",
                          "elapsed_time": 1.23,
                          "db_search_time": ...,
                          "reranking_time": ...,
                          "response_time": ... }
    { "type": "error",    "message": "..." }
"""
from __future__ import annotations

import json
import queue
import os
import threading
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.compat.graph import run_graph
from backend.compat.tools import set_sql_results_limit, set_llm_score_threshold, set_chat_history

from backend.services.auth import decode_token, get_user_by_id
from backend.services.session_store import clear_session, get_session, save_session
from backend.services.query_logger import log_user_query, log_assistant_response

router = APIRouter(tags=["chat"])

# Señales de control → etiquetas UI
_STATUS_LABELS = {
    "__CONTEXT_ANALYSIS__": "🧠 Analizando contexto conversacional...",
    "__PLANNING__":          "🤖 Planificando búsqueda...",
    "__DB_SEARCH__":         "🗃️ Buscando en base de datos...",
    "__SEARCH_DONE__":       "🤔 Preparando respuesta...",
    "__GENERATING__":        "✍️ Generando respuesta...",
}


async def _send(ws: WebSocket, msg: dict) -> None:
    try:
        await ws.send_text(json.dumps(msg, ensure_ascii=False))
    except Exception:
        pass


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    await websocket.accept()

    current_user: dict | None = None

    try:
        # ── Paso 1: autenticación ──────────────────────────────────────────
        raw_auth = await websocket.receive_text()
        auth_msg = json.loads(raw_auth)
        print(f"[WS] auth_msg type={auth_msg.get('type')} token_present={bool(auth_msg.get('token'))}")

        if auth_msg.get("type") != "auth":
            await _send(websocket, {"type": "error", "message": "Se esperaba mensaje de auth"})
            await websocket.close(code=4001)
            return

        token = auth_msg.get("token", "")
        payload = decode_token(token)
        print(f"[WS] decode_token payload={payload}")
        if payload is None:
            await _send(websocket, {"type": "error", "message": "Token inválido"})
            await websocket.close(code=4003)
            return

        current_user = get_user_by_id(payload.get("sub", ""))
        print(f"[WS] get_user_by_id={current_user}")
        if current_user is None:
            await _send(websocket, {"type": "error", "message": "Usuario no encontrado"})
            await websocket.close(code=4003)
            return

        await _send(websocket, {"type": "auth_ok", "username": current_user["username"]})

        session_id = current_user["id"]
        print(f"[WS] get_session for session_id={session_id}")
        memory = get_session(session_id)
        print(f"[WS] session ready, entering message loop")

        # ── Paso 2: bucle de mensajes ──────────────────────────────────────
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            if msg.get("type") == "clear":
                clear_session(session_id)
                memory = get_session(session_id)
                await _send(websocket, {"type": "cleared"})
                continue

            if msg.get("type") != "chat":
                await _send(websocket, {"type": "error", "message": f"Tipo desconocido: {msg.get('type')}"})
                continue

            user_query: str = msg.get("query", "").strip()
            if not user_query:
                await _send(websocket, {"type": "error", "message": "query vacía"})
                continue

            llm_backend    = msg.get("llm_backend", "ollama")
            llm_model      = msg.get("llm_model") or None
            sql_limit      = int(msg.get("sql_limit", 60))
            score_threshold = float(msg.get("llm_score_threshold", 0.5))
            image_context  = msg.get("image_context") or None

            # Aplicar ajustes globales
            set_sql_results_limit(sql_limit)
            set_llm_score_threshold(score_threshold)

            # Enriquecer query con contexto de imagen si existe
            query_with_context = user_query
            if image_context:
                query_with_context = (
                    f"Contexto de imagen analizada: {image_context}\n\n"
                    f"Consulta del usuario: {user_query}"
                )

            log_user_query(user_query, session_id, current_user.get("username"))
            await _send(websocket, {"type": "status", "label": "🔎 Buscando información..."})

            # ── Ejecutar run_graph en hilo (no bloquea event loop asyncio) ──
            chunk_queue: queue.Queue[Any] = queue.Queue()
            result_container: dict = {}

            def _stream_handler(chunk):
                try:
                    chunk_queue.put_nowait(chunk)
                except Exception:
                    pass

            def _bg(mem=memory):
                try:
                    # Capturamos `mem` como argumento por defecto para que este hilo
                    # use siempre el objeto ConversationMemory de ESTA query, sin
                    # verse afectado si el usuario pulsa "Limpiar" mientras se ejecuta
                    # (lo que reasignaría `memory` en el scope exterior y, en el viejo
                    # código, provocaba que el hilo viera un objeto ya vaciado o nuevo).
                    recent_turns = mem.get_recent_turns(5) if mem else []
                    set_chat_history(recent_turns)

                    res = run_graph(
                        query_with_context,
                        mem,
                        llm_backend=llm_backend,
                        llm_model=llm_model,
                        stream=True,
                        llm_stream_handler=_stream_handler,
                    )
                    result_container["result"] = res
                except Exception as exc:
                    result_container["result"] = {"success": False, "error": str(exc)}

            bg_thread = threading.Thread(target=_bg, daemon=True)
            bg_thread.start()

            stream_chunks: list[str] = []

            # Drenar la cola mientras el hilo trabaja
            import asyncio
            loop = asyncio.get_event_loop()

            while bg_thread.is_alive() or not chunk_queue.empty():
                try:
                    chunk = chunk_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.05)
                    continue

                if isinstance(chunk, str) and chunk in _STATUS_LABELS:
                    await _send(websocket, {"type": "status", "label": _STATUS_LABELS[chunk]})
                    continue

                if isinstance(chunk, dict):
                    ctype = chunk.get("type")
                    if ctype == "sources":
                        # guardamos en result_container; se enviarán en final
                        res = result_container.get("result", {})
                        res["sources"] = chunk.get("payload") or chunk.get("sources") or []
                        result_container["result"] = res
                        continue
                    elif ctype == "chunk":
                        text = chunk.get("text", "")
                        stream_chunks.append(text)
                        await _send(websocket, {"type": "chunk", "text": text})
                        continue
                    elif ctype == "final":
                        final_text = chunk.get("response", "")
                        stream_chunks = [final_text]
                        continue

                # Chunk de texto plano
                text = str(chunk)
                stream_chunks.append(text)
                await _send(websocket, {"type": "chunk", "text": text})

            bg_thread.join(timeout=5)

            result = result_container.get("result", {})
            if stream_chunks:
                result["response"] = "".join(stream_chunks)

            # ── Enviar mensaje final ──────────────────────────────────────
            final_msg = {
                "type":                   "final",
                "response":               result.get("response", ""),
                "sources":                result.get("sources", []),
                "sql_queries":            result.get("sql_queries", []),
                "prompt_used":            result.get("prompt_used", ""),
                "query_type":             result.get("query_type", ""),
                "search_classification":  result.get("search_classification"),
                "enhanced_query":         result.get("enhanced_query"),
                "is_contextual_follow_up": result.get("is_contextual_follow_up", False),
                "elapsed_time":           result.get("elapsed_time", 0),
                "search_time":            result.get("search_time", 0),
                "db_search_time":         result.get("db_search_time", 0),
                "reranking_time":         result.get("reranking_time", 0),
                "response_time":          result.get("response_time", 0),
                "prompt_tokens":          result.get("prompt_tokens", 0),
                "response_tokens":        result.get("response_tokens", 0),
                "error":                  result.get("error"),
                "success":                result.get("success", True),
            }
            await _send(websocket, final_msg)

            log_assistant_response(
                user_query,
                result.get("response"),
                session_id,
                current_user.get("username"),
                extra={
                    "elapsed_time":        result.get("elapsed_time"),
                    "search_time":         result.get("search_time"),
                    "response_time":       result.get("response_time"),
                    "query_type":          result.get("query_type"),
                    "search_classification": result.get("search_classification"),
                    "sql_queries":         result.get("sql_queries"),
                },
            )

            # Persistir sesión en CrateDB (fire-and-forget en el pool de threads
            # para no bloquear el event loop de asyncio)
            loop.run_in_executor(None, lambda mem=memory: save_session(session_id, mem))

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        import traceback
        print(f"[WS] EXCEPTION: {exc}")
        traceback.print_exc()
        try:
            await _send(websocket, {"type": "error", "message": str(exc)})
        except Exception:
            pass
