# Repository assessment — 4 October 2026

The application has a strong foundation for a personal portfolio and research tool: pure calculation modules, explicit FX conventions, source provenance, fixture-based tests, and useful separation of network access from most analytics. The highest-value next work is correctness across module boundaries. A rewrite or additional analytics would be premature.

This is a repository-wide architecture and code review, with deeper inspection of accounting, ingestion, storage, reporting, and operations. Findings describe code behavior; they do not establish that a particular live holding or deployed system has been affected. No production database, external delivery channel, or live refresh job was used. Tax rules and investment assumptions were not independently validated.

## How it works

| Area | Main components | Data flow and responsibilities |
|---|---|---|
| Navigation | `app.py`, seven pages | Streamlit reruns the entry point and selected page; shared chart controls retain settings in URLs. |
| Portfolio records | `database.py`, transaction/design pages | Firestore holds trades, manually maintained cash accounts, and saved target allocation. Reads have short Streamlit caches. |
| Accounting | `portfolio_math.py` | Documents become normalized transactions, replayed by asset and date. Average cost handles buys, sells, fees and historical FX. Current listing prices and FX provide EUR valuation. XIRR and CAPM supply performance analytics. |
| Dashboard | `market_data.py`, portfolio/design pages | Yahoo provides prices, metadata and FX; ECB provides inflation. Dry Powder belongs to portfolio allocation; the cash reserve stays outside it. |
| Analysis storage | `invest/data/cache.py` | DuckDB stores observations and vintages, prices, company facts, filings, watchlists, snapshots and run history. |
| Macro | TOML catalog, source adapters, refresh job, macro modules | Fetch source series, ingest manual releases, calculate derived series, evaluate freshness, then build scorecard and regime snapshots. |
| Fundamentals | EDGAR parser, canonical facts, metrics, valuation, screens | Resolve companies, ingest filings and prices, choose annual/TTM inputs, calculate metrics and model values, and persist ranked screener inputs. |
| Positioning | COT, 13F and Form 4 modules | Parse positioning and filings, compare quarters, calculate concentration and summarize insider activity. |
| Weekly report | snapshot, rebalance, FIFO lots, report modules | Independently load trades, fetch quotes, apply TOML allocation targets, propose bucket trades, assemble analysis, archive Markdown/HTML and optionally deliver. |
| Research | events, walkforward, hypotheses | Evaluate event returns and macro hypotheses over stored histories. |
| Operations | launcher, job panel, scripts, Docker, Actions | UI subprocesses and scheduled jobs update a shared database; backups copy DuckDB and archive reports. |

## Findings, ordered by urgency

P1 means address before relying on the affected output or deployment path. P2 means a material correctness or reliability improvement. “Reproduced” means an offline diagnostic exercised the actual implementation.

### 1. P1 — Docker builds include local secrets

[Dockerfile:11](C:/code/my-investment-app/Dockerfile:11) uses `COPY . .`, and there is no `.dockerignore`. A Firebase service-account JSON file exists at the build-context root. Gitignore does not exclude it from Docker's context. A local build would also include any `.env`, Streamlit secrets, local data and virtual environment present there.

**Fix:** use an explicit application-file allowlist and a `.dockerignore`; inject credentials only at runtime. Verify image contents without printing secrets. If an image built from this directory was distributed, investigate its contents and rotate affected credentials. This review did not build or inspect any existing image.

### 2. P1 — Dashboard and weekly report use different portfolios and targets

The dashboard includes Firestore Dry Powder balances and the saved design. [weekly.py:204](C:/code/my-investment-app/invest/jobs/weekly.py:204) loads `config/targets.toml`; [snapshot.py:69](C:/code/my-investment-app/invest/portfolio/snapshot.py:69) accepts transaction documents only. It never loads the bank balances or saved design.

For example, investments of EUR 80,000 plus EUR 20,000 Dry Powder are a EUR 100,000 portfolio on the dashboard but EUR 80,000 in the report. Changing the sliders does not change report trade proposals. TOML also has a different bucket model, including a crypto target.

**Fix:** introduce a shared portfolio input/snapshot service and an explicit allocation-policy source. Adapt both UIs and jobs to it. Preserve the distinction between invested assets, Dry Powder and reserve cash. Prove dashboard/report parity on identical inputs.

### 3. P1 — Missing prices produce misleading returns and trade proposals

[snapshot.py:109](C:/code/my-investment-app/invest/portfolio/snapshot.py:109) records unvalued holdings but calculates XIRR from all transaction cash flows and only the valued terminal assets. [weekly.py:211](C:/code/my-investment-app/invest/jobs/weekly.py:211) still rebalances this partial portfolio. The dashboard has the same XIRR issue; it adds an understatement note in the help text but continues displaying the result.

**Reproduced:** buy two assets for EUR 100 each, leave the first unchanged and omit the second quote. After one year the application marks a **−50% XIRR as credible**. This measures a missing quote as a loss.

**Fix:** carry explicit completeness status through the shared snapshot. Show partial valuation subtotals, but suppress whole-portfolio returns and actionable rebalancing until required holdings are valued. Include quote observation dates and a freshness policy.

### 4. P1 — XBRL ingestion discards distinct share counts

[edgar.py:131](C:/code/my-investment-app/invest/data/edgar.py:131) builds fact identity from company, tag, unit, dates and accession. Distinct values with those same fields collide and are removed at [edgar.py:186](C:/code/my-investment-app/invest/data/edgar.py:186). The later share-count aggregation cannot recover them.

**Reproduced:** two same-filing share rows containing 1,000 and 250 become one row; the downstream count is 1,000. The existing share-class unit test constructs distinct rows directly, bypassing the parser where they are lost. This can affect market cap, valuation per share and screen rankings.

**Fix:** preserve source-row identity and ambiguity through ingestion; verify class aggregation using real filing semantics rather than assuming every distinct value is additive. Add parser-to-store-to-metrics tests and re-ingest affected facts after a schema migration.

### 5. P1 — “Fetch Price” can write a listing price as a different transaction currency

[transactions.py:112](C:/code/my-investment-app/pages/transactions.py:112) downloads a listing close and inserts it into the nominal transaction price. The selected transaction currency is independent, and there is no conversion between them. The download also does not explicitly select unadjusted prices.

For a USD listing with the form left in EUR, a fetched numeric price of 100 becomes EUR 100 when saved. Warning that the currencies differ does not resolve the incorrect cost basis. The fetched price also survives changes to the identifier/date until explicitly replaced or cleared.

**Fix:** treat the fetched quote as dated, currency-tagged reference data; convert explicitly when appropriate or require the actual execution price in the payment currency. Set adjustment behavior explicitly. Bind cached form values to symbol/date/currency and invalidate them when those inputs change.

### 6. P1 — Stored insider data cannot be summarized

[cache.py:632](C:/code/my-investment-app/invest/data/cache.py:632) drops `derivative` and `security` from parsed Form 4 rows. [insiders.py:139](C:/code/my-investment-app/invest/positioning/insiders.py:139) requires `derivative` to exclude derivatives.

**Reproduced:** parse a supplied Form 4 fixture, store it, read it back and aggregate it: `KeyError: 'derivative'`. Weekly positioning fails when it encounters stored insider rows for a watchlist company.

**Fix:** migrate the table to retain those fields, re-ingest existing rows where the classification cannot be recovered, and test the complete storage round trip. Do not default unknown historical rows to non-derivative.

### 7. P2 — JSON exports lose same-day transaction order

[portfolio_math.py:550](C:/code/my-investment-app/portfolio_math.py:550) derives ordering only when `timestamp` is a Python datetime; a JSON timestamp string receives sequence zero. It also truncates native timestamps to whole seconds.

**Reproduced:** same-day buy then sell replays to zero units with native timestamps. Reverse the document list and encode timestamps as ISO strings, as a JSON export can, and replay leaves one unit open.

**Fix:** persist an explicit execution/order field, parse supported timestamp formats, preserve precision and supply a deterministic tie-breaker. Compare Firestore-shaped and JSON-shaped replay outputs.

### 8. P2 — Invalid trades are allowed into accounting

The forms validate positive quantity and price but do not replay the affected position before saving or deleting. The engine accepts oversells and books the excess proceeds against zero basis. Unknown document actions are treated as sells. Validation in the document builder does not fully reject non-finite amounts or invalid currency/fees.

**Reproduced:** buy one unit for EUR 100, then sell two at EUR 150; the engine reports EUR 200 realized profit and zero units, with a warning. This is an explicit recovery behavior, but it should not make a newly entered invalid ledger look valid.

**Fix:** validate the prospective ledger on create, edit and delete. Keep imported invalid records visible for repair, while marking affected performance unavailable. Add idempotent trade creation to prevent duplicate submissions.

### 9. P2 — Historical reports and research do not consistently honor information available at the time

[weekly.py:219](C:/code/my-investment-app/invest/jobs/weekly.py:219) builds readings and regimes from current stored data even with `--today`; portfolio valuation still uses recent quotes and all transactions. Filings and screener selection also use latest data. An older report date therefore does not mean an older information set.

[hypotheses.py:90](C:/code/my-investment-app/invest/research/hypotheses.py:90) reads latest-vintage macro histories and shifts observation dates by fixed publication lags. It does not use the stored historical vintages for each evaluation date. In addition, monthly uninversion events are timestamped at the start of the month whose full average determines the event, and recent recession signals can be counted as false alarms before their lead window has elapsed.

**Fix:** distinguish report labeling date from an actual historical cutoff. Implement a point-in-time query contract across transactions, quotes, filings, snapshots and derived data. Use release/vintage dates for research and exclude or label incomplete outcome windows. Until then, describe the research as retrospective analysis of revised histories.

### 10. P2 — “Conservative” valuation can omit the pessimistic model

[valuation.py:160](C:/code/my-investment-app/invest/fundamentals/valuation.py:160) discards non-positive model values before choosing the minimum. EPV and DCF also assume zero net debt when it is missing, although a note is attached.

**Reproduced:** EPV of −87.78 per share and DCF of 133.60 yield a “conservative” value of 133.60. This is not the lower of the two estimates described in the UI/documentation.

**Fix:** explicitly distinguish unavailable, non-positive and valid estimates; avoid selecting only favorable models. Require debt inputs or make assumed values a separate, clearly labeled scenario. Review consistency of cash-flow basis and discount-rate assumptions before treating DCF values as comparable across companies.

### 11. P2 — Delivery failures can leave the weekly job successful

[deliver.py:111](C:/code/my-investment-app/invest/report/deliver.py:111) catches channel errors and returns status strings. Weekly success and process exit code depend on `failures`, which does not include these delivery failures.

**Fix:** return structured channel outcomes and distinguish archive success from delivery success. A configured channel failure should be observable in run status and exit policy. Test a successful archive plus failed SMTP/Telegram delivery without sending real messages.

### 12. P2 — Job coordination and backups do not cover all writers

[job_panel.py:86](C:/code/my-investment-app/job_panel.py:86) coordinates jobs only inside one Streamlit server process. The scheduler, command line, second container and direct watchlist/release writes do not share that lock. DuckDB rejects conflicting access, producing unavailable pages or failed jobs rather than queued work.

[backup.py:38](C:/code/my-investment-app/invest/jobs/backup.py:38) checkpoints, closes the database connection, then copies the file. Another writer can acquire the file between close and copy. Backups also omit Firestore portfolio records and the manual release source file.

**Fix:** adopt one cross-process writer queue/lock, including backup and UI mutations. Hold exclusion throughout snapshot/copy, publish backup files atomically, and test restore. Define export/backup coverage for trades, cash, design, releases and watchlists. Prefer a single writer service before replacing DuckDB.

## Additional improvements

- **Cash-only dashboard:** the portfolio page stops before loading bank accounts when no transactions exist; the design page can already handle Dry Powder alone. Move the empty-state decision after loading all portfolio inputs.
- **Concurrent cash edits:** replacing the entire Firestore account collection from a cached editor can overwrite another session's update. Use stable account IDs and revision/precondition checks, with an explicit conflict message.
- **Real return labeling:** lifetime nominal XIRR adjusted using the latest one-year inflation rate is not lifetime purchasing-power return. Deflate dated cash flows consistently, or label the result as a current-inflation approximation.
- **Date alignment:** monthly transforms shift observation rows after dropping missing values, so a missing month changes what “12 months ago” means. FX conversion uses backward fill at the beginning of a price panel. Test irregular calendars and use only eligible prior observations.
- **Valuation scope:** the trade model supports buys and sells, but not dividends, splits, transfers or other corporate actions. Make that coverage visible and plan typed events before claiming comprehensive total-return accounting.
- **Instrument identity:** ISIN-first grouping, ticker-only entry and independent listing metadata need a canonical instrument model to prevent one security splitting into multiple positions or metadata pointing to a different listing.
- **Screener currency display:** model values are in the accounts currency while the stored US listing price is in USD. Show explicit currency labels or convert display values into the same currency, including watchlists.
- **Persistent configuration:** manual releases are written into the application source directory; Docker only mounts the data directory. Make editable state durable and shared, with one authoritative source.
- **Maintainability:** extract page orchestration into application services, add explicit schema migrations and typed result/status objects, and update documentation that still describes all analysis pages as read-only.
- **Testing:** retain the pure tests, fixture parsers and page smoke tests. Add cross-layer tests where current coverage is weakest: parser/store/calculation, dashboard/report parity, incomplete data, export replay and concurrent operations. Record dependency versions and add an offline network-denial guard so test runs cannot accidentally reach live services.

## Proposed implementation sequence

| Phase | Work | Acceptance criteria |
|---|---|---|
| 1 — Contain incorrect outputs and packaging risk | Docker exclusions; incomplete-portfolio gate; fetched-price currency handling; insider schema repair | No local secrets/data in image; missing quote suppresses XIRR/trades; cross-currency quote cannot silently become a payment price; Form 4 round trip aggregates correctly. |
| 2 — Unify portfolio state | Shared snapshot service; cash/design policy integration; stable transaction ordering; mutation validation | Same inputs produce identical dashboard/report totals and allocations; JSON replay matches native replay; invalid edits and duplicate submissions cannot silently alter holdings. |
| 3 — Repair analytical inputs | XBRL fact identity migration and re-ingestion; conservative valuation policy; dated quote metadata; calendar alignment | Distinct source facts survive round trips; ambiguous facts are flagged; unfavorable/missing model inputs do not generate a favorable fallback; irregular-date fixtures behave correctly. |
| 4 — Make historical analysis honest | End-to-end cutoff semantics, vintage-aware derived inputs, release timing, censored outcomes | Adding a later revision cannot change a previously evaluated information set; historical reports exclude later trades/filings/prices; incomplete research outcomes are identified. |
| 5 — Operational reliability | Shared writer coordination; structured delivery status; durable editable state; atomic backups and restore drill | Concurrent app/scheduler work queues predictably; failed configured delivery is visible; restored data reproduces portfolio state and reports. |
| 6 — Extend the product model | Corporate actions/income, richer instrument identities, performance and UI refinements | New behavior is defined with examples and invariants before UI implementation; existing fixtures remain stable. |

Implement these as focused changes with a reproducer and regression check for each defect. Schema work should include backup, migration and re-ingestion instructions. Preserve the pure calculation modules and the existing source/catalog separation.

## Verification

Six temporary offline diagnostics confirmed the share-count collision, incomplete-valuation XIRR, JSON ordering loss, conservative-model filtering, oversell behavior and insider storage failure. These diagnostics assert the observed defects; their passing status does not mean those defects are fixed. They were used for review only.

The full existing suite passed: **727 passed in 214.21 seconds**, using the repository virtual environment outside the sandbox. The first sandboxed attempt could not load Python's standard library, and the system interpreter did not have pytest installed; the project environment worked once access was allowed. The six additional diagnostics passed in 0.71 seconds, confirming the observed defect conditions.

Application code and production data are unchanged. This assessment is the only retained repository addition. No live-service integration, browser visual review, Docker build or deployed-image inspection was performed.
