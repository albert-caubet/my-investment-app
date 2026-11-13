# The market database

Everything the analysis side of the app stores lives in one DuckDB file:
- the macro series and their revisions,
- daily prices,
- the companies' reported accounts,
- the screener tables,
- 13F holdings and insider trades,
- the history of job runs,
- the watchlist.

The portfolio's transactions and cash accounts are not here; they are in Firestore.

The schema and every read and write go through one module, [invest/data/cache.py](invest/data/cache.py):
`SCHEMA` creates the tables, and the `Store` class wraps each query.

## Where it is

| What | Where |
|---|---|
| The database | `data/market.duckdb`, about 350 MB |
| Move the whole data folder | `INVEST_DATA_DIR` (default `data/`) |
| Point at another database file | `INVEST_DB` (the tests use this) |

The paths are set in [invest/paths.py](invest/paths.py). The `data/` folder is git-ignored. Besides the database it holds:

| Folder or file | What it is |
|---|---|
| `downloads/` | Raw files the refresh downloads (Shiller's data, the NY Fed's recession probabilities…), kept as they arrived; the `files` table records each one |
| `reports/` | The weekly reports, as Markdown and HTML |
| `logs/` | The logs of the jobs started from the app |
| `backups/` | Copies made by `python -m invest.jobs.backup`, which checkpoints the database, copies it and keeps the last 14 |
| `ui_catalog.duckdb` | DuckDB UI's own storage, not app data (see [Looking at it](#looking-at-it)) |

## Who writes and who reads

The jobs are the only writers. DuckDB lets one process write a file at a time, and only
while no other process has it open, so the pages open it read-only. While a job started
from the app is writing, the pages show its progress instead and reload when it ends
(`job_panel.follow_if_running`). The one write a page makes is to the watchlist, which
opens the file only for as long as the change takes.

| Command | Writes |
|---|---|
| `python -m invest.jobs.refresh` (the Macro page's refresh) | `series`, `fetches`, `prices`, `files`, `runs`, `snapshots` (`freshness`, `scorecard`) |
| `python -m invest.jobs.vintages` | `series` (ALFRED's past versions of revised series) |
| `python -m invest.jobs.fundamentals [--universe stoxx600]` (the Screener's build button) | `facts`, `companies`, `prices`, `snapshots` (`screener`, `screener_eu`), `series` (two breadth series, US build only), `runs` |
| `python -m invest.jobs.filings [--insiders CIK,CIK]` | `holdings13f`, `insider_tx`, `runs` |
| `python -m invest.jobs.weekly` | `snapshots` (`report`), `runs` |
| The research scripts (`invest/research/hypotheses.py`) | `snapshots` (`research`), `runs` |
| The Screener, Company financials, Stock explorer and Watchlist pages (through [watchlist.py](watchlist.py)) | `watchlist` |

## The tables at a glance

Row counts as of 13 November 2026.

| Table | One row is | Rows | Read by |
|---|---|---:|---|
| [`series`](#series) | One value of a macro series at one date, as known from one date (its vintage) | 651,869 | Macro page, Stock explorer, scorecard, weekly report, research, the fundamentals build (EUR/USD) |
| [`fetches`](#fetches) | One attempt by the refresh to fetch one series | 1,276 | Nothing in the app; kept for inspection |
| [`prices`](#prices) | One daily close of one symbol | 2,612,094 | Macro page (market charts), Screener and Company financials pages (share prices), the fundamentals build |
| [`facts`](#facts) | One figure a company reported | 880,004 | The fundamentals build, Screener and Company financials pages (fiscal-year history) |
| [`companies`](#companies) | One company of a screener universe | 739 | The fundamentals build; the watchlist (a ticker's CIK) |
| [`snapshots`](#snapshots) | One computed result, stored as JSON: a screener table, the scorecard, a report… | 19 | Screener, Company financials, Watchlist and Macro pages, weekly report |
| [`runs`](#runs) | One run of a job | 14 | Weekly report page (its run history) |
| [`holdings13f`](#holdings13f) | One position in one quarter's 13F filing of one manager | 285 | The filings job, weekly report |
| [`insider_tx`](#insider_tx) | One transaction in a Form 4 filing | 0 | Weekly report |
| [`watchlist`](#watchlist) | One stock on the watchlist | 11 | Watchlist page, the pages with a watchlist button, weekly report |
| [`files`](#files) | One download of a raw file | 18 | Nothing in the app; kept for provenance |

## Conventions

- **Times.** Every `fetched_at` is a timestamp in UTC without a time zone. Dates are plain `DATE`s.
- **Company ids.** The `cik` columns hold the SEC's Central Index Key for a US filer. A company whose accounts come from Yahoo Finance has no CIK. It is stored under a negative id derived from its Yahoo symbol (`yahoo_id` in [invest/fundamentals/universe.py](invest/fundamentals/universe.py): `-1 - (crc32(SYMBOL) & 0x7FFFFFFF)` of the upper-case symbol). The id is the same in every database, and a negative id never clashes with a CIK.
- **What was known when.** Two tables keep what was knowable on each day, so a past date can be replayed without hindsight:
  - `series` keeps every vintage of an observation.
  - `facts` keeps every filed or first-seen version of a figure.

  Both are read "as of" a date (`read_series(as_of=…)`, `read_facts(as_of=…)`).
- **Nothing fills a gap.** A missing observation or figure stays missing.
- **How each table changes:**

  | Table | A new fetch… |
  |---|---|
  | `series` | appends; never updates or deletes |
  | `facts` | adds new facts; a restated figure is a new row beside the old one |
  | `snapshots`, `runs`, `fetches`, `files` | appends |
  | `prices` | replaces the row with the same symbol and date |
  | `companies` | replaces the row with the same id |
  | `holdings13f`, `insider_tx` | replaces the row with the same id |
  | `watchlist` | adds, replaces or deletes by symbol |

## The tables

### series

Every macro series the refresh job stores, with every revision.

| Column | Type | Meaning |
|---|---|---|
| `source` | VARCHAR | Where it came from (see below) |
| `series_id` | VARCHAR | The catalog id, e.g. `US_UNRATE` |
| `obs_date` | DATE | The date the value is for |
| `value` | DOUBLE | The value, as the source gives it; the catalog's transform (year-on-year and so on) is applied when it is read |
| `fetched_at` | TIMESTAMP | When it was fetched |
| `vintage_date` | DATE | From when it was known: the fetch date, a hand-entered release's date, or an ALFRED vintage's first-publication date |

Indexed on `(series_id, obs_date)`.

**How values are added.**
- The refresh adds a row only when an observation is new or its value has changed (`upsert_series`). A second identical refresh adds nothing.
- The vintages job adds each first-published value with its own vintage date (`insert_vintages`).
- `read_series(id, as_of=D)` takes, for each observation, the latest vintage on or before `D`.

**Where the series come from.** The series are defined in the catalog,
[config/series.toml](config/series.toml) (207 entries). Hand-entered releases come from
[config/releases.toml](config/releases.toml) and are stored with the source `release`. The
two breadth series, the share of the US universe above its 200-day average and new highs
minus new lows, are computed by the fundamentals build (`computed`).

**Contents.** 173 series from 14 sources; 79 series have at least one observation stored in more than one vintage.

| Source | Series | Rows | From | To |
|---|---:|---:|---|---|
| `fred` (FRED, ALFRED) | 78 | 243,444 | 1854-12-01 | 2026-11-06 |
| `derived` (computed from other series by formula) | 36 | 230,432 | 1871-01-01 | 2026-11-06 |
| `yahoo` | 13 | 118,416 | 1927-12-30 | 2026-11-06 |
| `ecb` | 9 | 18,705 | 1990-01-01 | 2026-11-05 |
| `shiller` (Robert Shiller's market data) | 6 | 13,888 | 1871-01-01 | 2026-10-01 |
| `computed` (breadth) | 2 | 11,019 | 2015-07-21 | 2026-11-06 |
| `eurostat` | 13 | 4,202 | 1980-01-01 | 2026-10-01 |
| `ebp` (Fed's excess bond premium) | 2 | 3,868 | 1973-01-01 | 2026-10-01 |
| `oecd` | 2 | 3,154 | 1960-01-01 | 2026-09-01 |
| `cot` (CFTC positioning) | 4 | 1,524 | 2017-01-03 | 2026-11-03 |
| `philly` (Philadelphia Fed survey) | 2 | 1,404 | 1968-05-01 | 2026-10-01 |
| `nyfed_recprob` (NY Fed recession probability) | 1 | 814 | 1959-01-01 | 2026-10-01 |
| `bis` | 3 | 519 | 1960-01-01 | 2026-01-01 |
| `hlw` (Holston-Laubach-Williams natural rate) | 2 | 480 | 1961-01-01 | 2026-04-01 |

The latest value of each observation of one series:

```sql
SELECT obs_date, value
FROM series
WHERE series_id = 'US_UNRATE'
QUALIFY row_number() OVER (PARTITION BY obs_date ORDER BY vintage_date DESC, fetched_at DESC) = 1
ORDER BY obs_date DESC
LIMIT 5;
```

### fetches

The refresh job's log: one row per series per run, whatever the outcome.

| Column | Type | Meaning |
|---|---|---|
| `source` | VARCHAR | The series' source |
| `series_id` | VARCHAR | The catalog id, or the Yahoo symbol of a market chart |
| `key` | VARCHAR | What was asked of the source (a FRED id, a Yahoo symbol, a derived series' formula) |
| `started`, `finished` | TIMESTAMP | When the attempt began and ended |
| `status` | VARCHAR | `ok`, `failed` or `empty` |
| `message` | VARCHAR | Why it failed, or a note (up to 2,000 characters) |
| `n_rows` | INTEGER | Rows added (a series) or fetched (a market chart's symbol) |

**Contents.** 1,255 `ok` and 21 `failed`, the latest on 7 November 2026.

### prices

Daily closes: the screener's companies, the Macro page's market charts, and the exchange rates the European table needs.

| Column | Type | Meaning |
|---|---|---|
| `symbol` | VARCHAR | The Yahoo symbol: `MSFT`, `SAP.DE`, `^GSPC`, `GBP=X` |
| `date` | DATE | The trading day |
| `close` | DOUBLE | The close as quoted: adjusted for splits, not for dividends |
| `adj_close` | DOUBLE | Adjusted for dividends too, for returns |
| `currency` | VARCHAR | The currency of the quote. Mind the minor units: `GBp` is pence. NULL for what the refresh stores, which records none |
| `fetched_at` | TIMESTAMP | When it was fetched |

Primary key `(symbol, date)`: a refetch replaces the day.

**Who writes it.**
- The fundamentals build stores 12 years per company, with its quote currency. For the European table it also stores the exchange rates, as `XXX=X`, which closes in units of `XXX` per US dollar.
- The refresh stores the full history of the symbols in [config/markets.toml](config/markets.toml) and of the catalog's Yahoo series.

**Contents.** 817 symbols, closes from 1971 (indices from 1927) to 12 November 2026:

| Kind | Symbols | Rows |
|---|---:|---:|
| Stocks, ETFs, futures and the rest | 775 | 2,325,382 |
| Indices (`^…`) | 33 | 258,891 |
| Exchange rates (`…=X`) | 9 | 27,821 |

By quote currency: EUR 316, USD 141, GBp 116, CHF 59, SEK 50, DKK 26, NOK 21, PLN 16, GBP 1, GEL 1, none 70.

### facts

The companies' reported figures, one row per value as reported. These are the raw facts:
there is no stored "revenue for 2024" row. The canonical fields (revenue, net income,
total assets and the rest) are worked out each time the accounts are read, by
`fundamentals_as_of` in [invest/fundamentals/facts.py](invest/fundamentals/facts.py). It
takes, per field, the first tag in an ordered list of fallbacks that a company reported,
because companies tag the same item differently and switch tags over time. Only the tags
some field reads are stored.

| Column | Type | Meaning |
|---|---|---|
| `fact_id` | VARCHAR | Primary key, a hash. From EDGAR: of company, taxonomy, tag, unit, period and filing. From Yahoo: of company, item, unit, period and value |
| `cik` | INTEGER | The company id (see [Conventions](#conventions)) |
| `taxonomy` | VARCHAR | `us-gaap`, `ifrs-full` or `dei` from EDGAR; `yahoo` from Yahoo Finance |
| `tag` | VARCHAR | The concept: `Revenues`, `NetIncomeLoss`… from EDGAR; `TotalRevenue`… from Yahoo |
| `unit` | VARCHAR | A currency (`USD`, `EUR`…), `shares`, or per share (`USD/shares`) |
| `start_date` | DATE | Where the period begins; NULL for a balance at a date |
| `end_date` | DATE | Where it ends, or the balance's date |
| `value` | DOUBLE | The figure, in units, not thousands |
| `fy`, `fp` | INTEGER, VARCHAR | From EDGAR: the fiscal year and period of the **filing**, not of the figure (see below). From Yahoo: the figure's own, with `fp` `FY`, `Q` (a quarter's balance) or `TTM` (trailing twelve months) |
| `form` | VARCHAR | `10-K`, `10-Q`, `8-K`, an amendment…; `Yahoo annual`, `Yahoo quarterly` or `Yahoo trailing` |
| `filed_at` | DATE | When it became public: the filing date for EDGAR. For Yahoo, which has none, the day the value was first fetched |
| `accession` | VARCHAR | The EDGAR filing's accession number; `yahoo-YYYYMMDD` for Yahoo |
| `frame` | VARCHAR | EDGAR's calendar frame, e.g. `CY2024Q4I`; NULL for Yahoo |
| `fetched_at` | TIMESTAMP | When it was fetched |

Indexed on `(cik, tag)`.

**Two things to know before querying it.**
- **A 10-K reports earlier years too.** Its comparatives carry the filing's `fy` and `fp`, and a 10-Q carries both three-month and year-to-date figures. Select periods by `start_date` and `end_date`, as the app does: a fiscal year spans 340 to 380 days.
- **Revisions are kept.**
  - **EDGAR:** a figure restated in a later filing is a second row, with that filing's date.
  - **Yahoo:** a revised value is a new row, dated the day it was first seen; a value fetched again keeps its first day.

  Reading "as of" a date takes only rows filed by then (`read_facts(cik, as_of=…)`), which is what makes the US screener point-in-time.

**Why US and European histories differ in length.** EDGAR's facts go back to the late
2000s, about 20 fiscal years for large US filers. Yahoo gives only a company's last four
or five fiscal years, whatever is asked for. Because the build keeps every value it has
seen, the European history grows by a year with each year's rebuild.

**Contents.**
- 880,004 facts for 739 companies, filed from 15 April 2009 to 12 November 2026.
- Forms: 10-Q 503,631, 10-K 222,395, Yahoo annual 88,295, Yahoo quarterly 26,462, Yahoo trailing 21,521, 8-K 13,087, amendments and proxies the rest.
- No `ifrs-full` facts at present: no filer reporting in IFRS is in the US universe.

| Taxonomy | Companies | Facts | Tags | Periods ending |
|---|---:|---:|---:|---|
| `us-gaap` | 139 | 732,332 | 61 | 2005-12-31 to 2026-10-04 |
| `dei` (cover-page share counts and public float) | 139 | 11,394 | 2 | 2008-03-28 to 2026-11-02 |
| `yahoo` | 600 | 136,278 | 43 | 2017-09-30 to 2026-09-30 |

One company's annual revenue, every version filed:

```sql
SELECT end_date, value / 1e6 AS usd_m, form, filed_at
FROM facts
WHERE cik = (SELECT cik FROM companies WHERE ticker = 'MSFT')
  AND tag = 'RevenueFromContractWithCustomerExcludingAssessedTax'
  AND date_diff('day', start_date, end_date) BETWEEN 340 AND 380
ORDER BY end_date DESC, filed_at DESC
LIMIT 6;
```

### companies

One row per company of a screener universe, replaced by each build.

| Column | Type | Meaning |
|---|---|---|
| `cik` | INTEGER | Primary key: the company id (see [Conventions](#conventions)) |
| `ticker` | VARCHAR | As the universe lists it: the SEC ticker (`BRK-B`) or the Yahoo symbol (`SAP.DE`) |
| `name` | VARCHAR | As EDGAR or Yahoo gives it |
| `sic` | VARCHAR | The SEC's industry code; NULL for Yahoo |
| `sector` | VARCHAR | For a filer, a coarse label from its SIC code (`SIC_SECTORS` in [invest/fundamentals/universe.py](invest/fundamentals/universe.py)); for Yahoo, Yahoo's sector in lower case |
| `exchange` | VARCHAR | The listing exchange |
| `fetched_at` | TIMESTAMP | When the row was written |

**Contents.** 139 filers from EDGAR (20 sectors, every one with a SIC code) and 600 companies from Yahoo (12 sectors).

### snapshots

Computed results stored whole, as JSON, one row per result. A reader takes the latest of a
kind, or the one before it to compare (`load_snapshot(kind, offset=1)`).

| Column | Type | Meaning |
|---|---|---|
| `run_id` | VARCHAR | The run that made it (see [runs](#runs)) |
| `kind` | VARCHAR | What it is (below) |
| `created_at` | TIMESTAMP | When it was saved |
| `payload` | VARCHAR | The result, as JSON text |

| Kind | Written by | Payload |
|---|---|---|
| `screener` | The fundamentals build of the US universe | A screener table: `universe`, `universe_label`, `region`, `source`, `as_of`, `point_in_time`, `n_companies` and `rows`, one object per company (below). The three US tables stored so far were built before the European market existed and lack `region` and `source` |
| `screener_eu` | The fundamentals build of the European universe | The same, each row with `market_cap_eur` too |
| `freshness` | The refresh | A list, one object per catalog series: `id`, `source`, `status`, `last_obs`, `age_days`, `stale_after`, `critical`, `message` |
| `scorecard` | The refresh | The macro scorecard: `readings`, `regime` and `rules` |
| `report` | The weekly job | The weekly report's run: `date`, `paths` of the files written, `failures`, `statuses`, `summary` |
| `research` | The research scripts | A hypothesis test's result |

A screener row holds everything the Screener and the Company financials pages show of a company:
- **Who it is:** `cik`, `ticker`, `name`, `sic`, `sector`, `is_financial`, `currency` (of the accounts).
- **Its price:** `price` and `price_date`, in the quote's currency.
- **The period:** `period_end`, `fy_end`, and `basis` (`ttm` or the latest fiscal year).
- **The measures:** `shares_outstanding`, `market_cap`, `enterprise_value`, `total_debt`, `net_debt`, `ebit`, `ebitda`, `earnings_yield`, `roic`, `fcf`, `fcf_yield`, `gross_margin`, `operating_margin`, `net_debt_to_ebitda`, `interest_coverage`, `accruals`, `shareholder_yield`, `pe`, `pb`, `ev_ebitda`, `ev_sales`, `p_fcf`, `altman_z`, `gross_margin_slope`, `operating_margin_slope`, `operating_margin_stability`, `revenue_cagr_3y`, `earnings_cagr_3y`, `dilution_3y`, `share_count_change_1y`, `f_score`, `value_trap_flags`.
- **The valuation:** `epv_per_share`, `dcf_per_share`, `value_conservative` and `mos_conservative` (margin of safety).
- **What it rests on:** `f_checks` (the nine Piotroski checks), `valuation_inputs`, `tags` (the tag each field was read from) and `notes`.

The ranks are not stored: the Screener computes them for the screen chosen.

**Contents.**

| Kind | Rows | From | To | Largest payload |
|---|---:|---|---|---:|
| `screener` | 3 | 2026-10-04 | 2026-11-07 | 0.5 MB (139 companies) |
| `screener_eu` | 1 | 2026-11-12 | 2026-11-12 | 2.0 MB (600 companies) |
| `scorecard` | 6 | 2026-09-26 | 2026-11-07 | 0.1 MB |
| `freshness` | 6 | 2026-09-26 | 2026-11-07 | 37 KB (207 series) |
| `report` | 3 | 2026-10-04 | 2026-11-07 | 0.5 KB |

The five European companies with the widest margin of safety in the latest table, leaving
out banks, insurers and real estate as the Screener does by default (EBIT-based values do
not describe them):

```sql
WITH latest AS (
    SELECT payload FROM snapshots WHERE kind = 'screener_eu' ORDER BY created_at DESC LIMIT 1
), company AS (
    SELECT unnest(json_extract(payload, '$.rows[*]')) AS r FROM latest
)
SELECT r->>'ticker' AS ticker, r->>'name' AS name, (r->>'mos_conservative')::DOUBLE AS margin_of_safety
FROM company
WHERE NOT (r->>'is_financial')::BOOLEAN
ORDER BY margin_of_safety DESC NULLS LAST
LIMIT 5;
```

### runs

One row per job run, shown on the Weekly report page.

| Column | Type | Meaning |
|---|---|---|
| `run_id` | VARCHAR | Primary key: the start time in UTC and a random suffix, e.g. `20261112T224646-1a2b3c` |
| `kind` | VARCHAR | `refresh`, `fundamentals`, `filings`, `weekly` or `research` |
| `started` | TIMESTAMP | When it began |
| `finished` | TIMESTAMP | When it ended; NULL for a run that crashed or was stopped |
| `sources_ok`, `sources_failed` | INTEGER | What succeeded and what failed (series, companies…) |
| `notes` | VARCHAR | The problems, up to 4,000 characters |

**Contents.** 14 runs: 6 `refresh` (the latest on 7 November), 5 `fundamentals` (the latest on 12 November; one never finished), 3 `weekly` (the latest on 7 November).

### holdings13f

The 13F filings of the managers in [config/managers.toml](config/managers.toml): their long
positions in US-listed securities at each quarter end, filed up to 45 days later. Short
positions, cash and most non-US holdings are not in them. The filings job stores each
manager's last two quarters and prints what changed.

| Column | Type | Meaning |
|---|---|---|
| `row_id` | VARCHAR | Primary key: a hash of manager, filing, CUSIP, share class and put or call |
| `manager_cik` | INTEGER | The manager's CIK |
| `manager` | VARCHAR | As EDGAR names the filer |
| `period_end` | DATE | The quarter end the holdings are at |
| `filed_at` | DATE | When the filing was made |
| `accession` | VARCHAR | The filing's accession number |
| `cusip` | VARCHAR | The security's CUSIP |
| `issuer` | VARCHAR | The issuer's name, as filed |
| `title_class` | VARCHAR | The class: `COM`, `CL A`… |
| `value_usd` | DOUBLE | The position's value in dollars; filings before 2023, which reported thousands, are converted |
| `shares` | DOUBLE | Shares, or principal amount for a bond |
| `sh_prn` | VARCHAR | `SH` for shares, `PRN` for principal |
| `put_call` | VARCHAR | `Put` or `Call` for an option position, empty for shares |
| `discretion` | VARCHAR | Investment discretion: `SOLE`, `DFND` (shared) or `OTR` |
| `fetched_at` | TIMESTAMP | When it was fetched |

A position reported in several lines (for several sub-accounts) is summed into one row.

**Contents.** 285 positions of 6 managers, quarters from 30 September 2023 to 30 June 2026; 15 are options.

### insider_tx

Form 4 filings: trades by a company's directors and officers in its own shares. Only
`python -m invest.jobs.filings --insiders CIK,CIK` writes them. The weekly report reads the
trades of the watchlist's companies that have a CIK.

| Column | Type | Meaning |
|---|---|---|
| `row_id` | VARCHAR | Primary key: a hash of the filing and the transaction |
| `issuer_cik` | INTEGER | The company's CIK |
| `accession` | VARCHAR | The Form 4's accession number |
| `filed_at` | DATE | When it was filed |
| `trans_date` | DATE | When the trade was made |
| `owner` | VARCHAR | Who traded |
| `is_director`, `is_officer` | BOOLEAN | Their role |
| `code` | VARCHAR | The SEC's transaction code: `P` an open-market purchase, `S` a sale; others are grants (`A`), option exercises (`M`), shares withheld for tax (`F`), gifts (`G`)… |
| `shares` | DOUBLE | Shares traded |
| `price` | DOUBLE | Price per share |
| `acquired` | BOOLEAN | True for shares acquired, false for disposed of |
| `fetched_at` | TIMESTAMP | When it was fetched |

**Contents.** Empty: no insider trades are stored.

### watchlist

The stocks put on the watchlist from the Screener, the Company financials page or the Stock explorer.

| Column | Type | Meaning |
|---|---|---|
| `symbol` | VARCHAR | Primary key: the Yahoo symbol, in upper case |
| `cik` | INTEGER | The SEC CIK, by which the weekly report finds insider trades; NULL for a company without one |
| `note` | VARCHAR | Where and when it was added, e.g. `from magic_formula on 2026-11-07` |
| `added_at` | TIMESTAMP | When it was added |

**Contents.** 11 stocks, 7 of them with a CIK.

### files

A record of each raw file the refresh downloads, kept in `data/downloads/`, so a figure can be traced to the exact file it was read from.

| Column | Type | Meaning |
|---|---|---|
| `name` | VARCHAR | The file's name |
| `url` | VARCHAR | Where it was downloaded from |
| `sha256` | VARCHAR | Its hash: two downloads with the same hash are the same file |
| `n_bytes` | INTEGER | Its size |
| `fetched_at` | TIMESTAMP | When it was downloaded |
| `path` | VARCHAR | Where it was saved |

**Contents.** 18 downloads, six each of `ie_data.xls` (Shiller), `hlw_estimates.xlsx` (Holston-Laubach-Williams) and `nyfed_allmonth.xls` (NY Fed recession probability), the latest on 7 November 2026.

## What is not in it

- **The portfolio's transactions and cash accounts:** Firestore.
- **A company the Company financials page fetches because it is in no screener table:** fetched into a throwaway in-memory database, and kept only in the app's cache for 12 hours.
- **What the dashboard, the Stock explorer and the Watchlist fetch from Yahoo Finance and FRED:** kept only in the app's cache, by [market_data.py](market_data.py).
- **The weekly reports:** files in `data/reports/`.

## Looking at it

- **In a browser:** the README's section [Browse the market database with DuckDB UI](README.md#browse-the-market-database-with-duckdb-ui) opens DuckDB's own viewer, with the database attached read-only.
- **From Python:**

  ```python
  import duckdb

  con = duckdb.connect("data/market.duckdb", read_only=True)
  con.sql("SHOW TABLES").show()
  con.sql("DESCRIBE facts").show()
  for (table,) in con.sql("SHOW TABLES").fetchall():  # the row counts above, now
      print(table, con.sql(f"SELECT count(*) FROM {table}").fetchone()[0])
  ```

Always open it read-only. Several readers can share the file, and the app keeps reading
while you do. A job needs it to itself to write: one started while your connection is open
fails to open the file, so close the connection before running a job.
