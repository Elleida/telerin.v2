"""
Router de estadísticas: GET /api/stats
Lee el feedback.log y devuelve métricas de uso agregadas.
Solo accesible para administradores.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.dependencies import get_current_admin

router = APIRouter(prefix="/api/stats", tags=["stats"])

_LOG_PATH = Path(os.getenv("FEEDBACK_LOG_PATH", "/app/logs/feedback.log"))
_FALLBACK_LOG_PATH = Path("/tmp/feedback.log")


def _read_entries() -> list[dict]:
    for path in [_LOG_PATH, _FALLBACK_LOG_PATH]:
        try:
            if path.exists():
                entries = []
                with path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
                return entries
        except Exception:
            pass
    return []


class DayStat(BaseModel):
    date: str
    count: int
    up: int
    down: int


class UserStat(BaseModel):
    username: str
    count: int
    up: int
    down: int
    avg_response_s: float


class RecentEntry(BaseModel):
    ts: str
    user: str
    rating: str
    query: str
    num_sources: int
    total_s: float
    llm_model: str = ""
    comment: str = ""


class StatsResponse(BaseModel):
    total: int
    up: int
    down: int
    avg_db_search_s: float
    avg_reranking_s: float
    avg_response_s: float
    avg_total_s: float
    avg_num_sources: float
    avg_prompt_tokens: float
    avg_response_tokens: float
    by_day: list[DayStat]
    by_user: list[UserStat]
    recent: list[RecentEntry]


@router.get("", response_model=StatsResponse)
async def get_stats(_: dict = Depends(get_current_admin)):
    entries = _read_entries()

    total = len(entries)
    up = sum(1 for e in entries if e.get("rating") == "up")
    down = total - up

    # Timings aggregation
    def avg(vals):
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    db_times      = [e["timings"]["db_search_s"]  for e in entries if "timings" in e]
    rer_times     = [e["timings"]["reranking_s"]  for e in entries if "timings" in e]
    resp_times    = [e["timings"]["response_s"]   for e in entries if "timings" in e]
    tot_times     = [e["timings"]["total_s"]      for e in entries if "timings" in e]
    sources       = [e.get("num_sources", 0)      for e in entries]
    prompt_tokens = [e["prompt_tokens"]   for e in entries if e.get("prompt_tokens")]
    resp_tokens   = [e["response_tokens"] for e in entries if e.get("response_tokens")]

    # By day (last 30 days)
    day_counts: dict[str, dict] = defaultdict(lambda: {"count": 0, "up": 0, "down": 0})
    for e in entries:
        day = e.get("ts", "")[:10]
        if day:
            day_counts[day]["count"] += 1
            if e.get("rating") == "up":
                day_counts[day]["up"] += 1
            else:
                day_counts[day]["down"] += 1
    by_day = sorted(
        [DayStat(date=d, **v) for d, v in day_counts.items()],
        key=lambda x: x.date,
    )[-30:]

    # By user
    user_data: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "up": 0, "down": 0, "resp_times": []}
    )
    for e in entries:
        u = e.get("user", "unknown")
        user_data[u]["count"] += 1
        if e.get("rating") == "up":
            user_data[u]["up"] += 1
        else:
            user_data[u]["down"] += 1
        if "timings" in e:
            user_data[u]["resp_times"].append(e["timings"]["response_s"])

    by_user = sorted(
        [
            UserStat(
                username=u,
                count=v["count"],
                up=v["up"],
                down=v["down"],
                avg_response_s=avg(v["resp_times"]),
            )
            for u, v in user_data.items()
        ],
        key=lambda x: x.count,
        reverse=True,
    )

    # Recent 20 entries (newest first)
    recent = [
        RecentEntry(
            ts=e.get("ts", ""),
            user=e.get("user", ""),
            rating=e.get("rating", ""),
            query=e.get("query", "")[:120],
            num_sources=e.get("num_sources", 0),
            total_s=e.get("timings", {}).get("total_s", 0.0),
            llm_model=e.get("llm_model", ""),
            comment=e.get("comment", ""),
        )
        for e in reversed(entries[-20:])
    ]

    return StatsResponse(
        total=total,
        up=up,
        down=down,
        avg_db_search_s=avg(db_times),
        avg_reranking_s=avg(rer_times),
        avg_response_s=avg(resp_times),
        avg_total_s=avg(tot_times),
        avg_num_sources=avg(sources),
        avg_prompt_tokens=avg(prompt_tokens),
        avg_response_tokens=avg(resp_tokens),
        by_day=by_day,
        by_user=by_user,
        recent=recent,
    )
