"""Scoring for the Day-4 eval — two layers.

Deterministic (no LLM, cheap, objective):
  - number matching: does the answer state the ground-truth figure? Robust to
    formatting ($96.995 billion / $96,995 million / 96.995B / -2.8%).
  - citation validity: are the [N] anchors in the answer real (1..n_chunks)?
  - refusal detection: keyword backstop for the honesty tier.

LLM-judge (Claude, always — even when the *system under test* is Gemini, so the
grader is held constant across the A/B; self-preference bias is named in the
writeup):
  - faithfulness: is every claim grounded in the provided context/tool results?
  - answer relevance: does the answer actually address the question?
  - context precision: what fraction of retrieved chunks are relevant?
  - refusal judgement: did the agent appropriately decline?

The judge is deliberately pinned to claude.generate_text (not the provider
dispatcher) so the eval never grades an answer with the same model that wrote it
when that model is the variable under test.
"""

from __future__ import annotations

import json
import re

from finrag.llm.claude import generate_text as _claude_generate

# ── Deterministic: number extraction ─────────────────────────────────────
_SCALE = {
    "trillion": 1e12, "tn": 1e12,
    "billion": 1e9, "bn": 1e9,
    "million": 1e6, "mn": 1e6,
    "thousand": 1e3,
}
# $-led or scale-worded magnitudes. Plain bare integers (e.g. "2023") are NOT
# matched as currency — they must carry a $ or a scale word to count.
_CURRENCY_RE = re.compile(
    r"\$\s?(-?\d[\d,]*(?:\.\d+)?)\s*(trillion|billion|million|thousand|tn|bn|mn)?"
    r"|(-?\d[\d,]*(?:\.\d+)?)\s+(trillion|billion|million|thousand)",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:%|percent)", re.IGNORECASE)
_DECIMAL_RE = re.compile(r"-?\d+\.\d+")


def extract_currency(text: str) -> list[float]:
    """All monetary magnitudes in `text`, normalized to absolute dollars."""
    out: list[float] = []
    for m in _CURRENCY_RE.finditer(text):
        num = m.group(1) or m.group(3)
        scale = (m.group(2) or m.group(4) or "").lower()
        if num is None:
            continue
        try:
            val = float(num.replace(",", ""))
        except ValueError:
            continue
        out.append(val * _SCALE.get(scale, 1.0))
    return out


def extract_percentages(text: str) -> list[float]:
    return [float(m.group(1)) for m in _PERCENT_RE.finditer(text)]


def extract_decimals(text: str) -> list[float]:
    return [float(m.group(0)) for m in _DECIMAL_RE.finditer(text)]


def number_hit(expected: float, kind: str, text: str, tol: float) -> bool:
    """Does `text` state `expected`? `tol` is a relative tolerance for currency
    and per-share; for percent it's the absolute pp floor, OR'd with a 5%
    relative band so both '44%' and '44.13%' match a 44.13 ground truth."""
    if kind == "currency":
        band = abs(expected) * tol
        return any(abs(c - expected) <= band for c in extract_currency(text))
    if kind == "per_share":
        band = max(abs(expected) * tol, 0.01)
        cands = extract_decimals(text) + extract_currency(text)
        return any(abs(c - expected) <= band for c in cands)
    if kind == "percent":
        band = max(tol, abs(expected) * 0.05)
        return any(abs(abs(c) - abs(expected)) <= band for c in extract_percentages(text))
    raise ValueError(f"unknown gt_kind {kind!r}")


# ── Deterministic: citations ──────────────────────────────────────────────
_CITE_RE = re.compile(r"\[(\d+)\]")


def citations(text: str) -> list[int]:
    return [int(m.group(1)) for m in _CITE_RE.finditer(text)]


def citation_validity(text: str, n_chunks: int) -> tuple[bool, list[int]]:
    """True if the answer cites at least one chunk and every [N] is in range.
    Returns (valid, out_of_range_anchors)."""
    cites = citations(text)
    if not cites:
        return False, []
    bad = [c for c in cites if c < 1 or c > n_chunks]
    return (not bad), bad


# ── Deterministic: refusal backstop ───────────────────────────────────────
_REFUSAL_MARKERS = (
    "not in the corpus", "not in the provided", "isn't in the corpus",
    "is not in the corpus", "not available in", "cannot answer", "can't answer",
    "do not have", "don't have", "not included in", "no information",
    "not contain", "outside the", "not part of the corpus", "unable to",
    "not found in", "not covered",
)


def looks_like_refusal(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _REFUSAL_MARKERS)


# ── LLM judge ─────────────────────────────────────────────────────────────
def _judge_json(system: str, user: str, *, max_tokens: int = 400) -> dict:
    """Call the Claude judge and parse its JSON verdict. Tolerant of code-fence
    wrapping; returns {} on parse failure so a flaky judge degrades to a missing
    score rather than crashing the whole run."""
    raw = _claude_generate(system, user, max_output_tokens=max_tokens, temperature=0.0)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


_FAITH_SYS = (
    "You are a meticulous evaluator of financial question-answering. You are given "
    "CONTEXT (retrieved filing excerpts and tool results) and an ANSWER. Judge "
    "ONLY whether each factual claim in the ANSWER is supported by the CONTEXT — "
    "not whether it is true in general. Ignore citation markers like [1]. "
    'Reply with ONLY JSON: {"faithfulness": <float 0..1>, '
    '"unsupported_claims": [<short strings>], "reason": "<one sentence>"}. '
    "faithfulness is the fraction of claims supported by the context."
)

_RELEVANCE_SYS = (
    "You evaluate whether an ANSWER addresses the QUESTION asked (relevance, not "
    "correctness). A direct, on-topic answer scores 1.0; a partial or evasive one "
    "lower; an off-topic one near 0. A correct refusal for an unanswerable "
    "question is fully relevant (1.0). "
    'Reply with ONLY JSON: {"relevance": <float 0..1>, "reason": "<one sentence>"}.'
)

_PRECISION_SYS = (
    "You judge retrieval quality. Given a QUESTION and a numbered list of "
    "retrieved CHUNKS (by index), decide which chunks are relevant to answering "
    "the question. "
    'Reply with ONLY JSON: {"relevant_indices": [<int>], "reason": "<one sentence>"}.'
)

_REFUSAL_SYS = (
    "A QA system should DECLINE this question because the answer is NOT in its "
    "corpus (the company, year, or forward-looking figure is unavailable). Given "
    "the ANSWER, decide whether it appropriately declined / stated it cannot "
    "answer from the available filings, rather than fabricating a figure. "
    'Reply with ONLY JSON: {"declined": <true|false>, "reason": "<one sentence>"}.'
)


def judge_faithfulness(context: str, answer: str) -> dict:
    return _judge_json(_FAITH_SYS, f"CONTEXT:\n{context}\n\nANSWER:\n{answer}")


def judge_relevance(question: str, answer: str) -> dict:
    return _judge_json(_RELEVANCE_SYS, f"QUESTION:\n{question}\n\nANSWER:\n{answer}")


def judge_precision(question: str, chunks_listing: str) -> dict:
    return _judge_json(_PRECISION_SYS, f"QUESTION:\n{question}\n\nCHUNKS:\n{chunks_listing}")


def judge_refusal(question: str, answer: str) -> dict:
    return _judge_json(_REFUSAL_SYS, f"QUESTION:\n{question}\n\nANSWER:\n{answer}")
