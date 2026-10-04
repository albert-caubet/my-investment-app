# My Investment App

A Streamlit investment portfolio dashboard. Track holdings across asset classes, log buys
and sells, and see valuation and performance in EUR.

## Features

- **Portfolio Dashboard**
  - Cost basis, market value, and unrealised / realised / total P&L, all in EUR.
  - Total Value (the investments), Portfolio value (investments plus Dry Powder) and the
    Dry Powder itself, with the cash reserve on its own line, outside the portfolio.
  - Open positions and Dry Powder accounts with their weight in the portfolio, plus a
    separate table of closed positions.
  - Allocation against the saved portfolio design: the largest gap as a metric, and a
    table of how far each category is off and the euros that would reach the design.
  - Portfolio distribution by category and by holding, Dry Powder included.
  - Per-asset price history with buy/sell markers, an average-cost line and the inflation
    break-even, over a time range picked from presets (1M to 10Y and Max, three years by
    default), two charts to a row by default (1 to 3) at a chosen height (small, medium,
    large), with the y axis in price or in % change from the first close of the time range;
    the choices are kept in the page URL.
  - Beta, Alpha and R² against the S&P 500, estimated in EUR over two years.
- **Transaction Logging**
  - Buys and sells across Stocks, ETFs, Funds, Crypto and more.
  - Ticker or ISIN, with ISINs resolved to a Yahoo symbol once at entry.
  - Historical FX captured on the trade date; fees included in the cost basis.
  - Select a row in the history log to edit or delete it. Saving rebuilds the document
    in the current schema, so a legacy row is upgraded on its first edit.
  - Cash accounts: one row per bank account with its balance in EUR, as *Cash* (current
    accounts: the day-to-day reserve and emergency fund) or *Dry Powder* (set aside to
    invest), edited in one table and saved together; an account is deleted with a
    confirmation, the way a transaction is.
- **Portfolio design** (Analysis)
  - A slider per category (Dry Powder, Bonds, Equity funds, Stocks) sets a target share of
    the portfolio. The sliders always add up to 100%: moving one takes the difference from
    the unlocked others, in proportion to their size, and a lock keeps a category fixed.
  - Today's allocation and the design are drawn as two bars, with the euros each category
    would need to move to get there. Save the design; the dashboard measures against it.
- **Cloud Database**
  - Firebase Firestore, for multi-device sync.

## Accounting model

Worth knowing, because these choices decide what the numbers mean:

- **Base currency is EUR.** Cost basis converts at the FX rate *on the trade date*;
  market value converts at *today's* rate. The difference between the two is your
  currency gain or loss, and it belongs in P&L.
- **Transaction currency and listing currency are different things.** The first is what
  left your bank account and drives cost basis. The second is what the asset is quoted
  in and drives market value. Paying euros for a USD-listed stock is normal, and the two
  are tracked separately.
- **Average cost.** A sell releases a proportional share of the running cost basis and
  books the difference as realised P&L. A closed position keeps its realised P&L and
  drops out of the open table. FIFO is not implemented.
- **Fees are capitalised** into the cost basis on a buy, and net off proceeds on a sell.
- **The portfolio is the investments plus Dry Powder; the cash reserve is outside it.**
  Bank balances are entered by hand and carry the date they were last saved. Dry Powder
  counts in Portfolio value, the weights, the charts and the design. Cash in current
  accounts is the day-to-day reserve and emergency fund: shown on its own, and in none of
  those. Neither is in Total Value or any P&L figure, which stay on the investments.
  A holding's design category follows the category it was logged with (Fund and ETF are
  Equity funds, Bonds and Fixed Income are Bonds, Cash/Money Market is Dry Powder), so a
  bond fund logged as *Fund* counts as an equity fund until its category is changed.
  Crypto, commodities and the like are Other, which a design targets at 0%.
- **Each P&L percentage is on the cost it was earned on.** Unrealised is on the open
  cost basis, realised on the cost of what was sold, and total on the two together,
  which is every euro of cost ever deployed. None of them carries time; the
  money-weighted return does.
- **An FX rate that cannot be established blocks the save.** It is never defaulted to
  1.0 — that would silently understate a USD cost basis by whatever the rate was, and
  freeze the error into the database.
- **A listing currency that cannot be established blocks valuation.** It is never
  defaulted to EUR, which would value a USD price one-for-one as euros. The currency
  detected when the trade was logged is stored with it and used as the fallback when
  the live lookup fails.
- **Charts use unadjusted closes**, so the line is on the same scale as the raw trade
  prices plotted on it. The CAPM series uses adjusted prices, which is correct for returns.

## Prerequisites

- **Python 3.13**
- A **Google Firebase** project with Firestore enabled
- A Firebase service account key (JSON)

## Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd my-investment-app
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Firebase Configuration**:
   - Place your Firebase service account JSON in the project root as
     `firebaseServiceAccountKey.json`, or put its contents in `FIREBASE_CREDENTIALS_JSON`.

## How to Run

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`.

## Tests

The money math lives in `portfolio_math.py`, which imports no Streamlit, no Firestore and
no network, so it can be tested directly:

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest -q
```

## Analysis package (`invest/`)

A second, independent part of the repository: macro indicators, a value screener and a
weekly rebalancing report, built phase by phase according to `PLAN.md`. It shares the
principles of the portfolio engine: pure functions tested on fixtures, every number
carrying its observation date and source, nothing filled in silently.

- **Storage**: one DuckDB file, `data/market.duckdb` (gitignored). Time series are
  append-only with a fetch time and a vintage date, so revisions stay visible and
  `read_series(as_of=...)` answers what was known on a given day. Firestore keeps
  transactions only, exactly as before.
- **Catalog**: `config/series.toml` lists every series with its source, key, frequency,
  publication lag, transform, direction of concern and criticality. Sources: FRED (no key
  needed; `FRED_API_KEY` adds ALFRED vintages), the ECB, Eurostat, the OECD and BIS SDMX
  APIs, Yahoo Finance, Shiller's data file, the Fed's excess bond premium, the Philadelphia
  and New York Fed files, CFTC positioning, plus `derived` series computed from stored ones
  and `release` series entered by hand in `config/releases.toml` (ISM, PMIs, LEI, nowcasts,
  whose histories are licensed).
- **Refresh**: `python -m invest.jobs.refresh` fetches the catalog, computes derived series,
  ingests releases and prints a freshness table (last observation, age, limit, status). It
  exits non-zero when a critical series failed or is older than its frequency and lag allow
  and no fallback covers it. A second identical run adds no rows. A full run (no `--only`)
  also downloads the daily closes of every symbol in `config/markets.toml` for the market
  charts; a symbol with no data is named in the output and the fetch log, without changing
  the exit status.
- **Macro page** (`pages/macro.py`): market charts at the top, then the regime label with
  the rules behind it, a scorecard by group (value, observation date, z-score, percentile,
  three-month change, concern), a chart per indicator with NBER recession shading and rule
  thresholds, a "changed since the previous run" panel, a form to record releases, and a
  freshness footer. It reads DuckDB only; every figure shows its date.
- **Market charts** (Macro page): world main indices, world small and mid caps, Europe's
  national main indices, Europe's mid caps and small caps, and commodities, each chart
  overlaying its series as the % change from each one's first close in the time range (1M
  to 10Y and Max; Max starts where every line has data), in its own currency. Two charts to
  a row by default (1 to 3) at a chosen height, as on the portfolio page; the choices are
  kept in the page URL. The series, their Yahoo symbols and a few words on what each index
  covers ("Greece, 60 largest"), printed under its chart, are in `config/markets.toml`.
  Where Yahoo keeps no history for an index, an ETF on the same index (or a close index)
  stands in, starred in the legend and described under the chart; an index with neither is
  listed there as not charted.
- **Fundamentals and screener**: `python -m invest.jobs.fundamentals` ingests XBRL company
  facts, SIC sectors and prices for a universe from `config/universe.toml`, builds one row per
  company (Greenblatt earnings yield and ROIC, FCF yield, margins and their slope, growth,
  leverage, Altman Z, accruals, Piotroski F-score, multiples, EPV and a conservative DCF with
  the margin of safety) and stores it as a snapshot, plus two breadth series for the macro
  scorecard. `--as-of DATE` builds the table using only facts filed by that date. The
  Screener page ranks the table (magic formula, quality-value composite, deep value,
  shareholder yield), shows every input, the fiscal-year history and the intrinsic-value
  inputs, charts the selected company's stored daily close over a range picked with presets
  (1M to Max) or a date slider, and keeps a watchlist in DuckDB.
- **Filings**: `python -m invest.jobs.filings` stores the last two 13F quarters for each
  manager in `config/managers.toml` and prints what changed (new positions, exits, changes
  above 25%, concentration), always with the filing lag and the long-only caveat stated.
  `--insiders CIK,CIK` aggregates Form 4 open-market buys and sells per issuer.
- **Weekly report**: `python -m invest.jobs.weekly` values the portfolio (Firestore, or a JSON
  export named by `INVEST_TRANSACTIONS_JSON`), computes drift against `config/targets.toml`
  and proposes bucket-level trades (new cash first, fund-to-fund legs flagged as possible
  traspasos, FIFO taxable gain per sale), then assembles the macro scorecard and regime, what
  changed since last week, the tracked managers' 13F changes, the top of each screen, the
  hypotheses due for review from `config/hypotheses.md`, and a "Missing and failed" section
  naming everything that could not be produced. Rendered to HTML with inline sparklines,
  archived under `data/reports/`, and sent by SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
  `SMTP_PASSWORD`, `REPORT_EMAIL_TO`) or Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)
  when configured. `scripts/weekly.ps1` and `scripts/weekly.sh` run the whole chain for Task
  Scheduler or cron on Saturday mornings. The Report page lists the archive and the runs, and
  its "Build the report now" button runs the same job in the background, fetching fresh data
  first or, with the box unticked, building from the data already stored.
- **Automation and hosting**: `.github/workflows/weekly.yml` runs the whole chain on GitHub
  Actions every Saturday (the DuckDB file is cached between runs, the report is an artifact
  and is delivered when the secrets are set); `.github/workflows/tests.yml` runs pytest on
  every push. `Dockerfile` and `docker-compose.yml` run the app and a jobs container
  (`scripts/scheduler.py`: weekly chain on Saturday 08:00, `python -m invest.jobs.backup`
  nightly, keeping the last 14 copies) on one volume, with secrets in a `.env` file (see
  `.env.example`). The app binds to localhost only: reach it through Tailscale or a Cloudflare
  Access tunnel, and add `st.login` with an OIDC provider in `.streamlit/secrets.toml` for a
  second layer. Firebase credentials can come from `FIREBASE_CREDENTIALS_JSON` (the key
  file's contents) instead of a key file. When the weekly job records a problem it
  sends a one-line alert by Telegram or email.
- **Research**: `invest/research/` holds the event-study and walk-forward utilities and
  `python -m invest.research.hypotheses H1|H5|H6|H7|H8|H11|rules`, which measures the
  hypotheses of `config/hypotheses.md` on the stored data (n and intervals printed, publication
  lags applied, results stored with the code version). `python -m invest.jobs.vintages` loads
  ALFRED vintages for the revision-prone series when a FRED key is available.
- **Secrets**: from the environment or `.streamlit/secrets.toml` (gitignored):
  `FRED_API_KEY` (optional), `SEC_USER_AGENT` (an app name plus a contact address, required
  by EDGAR; the client refuses to run without one).
- **Paths**: `INVEST_DATA_DIR` moves the database, downloads and report archive together;
  `INVEST_DB` overrides the database file alone.

## Project Structure

- `app.py`: Entry point and navigation.
- `portfolio_math.py`: Pure calculation engine — positions, cost basis, valuation, CAPM.
  No I/O, so every figure it produces is unit-testable.
- `market_data.py`: All network access and caching (prices, FX, metadata, ISIN lookup).
- `database.py`: Firestore initialisation and helpers.
- `pages/portfolio.py`: Dashboard — loads, computes, renders.
- `pages/transactions.py`: Trade entry and history log.
- `pages/macro.py`, `pages/screener.py`, `pages/report.py`: the analysis pages; they read
  DuckDB only.
- `invest/`: the analysis package. `data/` (DuckDB store and every source client), `macro/`
  (catalog, indicators, derived series, regime rules, scorecard, market charts), `positioning/` (COT, 13F,
  Form 4), `fundamentals/` (facts, metrics, screens, valuation, universe), `portfolio/`
  (rebalancing, FIFO lots, valuation for the jobs), `report/` (build, render, deliver,
  hypotheses), `research/` (event studies, walk-forward, hypothesis scripts), `jobs/`
  (refresh, filings, fundamentals, weekly, backup, vintages, notify).
- `config/`: `series.toml` (indicator catalog), `releases.toml` (hand-entered headlines),
  `targets.toml` (allocation and bands), `universe.toml` (screener tickers), `managers.toml`
  (13F filers), `hypotheses.md` (the register).
- `scripts/`: scheduler entry points for Task Scheduler, cron and the jobs container.
- `data/` (gitignored): `market.duckdb`, downloads, the report archive, backups.
- `tests/`: pytest suite over `portfolio_math.py` and every `invest` module, source parsers
  on saved responses in `tests/fixtures/`, a golden weekly report, plus headless smoke tests
  that run every page on canned data.
- `requirements.txt` / `requirements-dev.txt`: Runtime and test dependencies.
- `firebaseServiceAccountKey.json`: (Not in repo) Your private Firebase credentials.
