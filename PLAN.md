# Plan: macro analysis, value screener, weekly rebalancing report

Draft v1, 2026-09-10. Companion to the portfolio app in this repo. Meant to be worked
through phase by phase; tick the boxes as you go and edit freely.

## 0. What gets built

Three things, in order, each usable on its own:

1. **Macro dashboard.** Where we are in the cycle, with every figure dated and sourced.
2. **Value screener.** Rank companies on fundamentals, as they were known at a given date,
   with an explicit margin of safety.
3. **Weekly report.** Portfolio drift and proposed rebalancing trades, the macro regime, new
   candidates from the screener, and what changed since last week. Run by hand first, then
   on a schedule, then self-hosted.

Later, and only once the above have produced dated history: a research harness that tests
the hypotheses in section 9 (event studies, regime rules, and if still tempting, models).

## 1. Principles

Carried over from the portfolio engine, because they are what made its numbers trustworthy.

- **Pure core, I/O at the edges.** Every indicator, metric and rule is a pure function over
  data frames, tested against hand-computed fixtures. Fetching lives in source modules.
- **Every number carries its date.** An indicator is a value plus its observation date,
  source, series id and fetch time. The report never shows a figure without an as-of date.
  This is the HICP lesson from the current dashboard.
- **No silent defaults.** A missing series is reported missing, never filled with a guess. A
  failed source appears in the report's "missing and failed" section instead of vanishing.
- **EUR is the base currency.** US data is shown in USD, but anything compared with the
  portfolio is converted at the dated FX rate.
- **Releases, lags and revisions are part of the data.** Backtests use vintage data where it
  exists (ALFRED), apply the publication lag explicitly otherwise, or are labelled as not
  point-in-time.
- **Rules before models.** Every signal starts as a documented rule with a measured
  historical hit rate and a printed n. A model earns its place by beating the rule out of
  sample.
- **Small steps, each shippable.** No phase requires refactoring the working app first.

## 2. Architecture

### 2.1 Layout

A new package next to the existing app. The current modules stay where they are until a
phase needs them inside the package; then they move behind an import shim.

```
invest/
  data/           fred.py, ecb.py, eurostat.py, edgar.py, yahoo.py, files.py (csv/xls
                  downloads), cache.py (DuckDB read/write with provenance)
  macro/          catalog.py (series list + metadata), indicators.py (pure),
                  regimes.py (pure rules)
  positioning/    cot.py, sentiment.py (AAII, NAAIM), flows.py, filings13f.py, insiders.py
  fundamentals/   facts.py (XBRL tag mapping, as-of queries), metrics.py (pure),
                  screens.py (pure), universe.py, valuation.py (EPV, DCF)
  portfolio/      rebalance.py (pure), lots.py (FIFO tax lots, pure);
                  later home of portfolio_math.py
  report/         build.py (assemble sections), render.py (Markdown -> HTML), templates/
  jobs/           refresh.py (data only), weekly.py (refresh + build + deliver)
pages/            macro.py, screener.py, report.py added to the Streamlit app
config/           series.toml, targets.toml, universe.toml, managers.toml, hypotheses.md
data/             market.duckdb, parquet/, reports/   (gitignored; reports archived elsewhere)
tests/            one fixture per source, golden report, AppTest for each new page
```

### 2.2 Storage

- **Firestore** keeps transactions only, exactly as now.
- **DuckDB**, one file at `data/market.duckdb`, for everything else: time series, XBRL facts,
  filings, screener snapshots, report runs. Single file, SQL, fast, no server. Parquet
  exports for portability and backups.
- Tables: `series(source, series_id, obs_date, value, fetched_at, vintage_date)`,
  `facts(cik, tag, unit, period_end, filed_at, value, form, accession)`,
  `prices(symbol, date, close, adj_close, currency)`, `snapshots(run_id, kind, payload)`,
  `runs(run_id, started, finished, sources_ok, sources_failed)`.
- Fetches are append-only with `fetched_at`, so revisions stay visible and a backtest can ask
  "what did we know on date D".

### 2.3 Configuration and secrets

- `config/series.toml`: the indicator catalog. Per series: id, source, key, frequency,
  publication lag, transform, direction of concern, z-score window, fallback series.
- `config/targets.toml`: strategic weights per bucket, tolerance bands, minimum trade size,
  tactical tilt limits (default zero).
- `config/universe.toml`: indices, tickers and CIKs the screener covers.
- `config/managers.toml`: 13F filers to track.
- Secrets from the environment or `.streamlit/secrets.toml` (gitignored): FRED key, SEC
  User-Agent contact, SMTP or Telegram token, Firebase credentials as a dict. Same rule as
  the Firebase key today: never in code, never in git.

### 2.4 Runtime

Two commands do everything: `python -m invest.jobs.refresh` (fetch) and
`python -m invest.jobs.weekly` (fetch, build, render, deliver). The Streamlit pages read
from DuckDB and never from the network, so the app stays fast and the job is the only writer.

## 3. Data sources

All free. Verify keys and availability when implementing; FRED series do get discontinued
(the Wilshire 5000 series stopped in 2024), so every critical entry in the catalog names a
fallback, and the job fails loudly when a series is older than its expected lag allows.

### 3.1 US macro: FRED (free API key)

| Block | Series | Cadence |
|---|---|---|
| Yield curve | DGS3MO, DGS2, DGS10, DGS30, T10Y3M, T10Y2Y | daily |
| Policy | FEDFUNDS, DFF; the neutral rate r* comes from the NY Fed (3.7) | monthly, daily |
| Inflation | CPIAUCSL, CPILFESL, PCEPILFE; breakeven T10YIE; real yields DFII5, DFII10; better cores: MEDCPIM158SFRBCLE (Cleveland median CPI), PCETRIM12M159SFRBDAL (Dallas trimmed-mean PCE), CORESTICKM159SFRBATL (Atlanta sticky CPI); expectations MICH (Michigan 1-year) | monthly; daily |
| Labor | ICSA, CCSA (weekly, Thu); UNRATE, UNEMPLOY, PAYEMS (monthly, first Fri); SAHMREALTIME; JTSJOL, JTSQUR; TEMPHELPS (temporary help, leads payrolls); AWHMAN (manufacturing hours, an LEI component); CES0500000003 (hourly earnings, deflated by CPI for real wages) | weekly, monthly |
| Housing | PERMIT (building permits, the lead), HOUST, EXHOSLUSM495S (existing home sales), MORTGAGE30US | monthly, weekly |
| Business investment | NEWORDER (core capital goods orders), TCU (capacity utilisation), CFNAI and CFNAIMA3 (Chicago Fed activity index) | monthly |
| Consumer | UMCSENT, PSAVERT, DSPIC96, RSAFS, RRSFS, REVOLSL, TOTALSL, TDSP (quarterly), DRCCLACBS, DRSFRMACBS (quarterly) | monthly, quarterly |
| Credit and stress | BAMLH0A0HYM2 (HY OAS), BAMLC0A0CM (IG OAS), BAA10Y, NFCI, ANFCI, STLFSI4; DRTSCILM (loan officer survey, net share of banks tightening on business loans) | daily, weekly, quarterly |
| Liquidity | WALCL (Wed), WTREGEN (Treasury account), RRPONTSYD, M2SL | weekly, daily, monthly |
| Dollar | DTWEXBGS (broad trade-weighted dollar); DXY via yfinance (`DX-Y.NYB`) | daily |
| Fiscal | GFDEGDQ188S (debt / GDP), FYFSGDA188S (deficit / GDP, annual), federal interest payments (BEA, on FRED as A091RC1Q027SBEA; verify), Monthly Treasury Statement via fiscaldata.treasury.gov | quarterly, monthly |
| Activity and profits | INDPRO, GDP, GDPC1, CP (corporate profits after tax), NCBEILQ027S (corporate equities, Z.1) | monthly, quarterly |
| Oil and fuel | DCOILWTICO, DCOILBRENTEU, GASREGW | daily, weekly |
| Volatility | VIXCLS | daily |
| Recessions | USREC (for chart shading and event studies) | monthly |
| Distribution | Fed Distributional Financial Accounts: equity ownership by wealth percentile (quarterly; identify the exact ids when implementing) | quarterly |

Auto-loan and credit-card delinquency detail by credit score: NY Fed Household Debt and
Credit Report, quarterly xlsx.

### 3.2 Euro area, EU, Spain

- **ECB Data Portal** (already used for HICP). Yield curve dataset `YC` (AAA euro area spot
  rates, for example `YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y`), key rates `FM` (deposit facility
  rate `FM.B.U2.EUR.4F.KR.DFR.LEV`), M3 growth `BSI`, bank lending survey, HICP `ICP`.
- **Eurostat API** (JSON-stat): `une_rt_m` unemployment, `prc_hicp_manr` HICP, `namq_10_gdp`
  GDP, `sts_trtu_m` retail trade, `sts_inpr_m` industrial production, `ei_bsco_m` consumer
  confidence (the DG ECFIN Business and Consumer Surveys).
- **OECD SDMX API**: composite leading indicators and consumer confidence for US, euro area,
  G20.
- **BIS data portal**: credit-to-GDP gaps and total credit to the private non-financial
  sector, quarterly, many countries. This is the "private and corporate debt" series.
- **Spain**: INE for CPI and EPA unemployment; HICP ES is already in place.

### 3.3 World context

IMF WEO and World Bank for GDP and debt (annual), OECD CLI for the G20. Context for the
report, not weekly signals.

### 3.4 Valuation

- **Shiller data** (`ie_data.xls`, Yale): monthly S&P price, earnings, dividends, CPI and long
  rate since 1871. Gives CAPE, real earnings, real returns and the excess CAPE yield, and is
  the backbone for long-history event studies.
- **Index levels** via yfinance: `^GSPC`, `^STOXX`, `^STOXX50E`, `^N225`, `ACWI` or `URTH`
  as world proxies, `RSP` and `SPY` for the breadth proxy, `^VIX` and `^VIX3M`.
- **Buffett indicator**: `NCBEILQ027S / GDP`, quarterly. It drifts upward structurally
  (foreign revenues, lower rates), so it is shown against its own trend, not as an absolute.
- **Forward earnings estimates** are paid data. Use trailing earnings and CAPE; if a
  published aggregate forward PE is ever entered by hand, label it as such.

### 3.5 Positioning and sentiment

- **CFTC Commitments of Traders**: weekly, large speculators' net positions in S&P futures,
  Treasuries, the dollar index. The closest free answer to "what are the big players betting".
- **AAII sentiment survey** (weekly; the historical file has at times required membership),
  **NAAIM exposure index** (weekly, free), **FINRA margin debt** (monthly), **ICI fund flows**
  (weekly), **CBOE put/call ratios** (daily; the download location moves, verify).
- **SEC EDGAR**: 13F-HR holdings (quarterly, up to 45 days after quarter end, long positions
  only, info tables as XML since 2013; Berkshire is CIK 1067983), Form 4 insider trades
  (two business days), 13D/G stakes, full-text search API.
- **Congress trading**: STOCK Act disclosures on the Senate eFD and House Clerk sites, up to
  45 days late, PDFs; third-party aggregators exist but scrape. Noisy and lagged. Last
  priority, treated as a curiosity in the report.

### 3.6 Fundamentals

- **SEC EDGAR XBRL**: `companyfacts` JSON per CIK, `submissions` JSON for filing dates, and
  the `frames` API for one tag across all filers for a period, which makes a universe-wide
  screen a handful of calls. Needs a User-Agent header with a contact address; 10 requests
  per second. Point-in-time by `filed_at`. US filers only, including foreign issuers on 20-F
  with IFRS tags.
- **yfinance fundamentals** for EU and other listings: limited history, no point-in-time.
  Used for EU coverage with a visible "not point-in-time" label. Revisit paid or ESEF XBRL
  sources later only if EU screening proves valuable.
- **Prices and FX** via yfinance, as now, through `market_data.py`.

### 3.7 Surveys, nowcasts and composites

The headline numbers are free in each monthly release; the histories are often licensed.
The catalog gets a `release` source type: the job records the headline with its release
date each month, so history accrues from the day the tool starts. FRED dropped the ISM
series in 2016 when ISM restricted redistribution, so ISM in particular has to be captured
this way or bought once for backfill.

| Indicator | Source | Cadence | Access |
|---|---|---|---|
| ISM Manufacturing PMI, with New Orders, Inventories, Prices Paid, Employment | ISM Report on Business | monthly, first business day | headline free in the release; history licensed |
| ISM Services PMI (since 1997), with Business Activity and New Orders | ISM | monthly, third business day | same |
| S&P Global US Manufacturing and Services PMI (flash mid-month, final) | S&P Global press releases | monthly | headline free |
| HCOB Euro Area Composite, Manufacturing and Services PMI; Germany and Spain | S&P Global / HCOB releases | monthly, flash and final | headline free |
| Caixin China PMI; JPMorgan Global Manufacturing PMI | releases | monthly | headline free |
| Regional Fed manufacturing surveys: Empire State, Philadelphia, Richmond, Kansas City, Dallas | each Fed bank's site | monthly, mid-month; earlier than ISM | free, full history |
| Conference Board Leading Economic Index: level, 6-month annualised change, diffusion | Conference Board release | monthly | headline free; series licensed |
| Excess bond premium (EBP) | Federal Reserve FEDS Notes, updated CSV | monthly | free, full history |
| NY Fed: recession probability from the term spread; r* (Holston-Laubach-Williams); Staff Nowcast; Survey of Consumer Expectations | NY Fed site | monthly, quarterly, weekly, monthly | free |
| Atlanta Fed: GDPNow; Wage Growth Tracker | Atlanta Fed site | several times a month; monthly | free |
| Cleveland Fed: Inflation Nowcasting (CPI and PCE before release); median CPI | Cleveland Fed site, FRED | daily; monthly | free |
| Senior Loan Officer Opinion Survey (full detail beyond DRTSCILM) | Federal Reserve | quarterly | free |
| Ifo Business Climate (Germany); ZEW expectations; Economic Sentiment Indicator (euro area, Eurostat `ei_bssi_m_r2`) | ifo, ZEW, Eurostat | monthly | free |
| Cass Freight Index (shipments and expenditures) | Cass Information Systems | monthly | free report |
| South Korea exports (a global trade bellwether, released on the 1st) | Korea Customs Service; OECD via FRED | monthly | free |
| Copper and gold futures (`HG=F`, `GC=F`), copper/gold ratio | yfinance | daily | free |
| MOVE index (Treasury volatility, `^MOVE` if available) | yfinance | daily | free if listed |
| Central bank gold purchases | World Gold Council | quarterly | free |

## 4. Macro module

### 4.1 Indicator contract

`indicator(series, spec) -> IndicatorReading` returns: latest value, observation date,
publication lag, z-score against a configurable window (default 10 years), percentile since
the series began, 3-month change, direction of concern, and a one-sentence reading. Pure,
and tested with hand-computed values.

### 4.2 Scorecard v1

| Group | Indicators |
|---|---|
| Rates and curve | 10y minus 3m, 10y minus 2y, real 10y (TIPS), Fed funds vs 2y (cuts or hikes priced), ECB deposit rate, euro AAA 10y and slope, NY Fed recession probability |
| Policy stance and currency | real Fed funds minus r* (restrictive or easy), real ECB rate, broad dollar YoY, DXY, copper/gold ratio, gold in EUR |
| Business surveys | ISM Manufacturing PMI, New Orders, New Orders minus Inventories, Prices Paid; ISM Services PMI and Business Activity; S&P Global US composite; average of the five regional Fed surveys (out before ISM); HCOB euro area composite, Germany, Spain; Ifo, ZEW, ESI; global and China PMIs |
| Leading composites and nowcasts | Conference Board LEI 6-month annualised change and diffusion; CFNAI 3-month average; OECD CLI for US, euro area and G20; GDPNow and NY Fed Staff Nowcast versus consensus; Cleveland Fed inflation nowcast |
| Inflation | CPI YoY and 3-month annualised, core, median and trimmed-mean cores, sticky CPI, breakevens, Michigan and NY Fed inflation expectations, Atlanta Fed wage growth, HICP euro area and Spain, oil YoY, real S&P earnings growth (trailing EPS deflated by CPI) |
| Labor | claims 4-week average, its YoY change and its rise from the 12-month low, continuing claims, unemployment, Sahm rule, payrolls 3-month average, temporary help YoY, manufacturing hours, openings per unemployed (V/U), quits rate, real wage growth |
| Housing | building permits YoY, starts, existing home sales, 30-year mortgage rate, NAHB index if captured |
| Business investment | core capital goods orders YoY, capacity utilisation, Cass freight shipments YoY, Korea exports YoY |
| Consumer | Michigan sentiment and its expectations component, euro area consumer confidence, real retail sales YoY, real disposable income YoY, saving rate, revolving credit growth, debt service ratio, card, auto and mortgage delinquencies |
| Credit and liquidity | HY OAS level and 3-month change, IG OAS, excess bond premium, loan officer survey net tightening, NFCI, net liquidity 13-week change, M2 YoY, margin debt YoY, euro area bank lending survey |
| Fiscal | deficit / GDP, federal interest payments / receipts, debt / GDP, euro area and Spain deficit and debt from Eurostat |
| Valuation | CAPE and its percentile, excess CAPE yield, trailing PE, Buffett indicator vs trend, corporate profits / GDP |
| Positioning and sentiment | AAII bull-minus-bear z-score, NAAIM, COT net speculative S&P, put/call, fund flows |
| Market internals | index vs 200-day average, Bollinger %B and bandwidth (20-day, 2 sigma), 20-day realised volatility, VIX and VIX/VIX3M, MOVE, RSP/SPY breadth proxy until Phase 3 supplies constituent prices, then share of the universe above its 200-day average and new highs minus new lows; drawdown from high |

### 4.3 Regime rules v1

A small set of transparent rules, each with a name, formula, threshold and, from Phase 6, a
measured hit rate and n:

- **Curve**: 10y minus 3m below zero for three months or more; and un-inversion after an
  inversion, which has historically sat closer to recessions than the inversion itself.
- **Labor**: Sahm rule triggered; claims 4-week average up more than 20% YoY.
- **Credit**: HY OAS z-score above 1, or up 150bp in three months.
- **Valuation**: CAPE percentile above 90. Says expected 10-year real returns are low; it is
  not a timing signal, and the report says so.
- **Sentiment extremes**: AAII spread z-score below minus 1.5 (contrarian bullish); NAAIM
  above 90 or below 20.
- **Trend**: index below a falling 200-day average.
- **Surveys**: ISM Manufacturing below 48 with New Orders below Inventories; ISM Services
  below 50. Services matters more than manufacturing in an economy that is mostly services,
  and the pair together is far more reliable than the manufacturing index alone.
- **Composites**: LEI 6-month annualised change below minus 4% with diffusion below 50 (the
  Conference Board's own signal); CFNAI 3-month average below minus 0.7 as confirmation that
  a recession has already begun.
- **Bank lending**: loan officer survey net tightening above 20%, which leads credit growth
  and activity by two to four quarters.
- **Corporate credit**: excess bond premium in its top decile, the cleanest measure of risk
  appetite in the bond market once expected defaults are stripped out.
- **Housing**: building permits down more than 15% YoY.
- **Claims off the low**: 4-week average of initial claims more than 15% above its 12-month
  low, historically with few false alarms.
- **Policy**: real Fed funds more than one point above r*, meaning policy is restrictive.

**Aggregation.** No single rule decides. "Stress" needs rules firing in at least three
different groups, "late cycle" two; a lone signal is listed but does not change the label.
Weights beyond that come only from Phase 6, once each rule has a measured record.

Output: a regime label (expansion, late cycle, stress, recovery) plus the list of rules
firing. The label summarises the rules; it does not predict anything, and is worded that way.

### 4.4 Streamlit page: Macro

Scorecard table coloured by z-score, each row expandable to a chart with recession shading
and the rule thresholds drawn. A "changed since last week" panel. Data freshness footer.

### 4.5 Track record, honestly

What the literature and the post-war data say about each indicator, so the report can
weight them by evidence rather than by how often they appear on television. Lead times are
against US recession starts. Phase 6 re-measures all of this on our own data.

| Indicator | Typical lead | Record | Known failures and limits |
|---|---|---|---|
| 10y minus 3m yield curve | 12 to 18 months | Best single recession predictor since the 1960s; the basis of the NY Fed model | 1966 false alarm; the 2022 to 2024 inversion had no recession within the usual window, so the record is under review |
| Initial claims, 4-week average rising 15% or more from its 12-month low | 0 to 3 months | Very few false alarms; the most timely hard labor data there is | Nearly coincident, so it confirms more than it warns |
| Sahm rule | coincident | No false positives from 1970 to 2023 | Triggered in mid-2024 with no recession following; its author flagged immigration-driven labor supply as the reason |
| Excess bond premium | 6 to 12 months | Strong out-of-sample predictor of activity and recessions in Fed research since 2012 | Monthly, and depends on the Fed keeping the series updated |
| HY credit spreads | coincident to 3 months | Reliable stress gauge; every major drawdown shows up | False alarms in 2011 and the 2015 to 2016 energy bust |
| Loan officer survey tightening | 2 to 4 quarters | Leads credit growth and business investment consistently | Tightened sharply in 2023 without a recession; quarterly only |
| Building permits | 12 months or more | Housing has led most post-war cycles | Weaker when housing is a small share of activity; distorted by 2020 to 2022 |
| ISM Manufacturing New Orders, and New Orders minus Inventories | 3 to 6 months | Good early read on the goods cycle; long history | Manufacturing is about a tenth of GDP; false alarms in 2015 to 2016 and 2022 to 2024 |
| ISM Services PMI | 0 to 3 months | Tracks the bulk of the economy; sub-50 has coincided with every recession since it began | History only from 1997; noisy month to month |
| Conference Board LEI (6-month change with diffusion) | 6 to 12 months | Called every post-war recession | Fell for more than twenty consecutive months in 2022 to 2024 without a recession, its first sustained false signal |
| CFNAI 3-month average below minus 0.7 | coincident | Clean confirmation that a recession has begun, with few false positives | Confirms; does not warn |
| Core capital goods orders | 3 to 6 months | Reliable read on business investment | Nominal; deflate it |
| Temporary help employment | 3 to 9 months | Leads payrolls at turning points | Structural decline in temp staffing muddies recent readings |
| Median and trimmed-mean inflation | current | Better signal of underlying inflation than headline or ex-food-and-energy core | Still monthly and revised |
| Cleveland Fed inflation nowcast | 2 to 4 weeks ahead of CPI | Small errors on the headline print | Only fills the gap until the release |
| Consumer sentiment | none as a predictor | Poor recession predictor on its own; useful for the gap between sentiment and spending | Politically coloured since 2016; spending has repeatedly ignored it |
| CAPE and the Buffett indicator | 7 to 10 years for returns | Strong link with subsequent decade returns | No timing power at all; say so in the report |
| VIX and VIX/VIX3M | coincident | Term-structure inversion marks stress reliably | Hours to weeks of horizon |
| Breadth (share above 200-day average) | weeks to months | Deteriorating breadth precedes most major tops | Whipsaws in sideways markets |
| Copper/gold ratio | coincident to 3 months | Tracks growth expectations and the 10-year yield | Supply shocks in either metal break the link |

The 2022 to 2024 episode is the lesson to keep in view: the yield curve, LEI, ISM
Manufacturing, the loan officer survey and finally the Sahm rule all signalled, and the
recession did not come. Any weighting scheme built only on earlier cycles would have been
wrong for two years. That is why the report shows the rules firing, the label they imply, and
the record behind each, rather than a single verdict.

## 5. Positioning module

- **13F tracker**: for each manager in `managers.toml`, parse the quarterly info table, diff
  quarter over quarter (new positions, exits, changes above 25%), concentration. Cross with
  our holdings and the screener output. The report states the 45-day lag and the long-only
  view every time.
- **Insider aggregates**: Form 4 open-market buys versus sells per company for screener
  candidates. Clusters of buying are a known modest positive; selling says little.
- **COT, AAII, NAAIM, margin debt, flows** feed the scorecard as indicators.
- **Congress trades**: optional, last.
- **Where an LLM helps**: summarising a manager's letter, a Fed statement or a quarter's 13F
  changes into a paragraph, with every number quoted from the parsed data so it can be
  checked. The holdings themselves are structured XML; no model is needed to read them.

## 6. Fundamentals and the screener

### 6.1 Ingestion

- Universe v1: S&P 500 and S&P 400, from EDGAR. Universe v2: STOXX 600 via yfinance,
  labelled not point-in-time.
- Map XBRL tags to canonical fields: revenue, gross profit, operating income, net income,
  operating cash flow, capex, depreciation, total assets, current assets and liabilities,
  cash, debt, shares outstanding, dividends, buybacks, interest expense, income tax.
  Companies use different tags, so each field has an ordered fallback list and records which
  tag was used. One test per field on fixture companies.
- Store facts with `filed_at`, so `facts_as_of(cik, date)` returns only what was public.

### 6.2 Metrics (pure, tested)

| Metric | Formula | Why |
|---|---|---|
| Earnings yield | EBIT / EV, with EV = market cap + debt minus cash | Greenblatt's cheapness measure, capital-structure neutral |
| ROIC | EBIT / (net working capital + net fixed assets) | Greenblatt's quality measure |
| FCF yield | (operating cash flow minus capex) / market cap | Cash, not accounting earnings |
| Margin trends | gross and operating margin, 3-year slope | Your point: widening margins precede profits |
| Growth | revenue and earnings 3-year CAGR; margin stability | Separates growth from cyclical noise |
| Leverage | net debt / EBITDA, interest coverage, Altman Z | Debt is what turns a cheap stock into a zero |
| Earnings quality | accruals ratio (net income minus CFO) / assets | Earnings not backed by cash |
| Capital return | share count change, shareholder yield (dividends + net buybacks) / market cap | Dilution versus return |
| Piotroski F-score | the nine binary checks | Proven filter against value traps |
| Multiples | P/E, P/B (with the intangibles caveat), EV/EBITDA, EV/sales, P/FCF | For context and sector comparison |
| Intrinsic value | EPV (normalised EBIT after tax / cost of capital) and a conservative DCF, both printing their inputs | Margin of safety = 1 minus price / value |
| Value-trap filters | revenue falling 3 years, negative FCF in 3 of 5 years, net debt / EBITDA above 4, F-score at or below 3, heavy dilution | Exclude before ranking |

### 6.3 Screens

- **Magic formula**: rank on earnings yield and ROIC, sum the ranks.
- **Quality-value composite**: z-scores of earnings yield, FCF yield, ROIC, F-score, accruals
  and gross-margin slope; sector-neutral option.
- **Deep value**: P/B below 1 with F-score at or above 7 (Piotroski's original setting).
- **Shareholder yield**.

Each screen returns ranked rows with every input visible. The row is the explanation.

### 6.4 Streamlit page: Screener

Choose screen, universe and filters; table of metrics; per-company detail with 10 years of
fundamentals and the intrinsic-value inputs; a watchlist stored in DuckDB showing price
against value and the margin of safety.

## 7. Rebalancing engine and the weekly report

### 7.1 Targets and drift

- Buckets in `targets.toml` (for example global equity, EU equity, US equity, bonds, cash,
  crypto, other); each holding maps to a bucket by category or an explicit override.
- Drift is actual weight minus target. Trade when the drift exceeds the band, absolute or
  relative, whichever hits first; trade back to target or to the band edge (configurable);
  skip trades below the minimum size.
- **New cash first.** Planned contributions go to the most underweight buckets before any sale
  is proposed. Cash-flow rebalancing avoids taxable sales.
- **Tactical tilt** from the macro regime: off by default, bounded when on (for example plus
  or minus 5 points of equity), and only enabled after Phase 6 shows a rule has any edge.

### 7.2 Tax awareness (Spain; verify against your own situation)

- Transfers between investment funds (traspasos) defer capital gains; sales of ETFs and
  stocks do not. When both legs are funds, the engine proposes a traspaso.
- Spanish capital-gains tax uses FIFO lots, while the app uses average cost for performance.
  `lots.py` reconstructs FIFO lots from the same transactions and estimates the taxable gain of
  each proposed sale. The report shows both figures.

### 7.3 Report contents

Markdown rendered to HTML, archived per run.

1. **Header**: run date, portfolio value and money-weighted return from the existing engine,
   and a freshness table: each source, latest observation, expected lag, status.
2. **Portfolio**: weights versus targets, drift, proposed trades with estimated cost and tax,
   and what the next contribution should buy.
3. **Macro**: the scorecard, the regime label with the rules firing, indicators that crossed
   a threshold since last run, sparklines.
4. **Positioning**: sentiment and positioning readings; 13F changes for tracked managers in
   the weeks after filing season; insider buying clusters among candidates.
5. **Opportunities**: top N from the chosen screens, entrants and drop-outs since last week,
   watchlist with margin of safety.
6. **Hypothesis register**: hypotheses due for review, with the current readings (section 9).
7. **Missing and failed**: anything that did not fetch, and what is therefore absent above.
8. **Appendix**: definitions and sources.

Delivery: a file in the archive plus email (SMTP) or a Telegram message with the HTML
attached. The Streamlit "Report" page lists past runs.

## 8. Automation and hosting

- **Stage A** (Phase 4): run `python -m invest.jobs.weekly` by hand, then from Windows Task
  Scheduler or cron on Saturday mornings. Claims land Thursday, most monthly releases early in
  the month, so Saturday sees a full week.
- **Stage B** (Phase 5): a GitHub Actions scheduled workflow, weekly, with secrets in the
  repository settings. The DuckDB file is cached between runs or rebuilt from sources; the
  report is emailed or committed to a private branch. Free and serverless; good enough for a
  weekly job.
- **Stage C** (Phase 5 or later): a self-hosted box (mini PC, Raspberry Pi or small VPS) with
  Docker Compose: a job container on cron, the Streamlit container, DuckDB on a volume,
  nightly backups. Reach it from any browser through Tailscale or Cloudflare Access plus
  `st.login`, so nothing listens on the open internet. Firestore rules stay deny-all for
  clients; the service account comes from secrets, not a file in the project folder.
- **Monitoring**: every run writes a run record; a failed source or a stale critical series
  sends a notification; a missed run is noticed because no report arrived.

## 9. Hypotheses to test

Your notes, turned into statements that data can confirm or reject. Kept in
`config/hypotheses.md` with a date and an outcome for each, so the reading of the evidence
does not drift with the mood of the week.

| Hypothesis | Measurement | Data | Caveat |
|---|---|---|---|
| Inflation is disguised as growth | Nominal versus CPI-deflated S&P earnings growth by decade; real PE (CAPE) | Shiller, CPIAUCSL | Roughly 40% of S&P 500 revenue is foreign, so a weaker dollar lifts reported earnings by translation. Separate that effect with a DXY-versus-EPS regression before attributing it to inflation |
| Debt gets cheaper to repay as inflation rises, and leveraged holders win | Real yields versus asset returns; margin debt and corporate debt growth versus real rates | DFII10, FINRA, Z.1 | True for fixed-rate debt, not floating. The relevant variable is the real rate, not inflation alone |
| Concentrated ownership means the big players set the direction | Ownership shares over time; whether positioning extremes predict forward returns | Fed DFA, COT, NAAIM, AAII | Positioning measures are modestly contrarian in the literature; expect small effects |
| Consumers spend on debt, and markets fall when they stop | Real retail sales, revolving credit, delinquencies and saving rate versus forward index returns and recessions | RRSFS, REVOLSL, DRCCLACBS, PSAVERT, NY Fed HHDC | Consumption is about 68% of US GDP; the turn is usually visible in claims first |
| The labor market lags and then breaks | Lead time of claims over the unemployment rate; claims rules versus the Sahm rule as recession calls | ICSA, UNRATE, SAHMREALTIME, USREC | About eight recessions since 1970. n is tiny; print it |
| Liquidity drives prices | Net liquidity 13-week change versus forward index returns | WALCL, WTREGEN, RRPONTSYD | The 2020 to 2022 episode dominates the sample |
| Crash fatigue, then a confidence break | Event study on HY OAS spikes, VIX spikes and curve un-inversions: forward return distribution at 1, 3, 6, 12 months | BAMLH0A0HYM2, VIXCLS, T10Y3M | Overlapping windows; report n and intervals |
| The longer the crash is delayed, the bigger it is | Regress drawdown depth on time since the previous 20% drawdown, monthly since 1871 | Shiller | Cheap and decisive either way |
| The end of the Ukraine war lifts markets but not defence | Pre-register the expected effects (EU indices, defence names, energy) and the window, then measure | yfinance | A single event; a case study, not a statistic |
| Manufacturing surveys no longer call the cycle in a services economy | Lead and hit rate of ISM Manufacturing versus ISM Services and the regional Fed surveys against USREC, by decade | ISM captures, regional Fed surveys, USREC | ISM Services history starts in 1997; manufacturing's share of GDP has roughly halved since 1970 |
| Buying dips beats holding | Rules (buy after minus 10%, minus 20%, below the 200-day average, %B below 0) versus buy-and-hold with monthly contributions, walk-forward, since 1928 and since 1990 | Shiller, yfinance | Do this before considering any model |

**Method rules**: walk-forward only; publication lags applied; n and confidence intervals
printed; overlapping windows corrected; every result stored with the code version that made
it.

**On the neural network.** Monthly macro data gives a few hundred observations and a handful
of recessions. A network trained to "buy dips" on one market's history will mostly memorise
that history. Start with the rules above, then logistic regression or gradient boosting on a
small feature set with strict walk-forward, and keep whatever beats the rules out of sample.
None of this needs a cluster; a laptop handles the data size. Cloud and GPUs become useful
only for text: letters, filings and news processed by a language model, with quoted sources.

## 10. Roadmap

Rough sizes are weekends of focused work. Each phase ends with a shippable result.

### Phase 0: foundations (1 to 2)

- [ ] Create `invest/` with `data/cache.py`: DuckDB open, upsert and read with provenance
      columns; tests on a temporary database.
- [ ] `data/fred.py`: fetch a series by id and store it; fixture test on a saved response;
      API key from secrets.
- [ ] `data/ecb.py`: generalise the existing ICP fetcher to any ECB key; move `_fetch_icp`
      onto it without changing the dashboard.
- [ ] `config/series.toml` with about 20 starter series and their metadata; a loader that
      validates it.
- [ ] `jobs/refresh.py`: fetch the whole catalog, print the freshness table, exit non-zero on
      any failure.
- [ ] Decide where report archives live and gitignore `data/`.

Done when a second `refresh` run adds no duplicate rows and the freshness table is right.

### Phase 1: macro dashboard (2 to 4)

- [ ] `macro/indicators.py`: the reading contract; z-score, percentile and change helpers
      with hand-computed tests.
- [ ] Yield curves for the US and euro area; real yields and breakevens; oil.
- [ ] Inflation block, including real earnings growth from Shiller data via `data/files.py`
      (downloads with checksum and fetch time).
- [ ] Labor block, including the Sahm rule and the claims 4-week average.
- [ ] Consumer and credit blocks; net liquidity; M2; loan officer survey tightening.
- [ ] `release` source type in the catalog: a headline recorded with its release date. Start
      capturing ISM Manufacturing (PMI, New Orders, Inventories, Prices Paid) and ISM
      Services on release day, plus S&P Global, HCOB and LEI headlines. History accrues from
      the first month; backfill later if a backtest needs it.
- [ ] Regional Fed surveys (full free history) and their average as the early read on ISM.
- [ ] Housing block: permits, starts, existing home sales, mortgage rate.
- [ ] Business investment block: core capital goods orders (deflated), capacity utilisation,
      temporary help, manufacturing hours, Cass freight, Korea exports.
- [ ] Composites and nowcasts: excess bond premium CSV, CFNAI, OECD CLI, NY Fed recession
      probability and r*, GDPNow, NY Fed Staff Nowcast, Cleveland inflation nowcast, Atlanta
      Fed wage tracker; better cores (median, trimmed-mean, sticky) and inflation
      expectations in the inflation block.
- [ ] Euro surveys: Ifo, ZEW, ESI; policy stance (real rate minus r*), dollar and copper/gold.
- [ ] Valuation block: CAPE, excess CAPE yield, Buffett indicator against trend, profits / GDP.
- [ ] Market internals from yfinance: 200-day average, Bollinger %B, realised volatility, VIX
      term structure, RSP/SPY.
- [ ] `macro/regimes.py` with the v1 rules; one test per rule on synthetic series.
- [ ] `pages/macro.py`: scorecard, expandable charts with USREC shading, freshness footer;
      AppTest smoke test.

Done when the page renders entirely from DuckDB and every figure shows its date.

### Phase 2: positioning and filings (2)

- [ ] COT, AAII, NAAIM, FINRA margin debt and ICI flows as scorecard indicators.
- [ ] `positioning/filings13f.py`: info-table parser, quarter-over-quarter diff, fixture from
      a real Berkshire filing; managers in config.
- [ ] Form 4 aggregate buys and sells per CIK over a window.
- [ ] Optional: congress disclosures through a third-party dataset, labelled noisy.

Done when the "what the big holders did" section prints from parsed data for two managers.

### Phase 3: fundamentals and screener (3 to 5)

- [ ] `data/edgar.py`: companyfacts, submissions, frames; rate limiting; User-Agent; fixtures.
- [ ] `fundamentals/facts.py`: tag mapping with fallbacks and `facts_as_of`; tests on three
      fixture companies (a bank, an industrial, an IFRS filer).
- [ ] `fundamentals/metrics.py`: every metric in 6.2, pure, tested against hand-computed
      numbers.
- [ ] `fundamentals/screens.py`: magic formula, quality-value composite, deep value;
      sector-neutral option.
- [ ] Universe loader from a maintained constituents CSV; note the survivorship bias.
- [ ] `fundamentals/valuation.py`: EPV and conservative DCF with printed inputs; margin of
      safety.
- [ ] `pages/screener.py` with detail view and a DuckDB watchlist; AppTest.
- [ ] EU coverage through yfinance with the not-point-in-time label.
- [ ] Breadth from constituent prices: share of the universe above its 200-day average, new
      highs minus new lows; replaces the RSP/SPY proxy in the macro scorecard.

Done when a screen run for a past date uses only facts filed before that date, and a test
asserts it.

### Phase 4: rebalancing and the report (2 to 3)

- [ ] `config/targets.toml`; `portfolio/rebalance.py`: drift, bands, trade list,
      contribution allocation; tests including band edge versus target.
- [ ] `portfolio/lots.py`: FIFO lots from the transactions; taxable gain per proposed sale;
      tests showing the same total P&L as the average-cost engine with a different split.
- [ ] `report/build.py` assembling sections from DuckDB; `report/render.py`; golden test on
      a fixture run.
- [ ] `jobs/weekly.py`: refresh, build, render, archive, deliver; the "missing and failed"
      section.
- [ ] `pages/report.py` listing archived reports.
- [ ] Task Scheduler or cron entry for Saturday morning.

Done when a report runs end to end on the real portfolio with every source live, and a
second run with one source blocked shows it under "missing" instead of crashing.

### Phase 5: automation and hosting (1 to 2, plus operations)

- [ ] GitHub Actions weekly workflow, or Docker Compose on a box: secrets, DuckDB
      persistence, delivery.
- [ ] Failure notifications; run history.
- [ ] Streamlit deployment behind Tailscale or Cloudflare Access with `st.login`;
      credentials from secrets (`credentials.Certificate` accepts a dict).
- [ ] Backups of DuckDB and the report archive.

Done when reports arrive for four consecutive weeks without being touched.

### Phase 6: research harness (ongoing)

- [ ] `research/` with event-study and walk-forward utilities; publication-lag handling;
      ALFRED vintages for CPI, unemployment and claims.
- [ ] `config/hypotheses.md` with one entry per row of section 9, each with a script and a
      dated outcome.
- [ ] Hit rates for the Phase 1 regime rules, with n; retire rules that do nothing.
- [ ] Only then: tactical tilt limits in `targets.toml`, if anything earned them.

### Phase 7: optional experiments

- [ ] Simple models against the rules, walk-forward.
- [ ] Language-model summaries of letters, filings and news, every number quoted from data.
- [ ] Anything with a GPU, if it is still fun.

## 11. Decisions to make before Phase 0

- **Universe**: US-only screener first, or EU from day one with weaker data?
- **Target allocation**: which buckets, which weights, how wide the bands?
- **Delivery**: email, Telegram, or the Streamlit page only?
- **Hosting**: GitHub Actions first, or straight to a box?
- **Report language**: English, like the code, or Spanish?
- **Repository**: keep everything here (recommended, the engine and tests are already in
  place) or split the analysis package out.

## 12. Definitions

- **Buffett indicator**: total US corporate equity market value / nominal GDP. Read against
  its own trend.
- **CAPE**: real index price / 10-year average of real earnings. **Excess CAPE yield**:
  1 / CAPE minus the real 10-year yield; a rough equity risk premium.
- **Real yield**: 10-year TIPS yield. **Breakeven inflation**: nominal 10-year minus TIPS.
- **Net liquidity**: Fed balance sheet minus Treasury General Account minus reverse repo.
- **Sahm rule**: the 3-month average unemployment rate rises 0.5 points or more above its low
  of the previous 12 months.
- **PMI**: a diffusion index, the share of respondents reporting improvement plus half of
  those reporting no change; 50 means no change, below about 48 in manufacturing and 50 in
  services has aligned with contraction. **New Orders minus Inventories**: the forward-looking
  spread inside the manufacturing survey; negative means production will be cut.
- **LEI signal**: the 6-month annualised change in the Leading Economic Index below minus 4%
  while its diffusion (share of the ten components rising) is below 50.
- **Excess bond premium**: the corporate bond spread left after removing the part explained
  by expected defaults; a pure measure of investors' risk appetite.
- **Policy stance**: the real Fed funds rate (nominal minus expected inflation) minus the
  neutral rate r*; positive is restrictive.
- **CFNAI**: weighted average of 85 activity indicators, scaled so zero is trend growth; the
  3-month average below minus 0.7 has marked the start of recessions.
- **V/U ratio**: job openings per unemployed person; above 1 the labor market is tight.
- **Copper/gold ratio**: copper price over gold price; rises with growth expectations and
  tracks the 10-year yield.
- **Real earnings growth**: (1 + nominal EPS growth) / (1 + inflation) minus 1, the Fisher
  form already used by `real_rate` in the app.
- **Z-score**: (latest minus mean) / standard deviation over the window. **Percentile**: rank
  of the latest value within the full history.
- **Bollinger %B**: (price minus lower band) / (upper minus lower), bands at the 20-day mean
  plus or minus two standard deviations. **Bandwidth**: (upper minus lower) / middle.
- **EV**: market cap plus debt minus cash. **Earnings yield**: EBIT / EV. **ROIC**: EBIT /
  (net working capital plus net fixed assets).
- **FCF**: operating cash flow minus capital expenditure. **Accruals ratio**: (net income
  minus operating cash flow) / average total assets.
- **EPV**: normalised EBIT times (1 minus tax rate), divided by the cost of capital.
  **Margin of safety**: 1 minus price / intrinsic value.
- **Drift**: actual weight minus target weight. **Band**: the drift beyond which a trade is
  proposed, absolute or relative to the target.
- **Money-weighted return**: as in the app, the IRR of dated cash flows closed with today's
  value.
