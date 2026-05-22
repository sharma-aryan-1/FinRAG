"""Parse downloaded 10-K HTML files into chunks ready for embedding.

Pipeline:
    data/raw/{TICKER}_{FY}/filing.htm + metadata.json
        ──▶  partition_html  ──▶  list[Element]
        ──▶  segment by Title, split tables off
        ──▶  chunk_by_title within each section (narrative)
        ──▶  emit table elements as their own chunks
    data/processed/{TICKER}_{FY}.jsonl    (one Chunk per line)
    data/processed/manifest.json
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from unstructured.chunking.title import chunk_by_title
from unstructured.partition.html import partition_html

# ── Paths ─────────────────────────────────────────────────────────────────
# parse.py → ingestion/ → finrag/ → src/ → backend/ → ROOT
REPO_ROOT = Path(__file__).resolve().parents[4]
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

# ── Chunking knobs (see Decision 5 discussion for reasoning) ──────────────
# max:   hard ceiling. Stays comfortably under Cohere embed-v3's ~512 token limit.
# new_after: soft target — lets natural breaks happen before reaching max.
# combine_under: absorbs tiny stub chunks (lone section titles, footers).
# overlap: only kicks in when a section is split mid-section due to length.
MAX_CHARACTERS = 1500
NEW_AFTER_N_CHARS = 1200
COMBINE_TEXT_UNDER_N_CHARS = 200
OVERLAP = 150


# ── Models ────────────────────────────────────────────────────────────────
class Chunk(BaseModel):
    """One retrievable unit: either a narrative passage or a single table.

    Every field after `chunk_type` is provenance — copied from the filing's
    metadata.json so the chunk is self-contained when it lands in Qdrant.
    The retriever can filter on any of these fields without joining back.
    """

    chunk_id: str          # deterministic SHA-256 hash, 16 hex chars
    text: str              # the actual content to embed
    chunk_type: str        # "narrative" | "table"
    section_title: str | None
    section_path: list[str]
    # Filing provenance
    ticker: str
    company_name: str
    fiscal_year: int
    period_of_report: str  # ISO date string
    accession_number: str
    sec_url: str
    # Position within document — currently a monotonic ordinal per filing.
    # On Day 2+ this becomes the anchor for citation-viewer highlighting.
    element_index: int


# ── Helpers ───────────────────────────────────────────────────────────────
def _hash_chunk(ticker: str, fiscal_year: int, position: int, text: str) -> str:
    """Stable ID. Including `text` means changing chunking params produces
    new IDs rather than silently overwriting old vectors with new content."""
    h = hashlib.sha256()
    h.update(f"{ticker}|{fiscal_year}|{position}|".encode())
    h.update(text.encode())
    return h.hexdigest()[:16]


def _load_filing_metadata(filing_dir: Path) -> dict[str, Any]:
    return json.loads((filing_dir / "metadata.json").read_text())


def _element_category(el: Any) -> str:
    # Unstructured elements expose `.category`; fall back to class name.
    return getattr(el, "category", type(el).__name__)


def _table_text(el: Any) -> str:
    """Prefer the HTML representation — preserves rows/columns for the
    embedder. Falls back to flattened text if HTML isn't available."""
    md = getattr(el, "metadata", None)
    if md is not None:
        html = getattr(md, "text_as_html", None)
        if html:
            return html
    return el.text


# ── Core ──────────────────────────────────────────────────────────────────
def parse_filing(filing_dir: Path) -> list[Chunk]:
    """Read a filing directory, return chunks ready for embedding."""
    meta = _load_filing_metadata(filing_dir)
    htm_path = filing_dir / "filing.htm"

    # Stage 1 — atomize the HTML into typed elements.
    # `partition_html` is slow on first run (downloads NLTK data); fast after.
    elements = partition_html(filename=str(htm_path))

    # Stage 2a — walk elements once, building two parallel structures:
    #   - sections: a list of section buckets, each holding narrative elements
    #   - tables: pulled out into their own stream with section context attached
    #
    # Why bucket by section ourselves rather than relying on chunk_by_title's
    # implicit handling? Because we need clean `section_title` attribution per
    # chunk, and Unstructured's CompositeElement doesn't always expose the
    # underlying Title element reliably across versions.
    sections: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None

    for idx, el in enumerate(elements):
        category = _element_category(el)

        if category == "Title":
            # Start a new section bucket. The Title element itself goes in
            # so chunk_by_title sees it as the leading boundary.
            current_section = {
                "title": el.text,
                "elements": [el],
            }
            sections.append(current_section)
        elif category == "Table":
            tables.append(
                {
                    "idx": idx,
                    "element": el,
                    "section_title": current_section["title"] if current_section else None,
                }
            )
        else:
            # Anything else: NarrativeText, ListItem, Header, etc.
            if current_section is None:
                # Content before the first Title (cover page, etc.)
                current_section = {"title": None, "elements": []}
                sections.append(current_section)
            current_section["elements"].append(el)

    chunks: list[Chunk] = []
    position = 0  # monotonic counter, used as element_index for stable IDs

    # Stage 2b — chunk narrative *within* each section.
    # By calling chunk_by_title per section, chunks never cross section
    # boundaries — a guarantee we couldn't make with a single document-wide call.
    for section in sections:
        if not section["elements"]:
            continue

        composite_chunks = chunk_by_title(
            section["elements"],
            max_characters=MAX_CHARACTERS,
            new_after_n_chars=NEW_AFTER_N_CHARS,
            combine_text_under_n_chars=COMBINE_TEXT_UNDER_N_CHARS,
            overlap=OVERLAP,
        )

        for cc in composite_chunks:
            text = cc.text.strip()
            if not text:
                continue  # skip empty composite results
            chunks.append(
                Chunk(
                    chunk_id=_hash_chunk(
                        meta["ticker"], meta["fiscal_year"], position, text
                    ),
                    text=text,
                    chunk_type="narrative",
                    section_title=section["title"],
                    section_path=[section["title"]] if section["title"] else [],
                    ticker=meta["ticker"],
                    company_name=meta["company_name"],
                    fiscal_year=meta["fiscal_year"],
                    period_of_report=meta["period_of_report"],
                    accession_number=meta["accession_number"],
                    sec_url=meta["sec_url"],
                    element_index=position,
                )
            )
            position += 1

    # Stage 2c — emit tables as their own chunks.
    # The text is the table's HTML (when available), which keeps cell/column
    # structure visible to the embedder. Tables that are very large will get
    # truncated by Cohere's 512-token limit — accepted, because Day 2's
    # DuckDB extractor will handle these structurally anyway.
    for tbl in tables:
        text = _table_text(tbl["element"]).strip()
        if not text:
            continue
        chunks.append(
            Chunk(
                chunk_id=_hash_chunk(
                    meta["ticker"], meta["fiscal_year"], position, text
                ),
                text=text,
                chunk_type="table",
                section_title=tbl["section_title"],
                section_path=[tbl["section_title"]] if tbl["section_title"] else [],
                ticker=meta["ticker"],
                company_name=meta["company_name"],
                fiscal_year=meta["fiscal_year"],
                period_of_report=meta["period_of_report"],
                accession_number=meta["accession_number"],
                sec_url=meta["sec_url"],
                element_index=position,
            )
        )
        position += 1

    return chunks


# ── CLI ───────────────────────────────────────────────────────────────────
def _write_chunks(chunks: list[Chunk], out_file: Path) -> None:
    with out_file.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(c.model_dump_json() + "\n")


def _read_chunks(out_file: Path) -> list[Chunk]:
    return [
        Chunk.model_validate_json(line)
        for line in out_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    filing_dirs = sorted(
        d for d in RAW_DIR.iterdir() if d.is_dir() and (d / "metadata.json").exists()
    )
    print(f"Found {len(filing_dirs)} filings to parse.\n")

    manifest_entries: list[dict[str, Any]] = []

    for filing_dir in filing_dirs:
        meta = _load_filing_metadata(filing_dir)
        ticker = meta["ticker"]
        fy = meta["fiscal_year"]
        out_file = PROCESSED_DIR / f"{ticker}_{fy}.jsonl"

        if out_file.exists():
            chunks = _read_chunks(out_file)
            print(f"  ↳ skip   {ticker} FY{fy} ({len(chunks)} chunks on disk)")
        else:
            print(f"  ↳ parse  {ticker} FY{fy}…", end="", flush=True)
            chunks = parse_filing(filing_dir)
            _write_chunks(chunks, out_file)
            print(f" → {len(chunks)} chunks")

        manifest_entries.append(
            {
                "ticker": ticker,
                "fiscal_year": fy,
                "chunks_total": len(chunks),
                "chunks_narrative": sum(1 for c in chunks if c.chunk_type == "narrative"),
                "chunks_table": sum(1 for c in chunks if c.chunk_type == "table"),
                "path": out_file.relative_to(REPO_ROOT).as_posix(),
            }
        )

    manifest_path = PROCESSED_DIR / "manifest.json"
    manifest_path.write_text(json.dumps({"filings": manifest_entries}, indent=2))
    total = sum(e["chunks_total"] for e in manifest_entries)
    print(f"\nDone. {total} chunks across {len(manifest_entries)} filings.")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
