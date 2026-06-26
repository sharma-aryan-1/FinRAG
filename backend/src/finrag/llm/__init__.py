"""LLM layer — provider-agnostic entry point.

`main.py`, the tools, and the LangGraph nodes import only these dispatchers and
the neutral result types; they never name a provider. Which backend runs is
decided by `settings.llm_provider` ("anthropic" default | "gemini" | "local").

Provider modules are imported lazily inside each dispatcher so a deploy with
only one provider's SDK/key still works.
"""

from __future__ import annotations

from collections.abc import Callable

from finrag.config import settings
from finrag.llm.base import SynthesisResult, ToolCall, ToolLoopResult
from finrag.retrieval.vector import RetrievedChunk

__all__ = [
    "synthesize",
    "generate_text",
    "run_tool_loop",
    "run_tool_loop_stream",
    "SynthesisResult",
    "ToolLoopResult",
    "ToolCall",
]


def _provider() -> str:
    return (settings.llm_provider or "anthropic").lower()


def synthesize(question: str, chunks: list[RetrievedChunk]) -> SynthesisResult:
    if _provider() == "gemini":
        from finrag.llm.gemini import synthesize_gemini

        return synthesize_gemini(question, chunks)
    if _provider() == "local":
        from finrag.llm.local import synthesize_local

        return synthesize_local(question, chunks)
    from finrag.llm.claude import synthesize_claude

    return synthesize_claude(question, chunks)


def generate_text(system_instruction: str, user_text: str, **kwargs) -> str:
    """Single-shot text completion (planning, NL→SQL)."""
    if _provider() == "gemini":
        from finrag.llm.gemini import generate_text as _gt

        return _gt(system_instruction, user_text, **kwargs)
    if _provider() == "local":
        from finrag.llm.local import generate_text as _gt

        return _gt(system_instruction, user_text, **kwargs)
    from finrag.llm.claude import generate_text as _gt

    return _gt(system_instruction, user_text, **kwargs)


def run_tool_loop(system: str, user_text: str, **kwargs) -> ToolLoopResult:
    """Agentic tool-calling loop — Gemini function-calling or Claude tool_use."""
    if _provider() == "gemini":
        from finrag.llm.gemini import tool_loop

        return tool_loop(system, user_text, **kwargs)
    if _provider() == "local":
        from finrag.llm.local import tool_loop

        return tool_loop(system, user_text, **kwargs)
    from finrag.llm.claude import tool_loop

    return tool_loop(system, user_text, **kwargs)


def run_tool_loop_stream(
    system: str,
    user_text: str,
    *,
    on_text: Callable[[str], None] = lambda _t: None,
    on_tool_call: Callable[[ToolCall], None] = lambda _c: None,
    **kwargs,
) -> ToolLoopResult:
    """Streaming agentic loop: emits text deltas (`on_text`) and live tool calls
    (`on_tool_call`) as they happen, returning the same ToolLoopResult.

    Only Claude implements true streaming. Gemini and the local backend stay
    non-streaming alternates, so we run their plain loop and replay the result
    through the callbacks once — the seam stays intact, the live demo just isn't
    granular."""
    if _provider() in ("gemini", "local"):
        if _provider() == "local":
            from finrag.llm.local import tool_loop
        else:
            from finrag.llm.gemini import tool_loop

        result = tool_loop(system, user_text, **kwargs)
        for tc in result.tool_calls:
            on_tool_call(tc)
        if result.answer:
            on_text(result.answer)
        return result
    from finrag.llm.claude import tool_loop_stream

    return tool_loop_stream(
        system, user_text, on_text=on_text, on_tool_call=on_tool_call, **kwargs
    )
