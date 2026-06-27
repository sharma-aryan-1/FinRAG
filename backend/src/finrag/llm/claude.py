"""Anthropic (Claude) backend — synthesis, text completion, and the agent
tool-loop via native `tool_use`.

This is the default provider (see config.llm_provider). Claude's tool-calling
is more reliable than flash-lite's (no malformed-call flakiness), which is why
the agent runs on it. Gemini stays fully wired as the alternate (see
[[gemini]]) so the eval harness can A/B either backend on identical retrieval.

Prompt caching earns its keep here: with tool schemas in the request the
cached prefix (tools + system) clears Anthropic's 1024-token floor, so the
repeated calls in a tool-loop hit cache — unlike the tiny /answer prompt.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from functools import lru_cache

import anthropic

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

# Default (eval baseline) is Sonnet; the public deploy sets CLAUDE_MODEL=
# claude-haiku-4-5-20251001 via env. Read through a helper so every call site
# (synthesis, generate_text, tool-loop, stream) picks up the configured model
# live — same pattern as the provider seam.
def _claude_model() -> str:
    return settings.claude_model or "claude-sonnet-4-6"


# Back-compat alias for any module that imported the constant. Note: this binds
# once at import; the live value is _claude_model(). Internal call sites use the
# helper so an env override (prod Haiku) always takes effect.
CLAUDE_MODEL = settings.claude_model or "claude-sonnet-4-6"

# Statuses worth retrying: rate limit, transient server errors, overloaded.
_RETRYABLE_STATUS = {429, 500, 503, 529}


@lru_cache(maxsize=1)
def get_anthropic_client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env, or set "
            "LLM_PROVIDER=gemini to use Gemini instead."
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _messages_create_with_retry(*, retries: int = 5, **kwargs):
    """Single choke-point for Claude calls, with backoff on rate-limit /
    overloaded / transient 5xx, honoring Retry-After when present."""
    last: Exception | None = None
    for i in range(retries):
        try:
            return get_anthropic_client().messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            status = getattr(e, "status_code", None)
            if status in _RETRYABLE_STATUS and i < retries - 1:
                last = e
                delay = 2.0 * (i + 1)
                try:
                    ra = e.response.headers.get("retry-after")
                    if ra:
                        delay = min(float(ra) + 1.0, 35.0)
                except Exception:
                    pass
                time.sleep(delay)
                continue
            raise
    raise last  # type: ignore[misc]


def _cached_system(text: str) -> list[dict]:
    """System block marked for prompt caching (ephemeral, 5-min TTL)."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def generate_text(
    system_instruction: str,
    user_text: str,
    *,
    max_output_tokens: int = 512,
    temperature: float = 0.0,
) -> str:
    """Single-shot text completion (planning, NL→SQL). Mirrors the Gemini
    backend's generate_text so the dispatcher can pick either."""
    resp = _messages_create_with_retry(
        model=_claude_model(),
        max_tokens=max_output_tokens,
        system=system_instruction,
        messages=[{"role": "user", "content": user_text}],
        temperature=temperature,
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def synthesize_claude(question: str, chunks: list[RetrievedChunk]) -> SynthesisResult:
    if not chunks:
        return empty_result(CLAUDE_MODEL)

    response = _messages_create_with_retry(
        model=_claude_model(),
        max_tokens=MAX_TOKENS,
        system=_cached_system(SYSTEM_PROMPT),
        messages=[{"role": "user", "content": build_user_message(question, chunks)}],
    )
    answer_text = "".join(b.text for b in response.content if b.type == "text")
    usage = response.usage
    return SynthesisResult(
        answer=answer_text,
        model=_claude_model(),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        stop_reason=response.stop_reason or "unknown",
    )


def _anthropic_tools() -> list[dict]:
    """ToolSpec registry → Anthropic tool schema. The ToolSpec.parameters are
    already JSON-schema, which is exactly Anthropic's `input_schema` shape."""
    from finrag.tools import TOOL_SPECS  # lazy: avoid llm↔tools import cycle

    return [
        {"name": s.name, "description": s.description, "input_schema": s.parameters}
        for s in TOOL_SPECS
    ]


def tool_loop(
    system: str,
    user_text: str,
    *,
    max_tokens: int = 1024,
    max_iters: int = 5,
) -> ToolLoopResult:
    """Run Claude with tools until it stops requesting them (or max_iters).

    The tools+system prefix is cache_control'd, so each loop turn re-reads the
    big prefix from cache instead of re-billing it at full rate.
    """
    from finrag.tools import dispatch  # lazy: avoid llm↔tools import cycle

    tools = _anthropic_tools()
    messages: list[dict] = [{"role": "user", "content": user_text}]
    in_tok = out_tok = 0
    calls: list[ToolCall] = []
    answer = ""

    for _ in range(max_iters):
        resp = _messages_create_with_retry(
            model=_claude_model(),
            max_tokens=max_tokens,
            system=_cached_system(system),
            tools=tools,
            messages=messages,
            temperature=0.0,
        )
        in_tok += resp.usage.input_tokens
        out_tok += resp.usage.output_tokens

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results: list[dict] = []
            for block in resp.content:
                if block.type == "tool_use":
                    result = json_safe(dispatch(block.name, dict(block.input)))
                    calls.append(ToolCall(block.name, dict(block.input), result))
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        }
                    )
            messages.append({"role": "user", "content": tool_results})
            continue

        answer = "".join(b.text for b in resp.content if b.type == "text")
        break

    return ToolLoopResult(answer=answer, input_tokens=in_tok, output_tokens=out_tok, tool_calls=calls)


def _stream_one_turn(*, tools, messages, system, max_tokens, on_text, retries: int = 5):
    """Open one streaming Claude turn: forward text deltas to `on_text` as they
    arrive, then return the fully-assembled Message (content blocks + usage).

    Retries only on a retryable status raised *before* any text was emitted — a
    mid-stream restart would re-send tokens the client already saw."""
    last: Exception | None = None
    for i in range(retries):
        emitted = False
        try:
            with get_anthropic_client().messages.stream(
                model=_claude_model(),
                max_tokens=max_tokens,
                system=system,
                tools=tools,
                messages=messages,
                temperature=0.0,
            ) as stream:
                for text in stream.text_stream:
                    emitted = True
                    on_text(text)
                return stream.get_final_message()
        except anthropic.APIStatusError as e:
            status = getattr(e, "status_code", None)
            if status in _RETRYABLE_STATUS and i < retries - 1 and not emitted:
                last = e
                delay = 2.0 * (i + 1)
                try:
                    ra = e.response.headers.get("retry-after")
                    if ra:
                        delay = min(float(ra) + 1.0, 35.0)
                except Exception:
                    pass
                time.sleep(delay)
                continue
            raise
    raise last  # type: ignore[misc]


def tool_loop_stream(
    system: str,
    user_text: str,
    *,
    max_tokens: int = 1024,
    max_iters: int = 5,
    on_text: Callable[[str], None] = lambda _t: None,
    on_tool_call: Callable[[ToolCall], None] = lambda _c: None,
) -> ToolLoopResult:
    """Streaming twin of `tool_loop`: identical control flow, but each turn is
    consumed via the streaming API so the final answer's text reaches `on_text`
    delta-by-delta, and each dispatched tool hits `on_tool_call` the moment it
    runs (not just at the end). Returns the same ToolLoopResult, so the caller's
    trace/usage handling is unchanged whether it streamed or not.

    Note: `on_text` fires for any text a turn emits. With this agent's prompt at
    temperature 0 the tool_use turns carry no preamble, so in practice on_text
    only sees the final answer; the authoritative answer is still the returned
    ToolLoopResult.answer (the last turn's text), not the streamed concatenation."""
    from finrag.tools import dispatch  # lazy: avoid llm↔tools import cycle

    tools = _anthropic_tools()
    cached_system = _cached_system(system)
    messages: list[dict] = [{"role": "user", "content": user_text}]
    in_tok = out_tok = 0
    calls: list[ToolCall] = []
    answer = ""

    for _ in range(max_iters):
        final = _stream_one_turn(
            tools=tools,
            messages=messages,
            system=cached_system,
            max_tokens=max_tokens,
            on_text=on_text,
        )
        in_tok += final.usage.input_tokens
        out_tok += final.usage.output_tokens

        if final.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": final.content})
            tool_results: list[dict] = []
            for block in final.content:
                if block.type == "tool_use":
                    result = json_safe(dispatch(block.name, dict(block.input)))
                    tc = ToolCall(block.name, dict(block.input), result)
                    calls.append(tc)
                    on_tool_call(tc)  # surface live, before the next turn runs
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        }
                    )
            messages.append({"role": "user", "content": tool_results})
            continue

        answer = "".join(b.text for b in final.content if b.type == "text")
        break

    return ToolLoopResult(answer=answer, input_tokens=in_tok, output_tokens=out_tok, tool_calls=calls)
