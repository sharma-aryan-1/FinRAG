"""Local / edge backend — Llama 3.2 3B via Ollama's OpenAI-compatible API.

This is the third provider behind the seam (see [[gemini]], [[claude]]). It maps
Ollama's responses onto the same `SynthesisResult`/`ToolLoopResult`, so no node,
tool, or graph code learns a local model is answering — `llm_provider="local"`
is the only switch.

Why the OpenAI client (not Ollama's native API): Ollama exposes an
OpenAI-compatible endpoint on :11434/v1, so the *same* code targets vLLM,
llama.cpp, or LM Studio by changing `local_base_url`. That portability is the
point of the edge story — the seam isn't Ollama-specific, it's "any
OpenAI-compatible local server."

The edge reality (the finding this variant exists to produce): a 3B model
retrieves + synthesizes grounded answers fine, but its tool-calling is weak — it
mis-forms or skips function calls that Claude handles reliably. So `tool_loop`
honors `settings.local_use_tools`: True runs the real agentic loop (and we report
how often it misfires); False degrades to synthesis-only over the provided
context, which is what small local models can actually do dependably.

No prompt caching here — local inference has no per-token cost or cache tier, so
the cache fields on SynthesisResult stay 0 (same as Gemini, see [[base]]).
"""

from __future__ import annotations

import json
from functools import lru_cache

from openai import OpenAI

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


@lru_cache(maxsize=1)
def get_local_client() -> OpenAI:
    """OpenAI client pointed at the local Ollama server. The api_key is a
    required-but-ignored placeholder (Ollama doesn't auth). A clear error if the
    daemon isn't up mirrors the missing-key errors on the cloud backends."""
    return OpenAI(base_url=settings.local_base_url, api_key="ollama")


def _model() -> str:
    return settings.local_model


def generate_text(
    system_instruction: str,
    user_text: str,
    *,
    max_output_tokens: int = 512,
    temperature: float = 0.0,
) -> str:
    """Single-shot text completion (planning, NL→SQL). Mirrors the claude/gemini
    backends so the dispatcher can pick any provider."""
    resp = get_local_client().chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_text},
        ],
        max_tokens=max_output_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


def synthesize_local(question: str, chunks: list[RetrievedChunk]) -> SynthesisResult:
    if not chunks:
        return empty_result(_model())

    resp = get_local_client().chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(question, chunks)},
        ],
        max_tokens=MAX_TOKENS,
        temperature=0.0,
    )
    choice = resp.choices[0]
    usage = resp.usage
    return SynthesisResult(
        answer=choice.message.content or "",
        model=_model(),
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        # Local inference has no cache-billing tier; keep both at 0.
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        stop_reason=choice.finish_reason or "unknown",
    )


# ── Agent tool-loop (OpenAI-style function calling) ──────────────────────────
def _openai_tools() -> list[dict]:
    """ToolSpec registry → OpenAI tool schema. ToolSpec.parameters are already
    JSON-schema, which is exactly the `function.parameters` shape."""
    from finrag.tools import TOOL_SPECS  # lazy: avoid llm↔tools import cycle

    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": s.parameters,
            },
        }
        for s in TOOL_SPECS
    ]


def _synthesis_only(system: str, user_text: str, max_tokens: int) -> ToolLoopResult:
    """Degraded path: no tools offered, just grounded synthesis over the context
    already embedded in `user_text`. This is what a 3B model does reliably, so we
    benchmark it as the honest edge finding when tool-calling is off."""
    resp = get_local_client().chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        max_tokens=max_tokens,
        temperature=0.0,
    )
    usage = resp.usage
    return ToolLoopResult(
        answer=resp.choices[0].message.content or "",
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        tool_calls=[],
    )


def tool_loop(
    system: str,
    user_text: str,
    *,
    max_tokens: int = 1024,
    max_iters: int = 5,
) -> ToolLoopResult:
    """Run the local model with tools until it stops requesting them (or
    max_iters). Mirrors claude/gemini tool_loop's signature/return.

    When settings.local_use_tools is False, skip tool-calling entirely and run
    synthesis-only — the documented degraded mode for small models."""
    if not settings.local_use_tools:
        return _synthesis_only(system, user_text, max_tokens)

    from finrag.tools import dispatch  # lazy: avoid llm↔tools import cycle

    tools = _openai_tools()
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]
    in_tok = out_tok = 0
    calls: list[ToolCall] = []
    answer = ""

    for _ in range(max_iters):
        resp = get_local_client().chat.completions.create(
            model=_model(),
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=0.0,
        )
        msg = resp.choices[0].message
        usage = resp.usage
        in_tok += getattr(usage, "prompt_tokens", 0) or 0
        out_tok += getattr(usage, "completion_tokens", 0) or 0

        if msg.tool_calls:
            # Re-send the assistant turn verbatim (content + the tool_calls it
            # requested), then one tool message per call, keyed by tool_call_id.
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )
            for tc in msg.tool_calls:
                # 3B models sometimes emit malformed JSON args — treat as empty
                # rather than crashing the loop (part of the weak-tool-calling story).
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = json_safe(dispatch(tc.function.name, args))
                calls.append(ToolCall(tc.function.name, args, result))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result),
                    }
                )
            continue

        answer = msg.content or ""
        break

    return ToolLoopResult(
        answer=answer, input_tokens=in_tok, output_tokens=out_tok, tool_calls=calls
    )
