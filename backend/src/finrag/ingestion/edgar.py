import json
import os
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import httpx
from pydantic import BaseModel

# ── Paths ─────────────────────────────────────────────────────────────────
# ← why: same trick as config.py — resolve to repo root so the script works
#   regardless of CWD. edgar.py → ingestion/ → finrag/ → src/ → backend/ → ROOT
REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = REPO_ROOT / "data" / "raw"

# ── HTTP client ───────────────────────────────────────────────────────────
# ← why: SEC requires a real contactable email. Fall back to yours so the
#   script never accidentally runs with a placeholder; override via env in CI.
USER_AGENT = os.getenv("SEC_USER_AGENT", "Aryan Sharma aryan250403@gmail.com")
HTTP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
}
client = httpx.Client(headers=HTTP_HEADERS, timeout=30.0)

# ── Corpus config ─────────────────────────────────────────────────────────
TARGET_TICKERS = ["AAPL", "TSLA", "JPM"]
TARGET_YEARS = [2022, 2023, 2024]
REQUEST_SLEEP_SECONDS = 0.2  # ← why: SEC allows 10 req/s; 5 req/s is polite.


# ── Models ────────────────────────────────────────────────────────────────
class Filing(BaseModel):
    # ← why: every field here exists because some downstream stage needs it.
    #   ticker/cik/accession give three independent identifiers.
    #   filing_date vs period_of_report are *different things* and both matter.
    #   sec_url is for citation rendering in the UI later.
    ticker: str
    company_name: str
    cik: str
    form: str
    filing_date: date
    period_of_report: date
    fiscal_year: int
    accession_number: str
    accession_clean: str
    primary_document: str
    sec_url: str
    out_dir: str  # relative to DATA_DIR

    def metadata_dict(self) -> dict:
        # ← why: pydantic's model_dump() emits dates as date objects; JSON
        #   can't serialize those, so coerce to ISO strings explicitly.
        d = self.model_dump()
        d["filing_date"] = self.filing_date.isoformat()
        d["period_of_report"] = self.period_of_report.isoformat()
        return d


# ── EDGAR API calls ───────────────────────────────────────────────────────
def resolve_cik_map() -> dict[str, tuple[str, str]]:
    """Return {ticker: (cik_str, company_name)} for the whole market.

    ← why: SEC publishes the entire ticker→CIK map as one JSON file. Fetching
       it once and resolving locally is cheaper than per-ticker lookups.
    """
    url = "https://www.sec.gov/files/company_tickers.json"
    response = client.get(url)
    response.raise_for_status()
    data = response.json()
    return {
        entry["ticker"].upper(): (str(entry["cik_str"]), entry["title"])
        for entry in data.values()
    }


def _iter_submission_pages(padded_cik: str) -> Iterator[dict]:
    """Yield each 'recent'-shaped page from the submissions endpoint.

    First yields filings.recent, then walks filings.files for older pages.
    High-volume filers (banks, frequent 8-K issuers) overflow `recent` and
    require pulling the paginated files to find 10-Ks more than ~1-2 yrs old.
    """
    url = f"https://data.sec.gov/submissions/CIK{padded_cik}.json"
    response = client.get(url)
    response.raise_for_status()
    data = response.json()

    yield data["filings"]["recent"]

    for file_entry in data["filings"].get("files", []):
        time.sleep(REQUEST_SLEEP_SECONDS)
        file_url = f"https://data.sec.gov/submissions/{file_entry['name']}"
        resp = client.get(file_url)
        resp.raise_for_status()
        yield resp.json()


def list_10k_filings(
    ticker: str, cik: str, company_name: str, target_years: list[int]
) -> list[Filing]:
    """Hit EDGAR's submissions endpoint and pull 10-Ks for the target years.

    Walks paginated submission pages until every target year is found or
    pages are exhausted.
    """
    padded_cik = cik.zfill(10)
    remaining_years = set(target_years)
    filings: list[Filing] = []

    for page in _iter_submission_pages(padded_cik):
        if not remaining_years:
            break
        for acc_num, form, filing_date_str, period_str, prim_doc in zip(
            page["accessionNumber"],
            page["form"],
            page["filingDate"],
            page["reportDate"],
            page["primaryDocument"],
        ):
            if form != "10-K":
                continue
            if not period_str:
                continue
            period_of_report = date.fromisoformat(period_str)
            fiscal_year = period_of_report.year
            if fiscal_year not in remaining_years:
                continue

            accession_clean = acc_num.replace("-", "")
            sec_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession_clean}/{prim_doc}"
            )
            filings.append(
                Filing(
                    ticker=ticker,
                    company_name=company_name,
                    cik=cik,
                    form=form,
                    filing_date=date.fromisoformat(filing_date_str),
                    period_of_report=period_of_report,
                    fiscal_year=fiscal_year,
                    accession_number=acc_num,
                    accession_clean=accession_clean,
                    primary_document=prim_doc,
                    sec_url=sec_url,
                    out_dir=f"{ticker}_{fiscal_year}",
                )
            )
            remaining_years.discard(fiscal_year)
    return filings


def download_filing(filing: Filing, base_out_dir: Path) -> None:
    """Download the primary 10-K HTML and write metadata.json alongside it."""
    dir_path = base_out_dir / filing.out_dir
    dir_path.mkdir(parents=True, exist_ok=True)

    htm_path = dir_path / "filing.htm"
    meta_path = dir_path / "metadata.json"

    # ← why: idempotency. Existence of *both* artifacts means a clean prior run.
    #   If only one exists, we redo to repair partial state.
    if htm_path.exists() and meta_path.exists():
        print(f"  ↳ skip   {filing.ticker} FY{filing.fiscal_year} (already on disk)")
        return

    print(f"  ↳ fetch  {filing.ticker} FY{filing.fiscal_year}  →  {filing.sec_url}")
    response = client.get(filing.sec_url)
    response.raise_for_status()
    htm_path.write_bytes(response.content)
    meta_path.write_text(json.dumps(filing.metadata_dict(), indent=2))


def write_manifest(filings: list[Filing], base_out_dir: Path) -> None:
    """Top-level index of everything we've downloaded.

    ← why: lets later stages (parser, embedder) load the corpus by reading one
       file instead of walking the tree.
    """
    manifest_path = base_out_dir / "manifest.json"
    manifest = {
        "filings": [
            {
                "ticker": f.ticker,
                "fiscal_year": f.fiscal_year,
                "period_of_report": f.period_of_report.isoformat(),
                "path": f.out_dir,
            }
            for f in filings
        ]
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))


# ── CLI entrypoint ────────────────────────────────────────────────────────
def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading to: {DATA_DIR}")
    print(f"User-Agent: {USER_AGENT}\n")

    ticker_map = resolve_cik_map()
    all_filings: list[Filing] = []

    for ticker in TARGET_TICKERS:
        if ticker not in ticker_map:
            raise ValueError(f"Ticker {ticker} not found in SEC ticker map")
        cik, company_name = ticker_map[ticker]
        print(f"[{ticker}] {company_name} (CIK {cik})")

        filings = list_10k_filings(ticker, cik, company_name, TARGET_YEARS)
        if len(filings) < len(TARGET_YEARS):
            missing = set(TARGET_YEARS) - {f.fiscal_year for f in filings}
            print(f"  ⚠ missing fiscal years: {sorted(missing)}")

        for filing in filings:
            download_filing(filing, DATA_DIR)
            time.sleep(REQUEST_SLEEP_SECONDS)
        all_filings.extend(filings)
        print()

    write_manifest(all_filings, DATA_DIR)
    print(f"Done. {len(all_filings)} filings on disk.")


if __name__ == "__main__":
    main()
