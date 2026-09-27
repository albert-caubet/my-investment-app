"""SEC EDGAR: company facts, submissions, filing files and the frames API.

EDGAR wants a User-Agent with a contact address and no more than ten requests
per second; the client enforces both. It refuses to run without a configured
``SEC_USER_AGENT`` rather than sending a made-up one.

Parsing is separated from fetching: ``facts_frame``, ``filings_frame`` and
``tickers_frame`` are pure functions over the JSON payloads, tested on saved
responses.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date

import pandas as pd

from invest.data.http import FetchError, fetch_bytes
from invest.secrets import sec_user_agent

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{name}"

MIN_INTERVAL = 0.11  # ten requests per second, with a little room


#: How to set it up, for the command line and the Screener page alike.
SEC_USER_AGENT_HELP = """SEC EDGAR answers only requests that name an app and a contact email. Set it once,
in .streamlit/secrets.toml at the top of the repository (git ignores that file):

    SEC_USER_AGENT = "my-investment-app your.name@example.com"

or as an environment variable of the same name. Use your own email address."""


class EdgarConfigError(RuntimeError):
    """No SEC_USER_AGENT configured. EDGAR requires a contact address."""


def user_agent_ok(user_agent: str | None) -> bool:
    return bool(user_agent) and "@" in user_agent


class EdgarClient:
    def __init__(self, user_agent: str | None = None, *, min_interval: float = MIN_INTERVAL):
        self.user_agent = user_agent or sec_user_agent()
        if not user_agent_ok(self.user_agent):
            raise EdgarConfigError(
                "SEC_USER_AGENT must be set to an app name and contact address, for example "
                "'my-investment-app you@example.com'"
            )
        self.min_interval = min_interval
        self._last = 0.0
        self.n_requests = 0

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get_bytes(self, url: str) -> bytes:
        self._throttle()
        self.n_requests += 1
        return fetch_bytes(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"})

    def get_json(self, url: str):
        try:
            return json.loads(self.get_bytes(url).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise FetchError(f"{url}: not JSON ({exc})") from exc

    # -- endpoints -------------------------------------------------------------

    def company_tickers(self) -> pd.DataFrame:
        return tickers_frame(self.get_json(COMPANY_TICKERS_URL))

    def submissions(self, cik: int) -> dict:
        return self.get_json(SUBMISSIONS_URL.format(cik=int(cik)))

    def companyfacts(self, cik: int) -> dict:
        return self.get_json(COMPANYFACTS_URL.format(cik=int(cik)))

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> pd.DataFrame:
        return frames_frame(self.get_json(FRAMES_URL.format(taxonomy=taxonomy, tag=tag, unit=unit, period=period)))

    def filing_index(self, cik: int, accession: str) -> list[str]:
        """File names inside one filing folder."""
        payload = self.get_json(ARCHIVE_URL.format(cik=int(cik), accession=accession.replace("-", ""), name="index.json"))
        return [item["name"] for item in payload.get("directory", {}).get("item", [])]

    def filing_file(self, cik: int, accession: str, name: str) -> bytes:
        return self.get_bytes(ARCHIVE_URL.format(cik=int(cik), accession=accession.replace("-", ""), name=name))


# --- pure parsers -------------------------------------------------------------

def tickers_frame(payload: dict) -> pd.DataFrame:
    """``company_tickers.json`` (a dict of row dicts) into ``(cik, ticker, name)``."""
    rows = payload.values() if isinstance(payload, dict) else payload
    frame = pd.DataFrame(
        [{"cik": int(r["cik_str"]), "ticker": str(r["ticker"]).upper(), "name": r.get("title")} for r in rows]
    )
    return frame.drop_duplicates("ticker").reset_index(drop=True)


def filings_frame(submissions: dict) -> pd.DataFrame:
    """The ``filings.recent`` block into one row per filing."""
    recent = submissions.get("filings", {}).get("recent", {})
    if not recent:
        return pd.DataFrame(columns=["accession", "form", "filing_date", "report_date", "primary_document"])
    frame = pd.DataFrame(
        {
            "accession": recent.get("accessionNumber", []),
            "form": recent.get("form", []),
            "filing_date": pd.to_datetime(recent.get("filingDate", [])).date,
            "report_date": [pd.Timestamp(d).date() if d else None for d in recent.get("reportDate", [])],
            "primary_document": recent.get("primaryDocument", []),
        }
    )
    return frame


def fact_id(cik: int, taxonomy: str, tag: str, unit: str, start: date | None, end: date, accession: str) -> str:
    raw = f"{cik}|{taxonomy}|{tag}|{unit}|{start or ''}|{end}|{accession}"
    return hashlib.sha1(raw.encode()).hexdigest()


def facts_frame(
    payload: dict,
    *,
    taxonomies=("us-gaap", "ifrs-full", "dei"),
    tags: dict[str, set[str]] | None = None,
) -> pd.DataFrame:
    """``companyfacts`` JSON into the store's ``facts`` shape, one row per reported value.

    Every row keeps ``filed_at`` (the filing date) so a screen can be run as of a
    past date using only what was public then, and ``accession`` so a restated
    value from a later filing is a separate row rather than an overwrite.

    ``tags`` (``{taxonomy: {tag, ...}}``) keeps only the concepts named; a large
    filer reports several hundred tags and the screener reads a few dozen.
    """
    cik = int(payload["cik"])
    rows = []
    for taxonomy in taxonomies:
        wanted = tags.get(taxonomy) if tags is not None else None
        for tag, body in payload.get("facts", {}).get(taxonomy, {}).items():
            if wanted is not None and tag not in wanted:
                continue
            for unit, values in body.get("units", {}).items():
                for v in values:
                    end = pd.Timestamp(v["end"]).date()
                    start = pd.Timestamp(v["start"]).date() if v.get("start") else None
                    filed = pd.Timestamp(v["filed"]).date()
                    accession = v.get("accn", "")
                    rows.append(
                        {
                            "fact_id": fact_id(cik, taxonomy, tag, unit, start, end, accession),
                            "cik": cik,
                            "taxonomy": taxonomy,
                            "tag": tag,
                            "unit": unit,
                            "start_date": start,
                            "end_date": end,
                            "value": float(v["val"]) if v.get("val") is not None else None,
                            "fy": int(v["fy"]) if v.get("fy") is not None else None,
                            "fp": v.get("fp"),
                            "form": v.get("form"),
                            "filed_at": filed,
                            "accession": accession,
                            "frame": v.get("frame"),
                        }
                    )
    columns = ["fact_id", "cik", "taxonomy", "tag", "unit", "start_date", "end_date", "value", "fy", "fp",
               "form", "filed_at", "accession", "frame"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).drop_duplicates("fact_id").reset_index(drop=True)


def frames_frame(payload: dict) -> pd.DataFrame:
    """The frames API (one tag, one period, every filer) into a frame."""
    rows = payload.get("data", [])
    frame = pd.DataFrame(
        [
            {
                "cik": int(r["cik"]),
                "entity": r.get("entityName"),
                "start_date": pd.Timestamp(r["start"]).date() if r.get("start") else None,
                "end_date": pd.Timestamp(r["end"]).date(),
                "value": float(r["val"]),
                "accession": r.get("accn"),
                "location": r.get("loc"),
            }
            for r in rows
        ]
    )
    frame.attrs.update({k: payload.get(k) for k in ("taxonomy", "tag", "uom", "ccp")})
    return frame


@dataclass(frozen=True)
class FilingRef:
    cik: int
    accession: str
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str


def select_filings(filings: pd.DataFrame, cik: int, forms: tuple[str, ...], *, limit: int | None = None,
                   since: date | None = None) -> list[FilingRef]:
    rows = filings[filings["form"].isin(forms)].sort_values("filing_date", ascending=False)
    if since is not None:
        rows = rows[rows["filing_date"] >= since]
    if limit is not None:
        rows = rows.head(limit)
    return [
        FilingRef(int(cik), r["accession"], r["form"], r["filing_date"], r["report_date"], r["primary_document"])
        for _, r in rows.iterrows()
    ]
