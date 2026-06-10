"""Gemini synthesis backend — the default provider.

Uses the unified `google-genai` SDK (NOT the legacy `google-generativeai`).
Same job as claude.py: retrieve → synthesize a citation-grounded answer.
Maps Gemini's `usage_metadata` onto the shared `SynthesisResult`.

Why Gemini 2.5 Flash: the free tier zeroes out dev cost, and plain grounded
synthesis (read chunks, cite [N], don't hallucinate) is an easy workload for
it — this isn't reasoning-heavy. We disable "thinking" (budget=0) because
synthesis needs determinism and speed, not a scratchpad; thinking would just
burn output tokens and latency here.

Caching note: Gemini's implicit context cache only kicks in above a ~1k-token
prefix, and our system prompt is ~450 tokens, so cached_content_token_count
stays 0. That's expected, not a bug — see [[base]] SynthesisResult docstring.
"""

from __future__ import annotations

import re
import time
from functools import lru_cache

from google import genai
from google.genai import types

from finrag.config import settings
from finrag.llm.base import (
    MAX_TOKENS,
    SYSTEM_PROMPT,
    SynthesisResult,
    ToolCall,
    ToolLoopResult,
    build_user_message,
    empty_result,
    json_safe,
)
from finrag.retrieval.vector import RetrievedChunk

# JSON-schema lowercase types → Gemini's uppercase Type enum values.
_TYPE_MAP = {
    "object": "OBJECT", "string": "STRING", "number": "NUMBER",
    "integer": "INTEGER", "boolean": "BOOLEAN", "array": "ARRAY",
}

# flash-lite is the default: the agent makes ~5 calls/question and 2.5-flash's
# free tier caps at only 20 requests/DAY, which an agentic workload exhausts in
# ~4 questions. flash-lite has a far larger free daily quota (~1000/day) and
# 15 req/min — enough to actually run and demo the agent for free. Quality is
# marginally lower but fine for grounded synthesis + mechanical sub-tasks.
# (3.5-flash resolves but 503s constantly on free tier; 2.5-flash is selectable
# by editing this line if billing is enabled.)
GEMINI_MODEL = "gemini-2.5-flash-lite"


@lru_cache(maxsize=1)
def get_gemini_client() -> genai.Client:
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to .env, or set "
            "LLM_PROVIDER=anthropic to use Claude instead."
        )
    return genai.Client(api_key=settings.gemini_api_key)


def _retry_delay(exc: Exception, attempt: int) -> float | None:
    """Seconds to wait before retrying `exc`, or None if it's not retryable.

    Two transient free-tier failures:
      - 503 UNAVAILABLE ("high demand") → linear backoff 2s/4s/6s.
      - 429 RESOURCE_EXHAUSTED (5 req/min cap) → honor the API's suggested
        retryDelay (it tells us exactly when the per-minute window resets),
        with a small buffer and a sane ceiling.
    """
    s = str(exc)
    if "429" in s or "RESOURCE_EXHAUSTED" in s:
        # Per-DAY quota won't reset within any sane wait — fail fast so the
        # caller gets a clear error instead of blocking ~60s for nothing.
        # Only the per-minute cap is worth waiting out.
        if "PerDay" in s or "RequestsPerDay" in s:
            return None
        m = re.search(r"retry in ([0-9.]+)s", s) or re.search(
            r"retryDelay['\"]?:?\s*['\"]?([0-9.]+)s", s
        )
        return min((float(m.group(1)) if m else 20.0) + 1.0, 35.0)
    if "503" in s or "UNAVAILABLE" in s:
        return 2.0 * (attempt + 1)
    return None


def _has_content(response: object) -> bool:
    """True if the response carries at least one usable text/function_call part.

    flash-lite intermittently returns a candidate with no parts (empty
    response), especially on larger tool-laden prompts. Such a response isn't
    an exception, so we detect it explicitly and retry.
    """
    cands = getattr(response, "candidates", None)
    if not cands:
        return False
    cand = cands[0]
    if not cand.content or not cand.content.parts:
        return False
    return any(
        getattr(p, "text", None) or getattr(p, "function_call", None)
        for p in cand.content.parts
    )


def generate_content_with_retry(
    contents: object,
    config: types.GenerateContentConfig,
    *,
    retries: int = 5,
):
    """Single choke-point for Gemini calls, with retry on 503, per-minute 429,
    and empty (zero-part) responses.

    The agent (Decision 16) makes several calls per question; on the free tier
    any one can hit a transient 503, the per-minute 429, or a flash-lite empty
    candidate. Centralizing retry here means synthesis, NL→SQL, planning, and
    the tool-loop all inherit it, so a single blip doesn't abort the graph.
    """
    last: Exception | None = None
    for i in range(retries):
        try:
            response = get_gemini_client().models.generate_content(
                model=GEMINI_MODEL, contents=contents, config=config
            )
        except Exception as e:  # noqa: BLE001 — re-raised unless _retry_delay matches
            delay = _retry_delay(e, i)
            if delay is not None and i < retries - 1:
                last = e
                time.sleep(delay)
                continue
            raise
        # Empty candidate → transient; retry a couple times before giving up.
        if not _has_content(response) and i < retries - 1:
            time.sleep(1.0)
            continue
        return response
    raise last  # type: ignore[misc]


def _config(
    system_instruction: str,
    *,
    tools: list[types.Tool] | None = None,
    max_output_tokens: int = MAX_TOKENS,
    temperature: float = 0.0,
) -> types.GenerateContentConfig:
    """Shared config: thinking disabled (synthesis/routing want determinism)."""
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=tools,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )


def _extract_text(response: object) -> tuple[str, str]:
    """Pull (text, finish_reason) out of a Gemini response defensively.

    If the candidate was blocked (safety) or truncated, `.parts` may be empty;
    `response.text` would warn/raise in that case, so we walk the parts.
    """
    text = ""
    finish_reason = "unknown"
    candidates = getattr(response, "candidates", None)
    if candidates:
        cand = candidates[0]
        finish_reason = str(getattr(cand, "finish_reason", "unknown"))
        if cand.content and cand.content.parts:
            text = "".join(
                p.text for p in cand.content.parts if getattr(p, "text", None)
            )
    return text, finish_reason


def generate_text(
    system_instruction: str,
    user_text: str,
    *,
    max_output_tokens: int = 512,
    temperature: float = 0.0,
) -> str:
    """Single-shot text completion — the building block for sub-LLM tasks
    like NL→SQL, where we want raw text out, not a SynthesisResult.

    (Lives on the Gemini backend for now; if LLM_PROVIDER swaps to Anthropic,
    this is the one helper sql_query would need mirrored in claude.py.)
    """
    response = generate_content_with_retry(
        user_text,
        _config(
            system_instruction,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        ),
    )
    text, _ = _extract_text(response)
    return text


def synthesize_gemini(question: str, chunks: list[RetrievedChunk]) -> SynthesisResult:
    if not chunks:
        return empty_result(GEMINI_MODEL)

    # system_instruction plays the role Anthropic's `system=` does — keeps
    # grounding rules out of the user turn. temperature 0 → deterministic
    # grounded extraction, not creativity.
    response = generate_content_with_retry(
        build_user_message(question, chunks),
        _config(SYSTEM_PROMPT),
    )

    answer_text, finish_reason = _extract_text(response)

    usage = response.usage_metadata
    return SynthesisResult(
        answer=answer_text,
        model=GEMINI_MODEL,
        input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
        output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
        # Gemini doesn't bill a separate cache-write tier the way Anthropic
        # does; implicit caching just reports read tokens. Keep write at 0.
        cache_creation_input_tokens=0,
        cache_read_input_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
        stop_reason=finish_reason,
    )


# ── Agent tool-loop (native function calling) ─────────────────────────────
def _to_schema(js: dict) -> types.Schema:
    """One JSON-schema fragment → a genai Schema (recursively)."""
    schema = types.Schema(type=_TYPE_MAP.get(js.get("type", "object"), "STRING"))
    if "description" in js:
        schema.description = js["description"]
    if js.get("type") == "object":
        schema.properties = {k: _to_schema(v) for k, v in js.get("properties", {}).items()}
        if js.get("required"):
            schema.required = list(js["required"])
    if js.get("type") == "array" and "items" in js:
        schema.items = _to_schema(js["items"])
    return schema


def _gemini_tool() -> types.Tool:
    from finrag.tools import TOOL_SPECS  # lazy: avoid llm↔tools import cycle

    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name=s.name, description=s.description, parameters=_to_schema(s.parameters)
            )
            for s in TOOL_SPECS
        ]
    )


def _args_to_dict(args: object) -> dict:
    """Convert a Gemini function_call.args (proto Map) to a plain dict."""
    def conv(v):
        if hasattr(v, "items"):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [conv(x) for x in v]
        return v

    return conv(args) if args else {}


def tool_loop(
    system: str,
    user_text: str,
    *,
    max_tokens: int = 1024,
    max_iters: int = 5,
) -> ToolLoopResult:
    """Run Gemini with tools until it stops requesting them (or max_iters).
    Mirrors claude.tool_loop's signature/return so the dispatcher can pick."""
    from finrag.tools import dispatch  # lazy: avoid llm↔tools import cycle

    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=[_gemini_tool()],
        max_output_tokens=max_tokens,
        temperature=0.0,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=user_text)])
    ]
    in_tok = out_tok = 0
    calls: list[ToolCall] = []
    answer = ""

    for _ in range(max_iters):
        resp = generate_content_with_retry(contents, config)
        um = resp.usage_metadata
        in_tok += getattr(um, "prompt_token_count", 0) or 0
        out_tok += getattr(um, "candidates_token_count", 0) or 0

        cand = resp.candidates[0] if resp.candidates else None
        if cand is None or not cand.content or not cand.content.parts:
            break
        parts = cand.content.parts
        contents.append(cand.content)

        fcs = [p.function_call for p in parts if getattr(p, "function_call", None)]
        if not fcs:
            answer = "".join(p.text for p in parts if getattr(p, "text", None))
            break

        response_parts: list[types.Part] = []
        for fc in fcs:
            args = _args_to_dict(fc.args)
            result = json_safe(dispatch(fc.name, args))
            calls.append(ToolCall(fc.name, args, result))
            response_parts.append(
                types.Part.from_function_response(name=fc.name, response=result)
            )
        contents.append(types.Content(role="user", parts=response_parts))

    return ToolLoopResult(answer=answer, input_tokens=in_tok, output_tokens=out_tok, tool_calls=calls)
