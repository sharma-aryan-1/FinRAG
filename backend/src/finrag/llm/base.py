"""Provider-agnostic pieces shared by every synthesis backend.

Lives in its own module (not __init__) so provider modules can import the
shared types without a circular import through the dispatcher.

`SynthesisResult` is the contract `main.py` depends on. Every provider maps
its native response/usage onto this shape, so the HTTP layer and the future
LangGraph nodes never learn which model actually answered.
"""

from __future__ import annotations

import datetime
import decimal
from dataclasses import dataclass, field
from typing import Any

from finrag.retrieval.vector import RetrievedChunk

# The grounding contract. Every rule exists because LLMs violate it by default:
#   - "Use ONLY the context" → without this, the model fills gaps from memory
#   - "[N] citations" → without this, citations come back as prose mentions
#   - "Don't round numbers" → without this, $394,328M becomes "about $400 billion"
#   - "Fiscal vs calendar year" → without this, Apple FY2024 (ended Sep 2024)
#     gets conflated with calendar 2024
# Provider-neutral: passed as Anthropic `system=` or Gemini `system_instruction`.
SYSTEM_PROMPT = """You are a financial analyst assistant answering questions about SEC 10-K filings.

Rules you must follow:

1. **Grounding**: Answer ONLY using the provided context chunks. If the context does not contain the answer, say so explicitly — do not fill gaps from prior knowledge.

2. **Citations**: Every factual claim must be followed by a citation in the form [N] where N is the chunk index from the provided context. Multiple supporting chunks: [1][3]. Cite even when paraphrasing.

3. **Numbers**: Quote exact figures from the source. Do not round unless explicitly asked. If a chunk says "$394,328 million", write "$394,328 million" — not "$394 billion".

4. **Fiscal year**: Be careful with fiscal vs calendar year. Apple's fiscal year ends in late September; Tesla and JPMorgan use calendar years. If the user says "2023", confirm which sense from context.

5. **Brevity**: Match the question's scope. A "what was X" question gets one number with a citation. A "how did X change" question gets a comparison sentence. Do not over-explain.

6. **Honest absence**: If the context doesn't answer the question, write "The provided context does not contain this information." Do not speculate.
"""

MAX_TOKENS = 1024


@dataclass
class SynthesisResult:
    """What synthesis returns, regardless of provider.

    The cache token fields stay in the contract even for providers that
    don't surface caching at this scale (both Anthropic <1024-token prompts
    and Gemini's <~1024-token prefixes report 0) — keeping them lets the
    frontend and eval harness read one stable shape.
    """

    answer: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int  # tokens billed at cache-write rate
    cache_read_input_tokens: int      # tokens served from cache (~10% cost)
    stop_reason: str


def format_chunks_for_prompt(chunks: list[RetrievedChunk]) -> str:
    """Format retrieved chunks with [N] anchors and provenance headers.

    The [N] anchor at the start of each chunk is what the model references in
    its citations. Position is 1-based to match human-reading convention.
    """
    blocks: list[str] = []
    for i, c in enumerate(chunks, start=1):
        section = c.section_title or "unknown section"
        # Mark table chunks so the model treats them as structured data and
        # doesn't hallucinate cell positions.
        kind_marker = " [table]" if c.chunk_type == "table" else ""
        header = f"[{i}] {c.ticker} · FY{c.fiscal_year} · {section}{kind_marker}"
        blocks.append(f"{header}\n{c.text}")
    return "\n\n---\n\n".join(blocks)


@dataclass
class ToolCall:
    """One tool invocation inside the agent loop, captured for the trace."""

    tool: str
    args: dict[str, Any]
    result: Any


@dataclass
class ToolLoopResult:
    """Provider-neutral result of an agentic tool-calling loop.

    Both the Gemini (native function-calling) and Anthropic (tool_use)
    implementations return this shape, so the agent node never learns which
    backend ran the loop — the same seam idea as SynthesisResult.
    """

    answer: str
    input_tokens: int
    output_tokens: int
    tool_calls: list[ToolCall] = field(default_factory=list)


def json_safe(v: Any) -> Any:
    """Coerce a tool result into JSON-serializable form for tool responses.

    DuckDB rows can carry date/Decimal values; both providers' tool-result
    channels want JSON scalars, so we stringify dates and float-ify Decimals.
    """
    if isinstance(v, dict):
        return {k: json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [json_safe(x) for x in v]
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


def build_user_message(question: str, chunks: list[RetrievedChunk]) -> str:
    """The per-query content: question + formatted context. Never cached."""
    return f"Question: {question}\n\nContext:\n\n{format_chunks_for_prompt(chunks)}"


def empty_result(model: str) -> SynthesisResult:
    """Returned when retrieval found nothing — skip the paid API call entirely."""
    return SynthesisResult(
        answer="No relevant context retrieved. Try rephrasing your question.",
        model=model,
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        stop_reason="empty_context",
    )
