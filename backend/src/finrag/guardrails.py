"""Cost/abuse guardrails for the public deploy.

The agent endpoints call a funded Anthropic key, so a public URL without limits
is an open invitation to burn money. Two independent defenses, both enforced as
a FastAPI dependency on the paid endpoints (/answer, /agent, /agent/stream):

  1. Per-IP sliding-window rate limit  → stops one client hammering the agent.
  2. Global daily question cap          → the hard cost circuit-breaker; once the
     day's paid questions hit the cap, every further request 429s until midnight
     UTC, no matter who sends it. This is the line between "a few dollars" and
     "a surprise bill."

Both are in-process (a dict + a counter under one lock). That's correct for the
single-instance Fly deploy we target; a multi-instance/multi-worker setup would
need a shared store (Redis) instead — called out in docs/deploy.md, not hidden.

Retrieval-only /query is intentionally NOT guarded here: it makes no LLM call
(Cohere rerank only, ~$0.002) so it isn't the cost risk the cap exists for.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from finrag.config import settings

_WINDOW_SECONDS = 60.0

_lock = threading.Lock()
# ip -> timestamps of accepted paid requests within the last _WINDOW_SECONDS
_hits: dict[str, deque[float]] = defaultdict(deque)
# (utc_date_str, count) — the global daily paid-question tally
_day: str = ""
_day_count: int = 0


def _client_ip(request: Request) -> str:
    """Best-effort real client IP. Behind Fly's proxy the socket peer is the
    proxy, so prefer the forwarded headers Fly/Vercel set; fall back to the
    socket. X-Forwarded-For is a chain — the original client is the first hop."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    fly = request.headers.get("fly-client-ip")
    if fly:
        return fly.strip()
    return request.client.host if request.client else "unknown"


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = now.date().toordinal() + 1
    midnight = datetime.fromordinal(tomorrow).replace(tzinfo=timezone.utc)
    return max(1, int((midnight - now).total_seconds()))


def enforce(request: Request) -> None:
    """FastAPI dependency: raise 429 if this paid request breaches either the
    per-IP rate limit or the global daily cap; otherwise record it and return.

    Recording happens here (not after the call) so an in-flight burst can't slip
    past the cap — we count on admission, which is the conservative choice for a
    cost ceiling."""
    global _day, _day_count
    now = time.monotonic()
    ip = _client_ip(request)

    with _lock:
        # ── global daily cap (reset on UTC date rollover) ──
        today = _today_utc()
        if today != _day:
            _day, _day_count = today, 0
        if _day_count >= settings.daily_question_cap:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Daily demo limit reached. This is a cost-capped public "
                    "demo; it resets at 00:00 UTC. Run it locally for unlimited "
                    "use — see the repo README."
                ),
                headers={"Retry-After": str(_seconds_until_utc_midnight())},
            )

        # ── per-IP sliding window ──
        bucket = _hits[ip]
        cutoff = now - _WINDOW_SECONDS
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= settings.rate_limit_per_min:
            retry = max(1, int(_WINDOW_SECONDS - (now - bucket[0])))
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit: max {settings.rate_limit_per_min} questions/min. "
                    f"Try again in ~{retry}s."
                ),
                headers={"Retry-After": str(retry)},
            )

        # Admitted — record against both limiters.
        bucket.append(now)
        _day_count += 1

        # Opportunistic cleanup so idle IPs don't accumulate forever.
        if len(_hits) > 4096:
            for k in [k for k, v in _hits.items() if not v]:
                del _hits[k]


def cap_status() -> dict[str, int | str]:
    """Snapshot for /health — lets the frontend show 'N questions left today'
    and makes the cap observable without reading logs."""
    with _lock:
        today = _today_utc()
        used = _day_count if today == _day else 0
        cap = settings.daily_question_cap
        return {
            "daily_cap": cap,
            "used_today": used,
            "remaining_today": max(0, cap - used),
            "resets_at": "00:00 UTC",
        }
