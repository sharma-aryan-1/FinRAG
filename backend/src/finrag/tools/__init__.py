"""Agent tools — provider-neutral registry.

Each tool is a plain Python function (independently testable) plus a `ToolSpec`
describing its name, when-to-use text, and JSON-schema parameters. The spec is
deliberately NOT in any vendor's tool format: Decision 16 adapts these into
LangChain/LangGraph tools (which bind to Gemini or Claude alike), keeping the
provider seam from Decision 14 intact. Hardwiring Anthropic's tool_use shape
here — as the original handoff assumed — would have undone that.

`dispatch(name, args)` is the single call site the agent loop uses to run a
tool by name; it returns the tool's dict result unchanged.

Three tools, by design — see [[project_finrag_overview]]:
  calculator       — arithmetic, safe-eval        (pure fn)
  lookup_citation  — re-fetch a chunk from Qdrant  (read-only)
  sql_query        — NL → SQL over DuckDB facts     (sub-LLM; added next)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from finrag.tools.calculator import calculator
from finrag.tools.citation import lookup_citation
from finrag.tools.sql import sql_query


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    # JSON-schema "object" describing the args. Both Gemini and Anthropic
    # accept this shape (modulo a thin adapter), as does LangChain.
    parameters: dict[str, Any]
    fn: Callable[..., dict[str, Any]]


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="calculator",
        description=(
            "Evaluate an arithmetic expression over numeric literals "
            "(+ - * / // % ** and parentheses). Use for growth rates, margins, "
            "sums, and ratios instead of doing mental math. Extract the numbers "
            "from context first, then pass an expression like "
            "'(383285 - 394328) / 394328 * 100'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Arithmetic expression over numeric literals only.",
                }
            },
            "required": ["expression"],
        },
        fn=calculator,
    ),
    ToolSpec(
        name="lookup_citation",
        description=(
            "Re-fetch the full text and provenance of a single retrieved chunk "
            "by its chunk_id. Use when you need to quote an exact figure or "
            "re-read a chunk you cited earlier."
        ),
        parameters={
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "The chunk_id of a previously retrieved chunk.",
                }
            },
            "required": ["chunk_id"],
        },
        fn=lookup_citation,
    ),
    ToolSpec(
        name="sql_query",
        description=(
            "Query exact financial figures (revenue, net income, R&D, margins, "
            "multi-year or cross-company comparisons) from the structured "
            "financial_facts database. Pass a natural-language description of "
            "the numbers you need; SQL is generated and run for you. Prefer this "
            "over reading figures out of text chunks when precision matters."
        ),
        parameters={
            "type": "object",
            "properties": {
                "natural_language": {
                    "type": "string",
                    "description": "Plain-language description of the figures to fetch.",
                }
            },
            "required": ["natural_language"],
        },
        fn=sql_query,
    ),
]

# name → spec, for O(1) dispatch.
TOOL_REGISTRY: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run tool `name` with keyword `args`. Returns the tool's dict result.

    Unknown tool names return an error dict (not a raise) so a hallucinated
    tool call degrades gracefully inside the agent loop.
    """
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return {"error": f"Unknown tool {name!r}. Available: {list(TOOL_REGISTRY)}"}
    try:
        return spec.fn(**args)
    except TypeError as e:
        # Wrong/missing args from the model — surface as data, not a crash.
        return {"error": f"Bad arguments for {name!r}: {e}"}


__all__ = ["ToolSpec", "TOOL_SPECS", "TOOL_REGISTRY", "dispatch"]
