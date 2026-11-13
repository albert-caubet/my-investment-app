"""Company financials: the accounts, measures and valuation of any company, found by search.

The search box finds stocks on Yahoo Finance by name, ticker or ISIN, as the Stock
explorer's does. A company in the latest screener table of either market is shown as
that table has it, read from the market database. Any other is fetched when picked, by
the screener build's own steps for a universe of one
(``invest.jobs.fundamentals.look_up_company``): a US filer's accounts from SEC EDGAR,
any other's from Yahoo Finance. What is fetched stays in the app's cache for half a day
and is written nowhere, so it never enters a screener table or a rank. The company is
kept in the page URL (``?company=7974.T``), so a reload, a bookmark or a link from the
Watchlist or the Stock explorer opens it. The detail is the Screener's (``company_detail``).
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

import company_detail
import job_panel
import market_data as md
from invest import secrets
from invest.data import search
from invest.data.cache import Store
from invest.data.edgar import user_agent_ok
from invest.fundamentals.universe import REGIONS, screener_kind
from invest.jobs.fundamentals import CompanyData
from invest.paths import db_path

st.title("Company financials")

job_panel.follow_if_running()  # a job holds the database; nothing below may read it

DB = db_path()
URL_KEY = company_detail.URL_KEY  # ?company=7974.T, which other pages link with
QUERY = "company_query"
#: Where a table's accounts come from, for a snapshot saved before the tables said so.
SOURCES = {"US": "edgar", "EU": "yahoo"}

secrets.reload()  # an edit to secrets.toml counts on the next rerun, without a restart
FROM_EDGAR = user_agent_ok(secrets.sec_user_agent())

st.caption(
    "Search for any stock Yahoo Finance quotes, by name, ticker or ISIN, and pick it. A company in a screener table "
    "is shown as that table has it; any other is fetched when picked: "
    + ("a US filer's accounts from SEC EDGAR, any other's from Yahoo Finance." if FROM_EDGAR
       else "its accounts from Yahoo Finance.")
    + " The page URL keeps the company, so a bookmark brings it back."
)
if not FROM_EDGAR:
    st.caption("With `SEC_USER_AGENT` set in `.streamlit/secrets.toml`, as the US screener table needs, a US filer's "
               "accounts come from SEC EDGAR instead: ten years and more of them, point-in-time, rather than Yahoo's "
               "four or five.")


# ==========================================================================================
# Search
# ==========================================================================================


def _pick(hit: search.Hit) -> None:
    st.query_params[URL_KEY] = hit.ref
    st.session_state[QUERY] = ""  # this search is done


def _results(query: str, current: str) -> None:
    """The stocks a search finds, each a button that shows that company."""
    if len(query) < 2:
        st.caption("Type at least two characters.")
        return
    try:
        quotes = md.search_yahoo(query)
    except Exception as exc:
        st.caption(f"Yahoo Finance did not answer: {md.reason(exc)}")
        return
    # ETFs, funds, indices and the rest have no accounts to show
    hits = search.rank(query, [[hit for hit in search.yahoo_hits(quotes) if hit.kind == "Stock"]])
    if not hits:
        st.caption(f"No stock found for “{query}”.")
        return
    found = st.container(gap=None)  # a list, not a column of separate buttons
    for n, hit in enumerate(hits):
        shown = hit.ref.upper() == current
        found.button(search.hit_label(hit), key=f"company_hit_{n}", on_click=_pick, args=(hit,), type="tertiary",
                     icon=":material/check:" if shown else ":material/arrow_forward:", disabled=shown)


current = (st.query_params.get(URL_KEY) or "").strip().upper()
query = st.text_input("Find a company", key=QUERY, label_visibility="collapsed", icon=":material/search:",
                      placeholder="Find a company: name, ticker or ISIN")
if query.strip():
    _results(query.strip(), current)

if not current:
    st.info("Search for a company to see its accounts, measures and valuation.", icon=":material/search:")
    st.stop()

# ==========================================================================================
# The company: from a stored screener table, else fetched
# ==========================================================================================


def _version() -> float:
    """Cache key: the file's modification time, so a new screener table shows on the next run."""
    try:
        return os.path.getmtime(DB)
    except OSError:
        return 0.0


@st.cache_data(ttl=600, show_spinner=False)
def _tables(db: str, version: float) -> dict[str, dict]:
    """The latest screener table of each market, by region: its rows by ticker, where its accounts
    come from, its universe, the day it was built and whether it is point-in-time."""
    out = {}
    with Store(db, read_only=True) as store:
        for region in REGIONS:
            snap = store.load_snapshot(screener_kind(region))
            rows = pd.DataFrame(snap[2].get("rows", [])) if snap else pd.DataFrame()
            if "ticker" not in rows:
                continue
            payload = snap[2]
            out[region] = {
                "rows": rows.drop_duplicates("ticker").set_index("ticker", drop=False),
                "source": payload.get("source") or SOURCES[region],
                "universe": payload.get("universe_label") or payload.get("universe") or region,
                "built": f"{pd.Timestamp(snap[1]):%Y-%m-%d}",
                "point_in_time": payload.get("point_in_time", True),
            }
    return out


def _stored(symbol: str) -> tuple[CompanyData, str] | None:
    """The company as a stored screener table has it, and a line saying which; None when none has it,
    or when the market database cannot be read, as while a job outside the app writes it."""
    if not DB.exists():
        return None
    try:
        for table in _tables(str(DB), _version()).values():
            if symbol in table["rows"].index:
                company = company_detail.from_table(str(DB), _version(), table["rows"].loc[symbol], table["source"])
                return company, (f"As the screener table of the {table['universe']} has it, built {table['built']}"
                                 + ("" if table["point_in_time"] else ", not point-in-time") + ".")
    except Exception:
        return None
    return None


def _fetched(symbol: str) -> tuple[CompanyData, str]:
    """The company fetched just now, and a line saying from where. Stops the page when nothing has its accounts."""
    try:
        with st.spinner(f"Fetching the accounts of {symbol}…"):
            company = md.fetch_company(symbol)
    except Exception as exc:
        st.warning(f"No accounts for {symbol}.\n\n{md.reason(exc).rstrip('.')}.")
        st.stop()
    source = ("SEC EDGAR, as filed and point-in-time, with Yahoo Finance's prices" if company.source == "edgar"
              else "Yahoo Finance: four or five fiscal years, not point-in-time")
    return company, (f"Fetched from {source}. In no screener table, so it has no rank; what is fetched here is "
                     f"kept for half a day and stored nowhere.")


company, where = _stored(current) or _fetched(current)
row = company.row
name = row.get("name") if isinstance(row.get("name"), str) and row.get("name") else current
label = f"{name} ({current})"

head = st.container(horizontal=True, vertical_alignment="center")
head.subheader(search.plain(label), width="stretch")  # the button to the far right
company_detail.watch_button(head, current, cik=company_detail.sec_cik(row), label=label,
                            origin="the Company financials page")
sector = row.get("sector")
st.caption(" · ".join(([sector.capitalize()] if isinstance(sector, str) and sector != "unknown" else []) + [where]))
company_detail.show(company, key="company", measures=True)
