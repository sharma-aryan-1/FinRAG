"""lookup_citation(chunk_id) — re-fetch one full chunk from Qdrant by id.

Why the agent needs this: retrieval hands the model a working set, but during
reasoning it may want to pull a specific chunk back in full — to quote an exact
figure, or to re-read a chunk it cited earlier in a longer tool loop. This is
the read-only "dereference a citation" primitive.

Thin wrapper over retrieval.vector.retrieve_by_chunk_ids — no new Qdrant
plumbing, just the single-id ergonomics and a not-found path that returns data
rather than raising (so the agent can recover).
"""

from __future__ import annotations

from typing import Any

from finrag.retrieval.vector import payload_to_chunk, retrieve_by_chunk_ids


def lookup_citation(chunk_id: str) -> dict[str, Any]:
    """Return the full chunk for `chunk_id`, or an error dict if absent.

    The score is 1.0 — it's an exact id fetch, not a similarity match; the
    field exists only to reuse the RetrievedChunk shape the rest of the
    system already speaks.
    """
    payloads = retrieve_by_chunk_ids([chunk_id])
    payload = payloads.get(chunk_id)
    if payload is None:
        return {"error": f"No chunk found with id {chunk_id!r}"}
    return {"chunk": payload_to_chunk(payload, score=1.0).model_dump()}


if __name__ == "__main__":
    # A real id from the AAPL FY2023 net-sales table chunk (seen in /answer).
    print(lookup_citation("d899f2e938bec647"))
    print(lookup_citation("deadbeefdeadbeef"))  # not-found path
