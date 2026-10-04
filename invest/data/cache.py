"""DuckDB storage with provenance.

One file, ``data/market.duckdb``, for everything that is not a transaction:
time series, prices, XBRL facts, 13F holdings, insider trades, screener
snapshots, report runs. Firestore keeps transactions only, exactly as before.

Two rules make backtests honest later:

- ``series`` is **append-only**. A fetch adds a row only where the value for an
  observation date is new or has changed, stamped with ``fetched_at`` and a
  ``vintage_date``. A second identical refresh adds nothing, a revision adds a
  row and keeps the old one, and ``read_series(as_of=D)`` answers "what did we
  know on D" by taking the latest vintage at or before D.
- Nothing here fills a gap. A missing observation stays missing.

The Streamlit pages open the file read-only and never write series; the jobs are
the only writers. DuckDB allows one writer per file, so a page that finds the
file locked says so instead of failing.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import duckdb
import pandas as pd

from invest.paths import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    source       VARCHAR NOT NULL,
    series_id    VARCHAR NOT NULL,
    obs_date     DATE    NOT NULL,
    value        DOUBLE,
    fetched_at   TIMESTAMP NOT NULL,
    vintage_date DATE    NOT NULL
);
CREATE INDEX IF NOT EXISTS series_key ON series (series_id, obs_date);

CREATE TABLE IF NOT EXISTS fetches (
    source     VARCHAR NOT NULL,
    series_id  VARCHAR NOT NULL,
    key        VARCHAR,
    started    TIMESTAMP NOT NULL,
    finished   TIMESTAMP NOT NULL,
    status     VARCHAR NOT NULL,
    message    VARCHAR,
    n_rows     INTEGER
);

CREATE TABLE IF NOT EXISTS prices (
    symbol     VARCHAR NOT NULL,
    date       DATE    NOT NULL,
    close      DOUBLE,
    adj_close  DOUBLE,
    currency   VARCHAR,
    fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS facts (
    fact_id    VARCHAR PRIMARY KEY,
    cik        INTEGER NOT NULL,
    taxonomy   VARCHAR NOT NULL,
    tag        VARCHAR NOT NULL,
    unit       VARCHAR NOT NULL,
    start_date DATE,
    end_date   DATE NOT NULL,
    value      DOUBLE,
    fy         INTEGER,
    fp         VARCHAR,
    form       VARCHAR,
    filed_at   DATE NOT NULL,
    accession  VARCHAR NOT NULL,
    frame      VARCHAR,
    fetched_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_cik ON facts (cik, tag);

CREATE TABLE IF NOT EXISTS companies (
    cik        INTEGER PRIMARY KEY,
    ticker     VARCHAR,
    name       VARCHAR,
    sic        VARCHAR,
    sector     VARCHAR,
    exchange   VARCHAR,
    fetched_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS holdings13f (
    row_id       VARCHAR PRIMARY KEY,
    manager_cik  INTEGER NOT NULL,
    manager      VARCHAR,
    period_end   DATE NOT NULL,
    filed_at     DATE NOT NULL,
    accession    VARCHAR NOT NULL,
    cusip        VARCHAR NOT NULL,
    issuer       VARCHAR,
    title_class  VARCHAR,
    value_usd    DOUBLE,
    shares       DOUBLE,
    sh_prn       VARCHAR,
    put_call     VARCHAR,
    discretion   VARCHAR,
    fetched_at   TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS insider_tx (
    row_id       VARCHAR PRIMARY KEY,
    issuer_cik   INTEGER NOT NULL,
    accession    VARCHAR NOT NULL,
    filed_at     DATE NOT NULL,
    trans_date   DATE,
    owner        VARCHAR,
    is_director  BOOLEAN,
    is_officer   BOOLEAN,
    code         VARCHAR,
    shares       DOUBLE,
    price        DOUBLE,
    acquired     BOOLEAN,
    fetched_at   TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id         VARCHAR PRIMARY KEY,
    kind           VARCHAR NOT NULL,
    started        TIMESTAMP NOT NULL,
    finished       TIMESTAMP,
    sources_ok     INTEGER,
    sources_failed INTEGER,
    notes          VARCHAR
);

CREATE TABLE IF NOT EXISTS snapshots (
    run_id     VARCHAR NOT NULL,
    kind       VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL,
    payload    VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol   VARCHAR PRIMARY KEY,
    cik      INTEGER,
    note     VARCHAR,
    added_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    name        VARCHAR NOT NULL,
    url         VARCHAR,
    sha256      VARCHAR NOT NULL,
    n_bytes     INTEGER,
    fetched_at  TIMESTAMP NOT NULL,
    path        VARCHAR
);
"""


def utcnow() -> datetime:
    """Naive UTC timestamp, the convention for every ``fetched_at`` column.

    Microseconds are kept: two snapshots saved in the same second must still
    order correctly.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_run_id() -> str:
    return f"{utcnow():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    return pd.Timestamp(value).date()


def observations_frame(data) -> pd.DataFrame:
    """Normalise anything series-like into ``DataFrame(obs_date: date, value: float)``.

    Accepts a ``pd.Series`` with a datetime index, a two-column frame, or a
    mapping ``{date-like: value}``. Non-finite values are dropped: a blank in a
    FRED CSV is a market holiday, not an observation of nothing.
    """
    if isinstance(data, pd.Series):
        frame = data.rename("value").rename_axis("obs_date").reset_index()
    elif isinstance(data, pd.DataFrame):
        if list(data.columns[:2]) != ["obs_date", "value"]:
            frame = data.iloc[:, :2].copy()
            frame.columns = ["obs_date", "value"]
        else:
            frame = data[["obs_date", "value"]].copy()
    elif isinstance(data, Mapping):
        frame = pd.DataFrame({"obs_date": list(data.keys()), "value": list(data.values())})
    else:
        raise TypeError(f"cannot turn {type(data).__name__} into observations")
    frame["obs_date"] = pd.to_datetime(frame["obs_date"]).dt.date
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce").astype(float)
    frame = frame[frame["value"].map(lambda v: v is not None and math.isfinite(v))]
    frame = frame.dropna(subset=["obs_date"]).drop_duplicates("obs_date", keep="last")
    return frame.sort_values("obs_date").reset_index(drop=True)


@dataclass(frozen=True)
class SeriesMeta:
    series_id: str
    source: str | None
    n_obs: int
    first_obs: date | None
    last_obs: date | None
    last_fetched: datetime | None
    last_value: float | None


class Store:
    """One DuckDB connection. Use as a context manager or call ``close``."""

    def __init__(self, path: str | Path | None = None, *, read_only: bool = False):
        self.path = Path(path) if path is not None else db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.read_only = read_only
        self.con = duckdb.connect(str(self.path), read_only=read_only)
        if not read_only:
            self.con.execute(SCHEMA)

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # series
    # ------------------------------------------------------------------

    def upsert_series(
        self,
        series_id: str,
        data,
        *,
        source: str,
        fetched_at: datetime | None = None,
        vintage_date: date | None = None,
    ) -> int:
        """Append the observations whose value is new or changed. Returns rows added.

        ``vintage_date`` defaults to the fetch date: what was knowable on the day
        of the fetch. A release-type entry passes its release date; an ALFRED
        vintage passes its ``realtime_start``.
        """
        frame = observations_frame(data)
        if frame.empty:
            return 0
        fetched_at = fetched_at or utcnow()
        vintage = vintage_date or fetched_at.date()
        self.con.register("new_rows", frame)
        try:
            before = self.con.execute(
                "SELECT count(*) FROM series WHERE series_id = ?", [series_id]
            ).fetchone()[0]
            self.con.execute(
                """
                INSERT INTO series (source, series_id, obs_date, value, fetched_at, vintage_date)
                SELECT ?, ?, n.obs_date, n.value, ?, ?
                FROM new_rows n
                LEFT JOIN (
                    SELECT obs_date, value FROM (
                        SELECT obs_date, value,
                               row_number() OVER (PARTITION BY obs_date
                                                  ORDER BY vintage_date DESC, fetched_at DESC) AS rn
                        FROM series WHERE series_id = ?
                    ) WHERE rn = 1
                ) latest ON latest.obs_date = n.obs_date
                WHERE latest.obs_date IS NULL OR latest.value IS DISTINCT FROM n.value
                """,
                [source, series_id, fetched_at, vintage, series_id],
            )
            after = self.con.execute(
                "SELECT count(*) FROM series WHERE series_id = ?", [series_id]
            ).fetchone()[0]
        finally:
            self.con.unregister("new_rows")
        return int(after - before)

    def insert_vintages(self, series_id: str, frame: pd.DataFrame, *, source: str, fetched_at: datetime | None = None) -> int:
        """Store historical vintages: every ``(obs_date, value, vintage_date)`` not already present.

        Unlike :meth:`upsert_series`, which compares against the latest known
        value, this keeps each first-published print with its own vintage date,
        so a point-in-time read can find the value that was current on a past day.
        """
        if frame is None or frame.empty:
            return 0
        rows = frame[["obs_date", "value", "vintage_date"]].copy()
        rows["obs_date"] = pd.to_datetime(rows["obs_date"]).dt.date
        rows["vintage_date"] = pd.to_datetime(rows["vintage_date"]).dt.date
        rows["value"] = pd.to_numeric(rows["value"], errors="coerce").astype(float)
        rows = rows.dropna().drop_duplicates(["obs_date", "vintage_date"], keep="last")
        fetched_at = fetched_at or utcnow()
        self.con.register("new_vintages", rows)
        try:
            before = self.con.execute("SELECT count(*) FROM series WHERE series_id = ?", [series_id]).fetchone()[0]
            self.con.execute(
                """
                INSERT INTO series (source, series_id, obs_date, value, fetched_at, vintage_date)
                SELECT ?, ?, n.obs_date, n.value, ?, n.vintage_date
                FROM new_vintages n
                WHERE NOT EXISTS (
                    SELECT 1 FROM series s
                    WHERE s.series_id = ? AND s.obs_date = n.obs_date AND s.vintage_date = n.vintage_date
                )
                """,
                [source, series_id, fetched_at, series_id],
            )
            after = self.con.execute("SELECT count(*) FROM series WHERE series_id = ?", [series_id]).fetchone()[0]
        finally:
            self.con.unregister("new_vintages")
        return int(after - before)

    def read_series(self, series_id: str, *, as_of: date | None = None) -> pd.Series:
        """Latest known value per observation date, as a float series on a DatetimeIndex.

        With ``as_of``, only vintages at or before that date count, so the result
        is what a reader on that day would have seen.
        """
        params: list = [series_id]
        clause = ""
        if as_of is not None:
            clause = " AND vintage_date <= ?"
            params.append(_as_date(as_of))
        frame = self.con.execute(
            f"""
            SELECT obs_date, value FROM (
                SELECT obs_date, value,
                       row_number() OVER (PARTITION BY obs_date
                                          ORDER BY vintage_date DESC, fetched_at DESC) AS rn
                FROM series WHERE series_id = ?{clause}
            ) WHERE rn = 1 ORDER BY obs_date
            """,
            params,
        ).df()
        if frame.empty:
            return pd.Series(dtype=float, name=series_id)
        out = pd.Series(
            frame["value"].astype(float).to_numpy(),
            index=pd.DatetimeIndex(pd.to_datetime(frame["obs_date"])),
            name=series_id,
        )
        return out.dropna()

    def read_vintages(self, series_id: str) -> pd.DataFrame:
        """Every stored row for a series, revisions included."""
        return self.con.execute(
            "SELECT source, series_id, obs_date, value, fetched_at, vintage_date FROM series "
            "WHERE series_id = ? ORDER BY obs_date, vintage_date, fetched_at",
            [series_id],
        ).df()

    def series_meta(self, series_id: str) -> SeriesMeta:
        row = self.con.execute(
            """
            SELECT any_value(source), count(*), min(obs_date), max(obs_date), max(fetched_at)
            FROM series WHERE series_id = ?
            """,
            [series_id],
        ).fetchone()
        source, n, first, last, fetched = row
        last_value = None
        if n:
            last_value = self.con.execute(
                "SELECT value FROM series WHERE series_id = ? AND obs_date = ? "
                "ORDER BY vintage_date DESC, fetched_at DESC LIMIT 1",
                [series_id, last],
            ).fetchone()[0]
        return SeriesMeta(
            series_id=series_id,
            source=source,
            n_obs=int(n or 0),
            first_obs=_as_date(first),
            last_obs=_as_date(last),
            last_fetched=fetched,
            last_value=last_value,
        )

    def list_series(self) -> pd.DataFrame:
        return self.con.execute(
            """
            SELECT series_id, any_value(source) AS source, count(*) AS n_rows,
                   min(obs_date) AS first_obs, max(obs_date) AS last_obs,
                   max(fetched_at) AS last_fetched
            FROM series GROUP BY series_id ORDER BY series_id
            """
        ).df()

    # ------------------------------------------------------------------
    # fetch log and freshness
    # ------------------------------------------------------------------

    def log_fetch(
        self,
        series_id: str,
        *,
        source: str,
        key: str | None,
        status: str,
        message: str = "",
        n_rows: int = 0,
        started: datetime | None = None,
        finished: datetime | None = None,
    ) -> None:
        finished = finished or utcnow()
        started = started or finished
        self.con.execute(
            "INSERT INTO fetches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [source, series_id, key, started, finished, status, message[:2000], int(n_rows)],
        )

    def latest_fetches(self) -> pd.DataFrame:
        """The most recent attempt per series, whatever its outcome."""
        return self.con.execute(
            """
            SELECT source, series_id, key, started, finished, status, message, n_rows FROM (
                SELECT *, row_number() OVER (PARTITION BY series_id ORDER BY finished DESC) AS rn
                FROM fetches
            ) WHERE rn = 1 ORDER BY series_id
            """
        ).df()

    # ------------------------------------------------------------------
    # prices
    # ------------------------------------------------------------------

    def upsert_prices(
        self,
        symbol: str,
        frame: pd.DataFrame,
        *,
        currency: str | None = None,
        fetched_at: datetime | None = None,
    ) -> int:
        """``frame`` indexed by date with ``close`` and optionally ``adj_close``."""
        if frame is None or frame.empty:
            return 0
        close = pd.to_numeric(frame["close"], errors="coerce").astype(float).to_numpy()
        adj = (
            pd.to_numeric(frame["adj_close"], errors="coerce").astype(float).to_numpy()
            if "adj_close" in frame
            else close
        )
        rows = pd.DataFrame(
            {
                "symbol": symbol,
                "date": pd.to_datetime(frame.index).date,
                "close": close,
                "adj_close": adj,
                "currency": currency,
                "fetched_at": fetched_at or utcnow(),
            }
        )
        rows = rows[rows["close"].notna()].drop_duplicates("date", keep="last")
        if rows.empty:
            return 0
        self.con.register("new_prices", rows)
        try:
            self.con.execute("INSERT OR REPLACE INTO prices SELECT * FROM new_prices")
        finally:
            self.con.unregister("new_prices")
        return int(len(rows))

    def read_prices(self, symbol: str, *, adjusted: bool = False) -> pd.Series:
        column = "adj_close" if adjusted else "close"
        frame = self.con.execute(
            f"SELECT date, {column} AS value FROM prices WHERE symbol = ? ORDER BY date", [symbol]
        ).df()
        if frame.empty:
            return pd.Series(dtype=float, name=symbol)
        return pd.Series(
            frame["value"].astype(float).to_numpy(),
            index=pd.DatetimeIndex(pd.to_datetime(frame["date"])),
            name=symbol,
        ).dropna()

    def read_price_panel(self, symbols: Sequence[str], *, adjusted: bool = True) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame()
        column = "adj_close" if adjusted else "close"
        placeholders = ",".join("?" for _ in symbols)
        frame = self.con.execute(
            f"SELECT symbol, date, {column} AS value FROM prices WHERE symbol IN ({placeholders})",
            list(symbols),
        ).df()
        if frame.empty:
            return pd.DataFrame()
        panel = frame.pivot(index="date", columns="symbol", values="value").sort_index()
        panel.index = pd.DatetimeIndex(pd.to_datetime(panel.index))
        return panel.astype(float)

    def price_currency(self, symbol: str) -> str | None:
        """The currency stored with the latest price that has one; None when none does."""
        row = self.con.execute(
            "SELECT currency FROM prices WHERE symbol = ? AND currency IS NOT NULL ORDER BY date DESC LIMIT 1",
            [symbol],
        ).fetchone()
        return row[0] if row else None

    def price_symbols(self) -> list[str]:
        rows = self.con.execute("SELECT DISTINCT symbol FROM prices ORDER BY symbol").fetchall()
        return [r[0] for r in rows]

    def price_meta(self) -> pd.DataFrame:
        return self.con.execute(
            "SELECT symbol, any_value(currency) AS currency, count(*) AS n_rows, "
            "min(date) AS first_date, max(date) AS last_date, max(fetched_at) AS last_fetched "
            "FROM prices GROUP BY symbol ORDER BY symbol"
        ).df()

    # ------------------------------------------------------------------
    # XBRL facts and companies
    # ------------------------------------------------------------------

    def upsert_facts(self, frame: pd.DataFrame, *, fetched_at: datetime | None = None) -> int:
        """``frame`` in the shape produced by :func:`invest.data.edgar.facts_frame`."""
        if frame is None or frame.empty:
            return 0
        rows = frame.copy()
        rows["fetched_at"] = fetched_at or utcnow()
        cols = [
            "fact_id", "cik", "taxonomy", "tag", "unit", "start_date", "end_date", "value",
            "fy", "fp", "form", "filed_at", "accession", "frame", "fetched_at",
        ]
        rows = rows[cols]
        self.con.register("new_facts", rows)
        try:
            self.con.execute("INSERT OR REPLACE INTO facts SELECT * FROM new_facts")
        finally:
            self.con.unregister("new_facts")
        return int(len(rows))

    def read_facts(self, cik: int, *, as_of: date | None = None) -> pd.DataFrame:
        params: list = [int(cik)]
        clause = ""
        if as_of is not None:
            clause = " AND filed_at <= ?"
            params.append(_as_date(as_of))
        return self.con.execute(
            f"SELECT * FROM facts WHERE cik = ?{clause} ORDER BY tag, end_date, filed_at", params
        ).df()

    def facts_ciks(self) -> list[int]:
        rows = self.con.execute("SELECT DISTINCT cik FROM facts ORDER BY cik").fetchall()
        return [int(r[0]) for r in rows]

    def facts_meta(self) -> pd.DataFrame:
        return self.con.execute(
            "SELECT cik, count(*) AS n_facts, max(filed_at) AS last_filed, max(fetched_at) AS last_fetched "
            "FROM facts GROUP BY cik ORDER BY cik"
        ).df()

    def upsert_companies(self, rows: Iterable[Mapping]) -> int:
        frame = pd.DataFrame(list(rows))
        if frame.empty:
            return 0
        for col in ("ticker", "name", "sic", "sector", "exchange"):
            if col not in frame:
                frame[col] = None
        frame["fetched_at"] = utcnow()
        frame = frame[["cik", "ticker", "name", "sic", "sector", "exchange", "fetched_at"]]
        self.con.register("new_companies", frame)
        try:
            self.con.execute("INSERT OR REPLACE INTO companies SELECT * FROM new_companies")
        finally:
            self.con.unregister("new_companies")
        return int(len(frame))

    def companies(self) -> pd.DataFrame:
        return self.con.execute("SELECT * FROM companies ORDER BY ticker").df()

    # ------------------------------------------------------------------
    # 13F holdings and insider transactions
    # ------------------------------------------------------------------

    def upsert_holdings(self, frame: pd.DataFrame) -> int:
        if frame is None or frame.empty:
            return 0
        rows = frame.copy()
        rows["fetched_at"] = utcnow()
        cols = [
            "row_id", "manager_cik", "manager", "period_end", "filed_at", "accession", "cusip",
            "issuer", "title_class", "value_usd", "shares", "sh_prn", "put_call", "discretion",
            "fetched_at",
        ]
        self.con.register("new_holdings", rows[cols])
        try:
            self.con.execute("INSERT OR REPLACE INTO holdings13f SELECT * FROM new_holdings")
        finally:
            self.con.unregister("new_holdings")
        return int(len(rows))

    def read_holdings(self, manager_cik: int) -> pd.DataFrame:
        return self.con.execute(
            "SELECT * FROM holdings13f WHERE manager_cik = ? ORDER BY period_end, value_usd DESC",
            [int(manager_cik)],
        ).df()

    def holdings_periods(self, manager_cik: int) -> list[date]:
        rows = self.con.execute(
            "SELECT DISTINCT period_end FROM holdings13f WHERE manager_cik = ? ORDER BY period_end",
            [int(manager_cik)],
        ).fetchall()
        return [_as_date(r[0]) for r in rows]

    def upsert_insider_tx(self, frame: pd.DataFrame) -> int:
        if frame is None or frame.empty:
            return 0
        rows = frame.copy()
        rows["fetched_at"] = utcnow()
        cols = [
            "row_id", "issuer_cik", "accession", "filed_at", "trans_date", "owner", "is_director",
            "is_officer", "code", "shares", "price", "acquired", "fetched_at",
        ]
        self.con.register("new_insider", rows[cols])
        try:
            self.con.execute("INSERT OR REPLACE INTO insider_tx SELECT * FROM new_insider")
        finally:
            self.con.unregister("new_insider")
        return int(len(rows))

    def read_insider_tx(self, issuer_cik: int, *, since: date | None = None) -> pd.DataFrame:
        params: list = [int(issuer_cik)]
        clause = ""
        if since is not None:
            clause = " AND filed_at >= ?"
            params.append(_as_date(since))
        return self.con.execute(
            f"SELECT * FROM insider_tx WHERE issuer_cik = ?{clause} ORDER BY filed_at", params
        ).df()

    # ------------------------------------------------------------------
    # runs and snapshots
    # ------------------------------------------------------------------

    def start_run(self, kind: str) -> str:
        run_id = new_run_id()
        self.con.execute(
            "INSERT INTO runs (run_id, kind, started) VALUES (?, ?, ?)", [run_id, kind, utcnow()]
        )
        return run_id

    def finish_run(self, run_id: str, *, ok: int, failed: int, notes: str = "") -> None:
        self.con.execute(
            "UPDATE runs SET finished = ?, sources_ok = ?, sources_failed = ?, notes = ? "
            "WHERE run_id = ?",
            [utcnow(), int(ok), int(failed), notes[:4000], run_id],
        )

    def runs(self, kind: str | None = None, limit: int = 50) -> pd.DataFrame:
        if kind:
            return self.con.execute(
                "SELECT * FROM runs WHERE kind = ? ORDER BY started DESC LIMIT ?", [kind, limit]
            ).df()
        return self.con.execute("SELECT * FROM runs ORDER BY started DESC LIMIT ?", [limit]).df()

    def save_snapshot(self, run_id: str, kind: str, payload) -> None:
        self.con.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
            [run_id, kind, utcnow(), json.dumps(payload, default=str)],
        )

    def load_snapshot(self, kind: str, *, before: datetime | None = None, offset: int = 0):
        """Latest snapshot of ``kind`` (or the ``offset``-th latest). ``None`` if absent.

        Returns ``(run_id, created_at, payload)``.
        """
        params: list = [kind]
        clause = ""
        if before is not None:
            clause = " AND created_at < ?"
            params.append(before)
        params += [int(offset)]
        row = self.con.execute(
            f"SELECT run_id, created_at, payload FROM snapshots WHERE kind = ?{clause} "
            f"ORDER BY created_at DESC, rowid DESC LIMIT 1 OFFSET ?",
            params,
        ).fetchone()
        if row is None:
            return None
        return row[0], row[1], json.loads(row[2])

    def snapshots(self, kind: str, limit: int = 50) -> pd.DataFrame:
        return self.con.execute(
            "SELECT run_id, created_at FROM snapshots WHERE kind = ? ORDER BY created_at DESC LIMIT ?",
            [kind, limit],
        ).df()

    # ------------------------------------------------------------------
    # watchlist and files
    # ------------------------------------------------------------------

    def add_watch(self, symbol: str, *, cik: int | None = None, note: str = "") -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO watchlist VALUES (?, ?, ?, ?)",
            [symbol.upper(), cik, note, utcnow()],
        )

    def remove_watch(self, symbol: str) -> None:
        self.con.execute("DELETE FROM watchlist WHERE symbol = ?", [symbol.upper()])

    def watchlist(self) -> pd.DataFrame:
        return self.con.execute("SELECT * FROM watchlist ORDER BY added_at").df()

    def record_file(
        self, name: str, *, url: str | None, sha256: str, n_bytes: int, path: str | None
    ) -> None:
        self.con.execute(
            "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?)",
            [name, url, sha256, int(n_bytes), utcnow(), path],
        )

    def file_history(self, name: str) -> pd.DataFrame:
        return self.con.execute(
            "SELECT * FROM files WHERE name = ? ORDER BY fetched_at DESC", [name]
        ).df()
