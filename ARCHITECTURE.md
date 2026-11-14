# Architecture

![Architecture map: the Streamlit pages and the batch jobs, the libraries they call, where DuckDB and Firestore keep the data, and the internet sources it comes from](docs/architecture.svg)

Two programs share this repository: the **Streamlit app** (`streamlit run app.py`), whose
pages you use in the browser, and the **batch jobs** (`python -m invest.jobs.<name>`), which
run on a schedule or from a page's button. On the map, arrows run from the caller to what it
calls, and the colour says which of three paths the data takes. The file-by-file list is in
the README's [Project Structure](README.md#project-structure), and every DuckDB table is
described in [DATABASE.md](DATABASE.md).

## The three data paths

- **Batch (amber).** A job fetches everything `config/` lists through the clients in
  `invest/data/`, computes with the analysis logic and appends to `data/market.duckdb`, each
  row with its fetch time and vintage. Macro, Screener and Weekly report show what is stored
  and fetch nothing themselves: their buttons start a job (`job_panel.py`).
- **Live (blue).** `market_data.py` fetches what a page needs when it needs it: prices,
  exchange rates, search results, a company's accounts. Streamlit's cache keeps each answer
  for 15 minutes to 30 days; nothing is stored.
- **Your records (green).** Transactions, cash accounts and the portfolio design are kept in
  Firestore, read and written through `database.py`. The weekly job reads them too, to value
  the portfolio for the report.

The Stock explorer, Company financials and Watchlist pages use the first two paths together:
what the jobs stored, and anything else fetched live.

## Rules the code keeps

- `portfolio_math.py` does no I/O: `tests/test_purity.py` fails if it imports Streamlit,
  Firestore or a network library.
- Apart from Firestore, every network call of the app is in `market_data.py`, and every
  network call of `invest/` is in `invest/data/`.
- Pages open DuckDB read-only, with two exceptions that write through the Store: the
  watchlist (`watchlist.py`) and a release recorded on the Macro page.
- DuckDB allows one writer, so while a job runs, the pages that read the file wait for it
  (`job_panel.follow_if_running`).
- Stored series are append-only with a vintage date, so a read can ask what was known on a
  given day.

## Keeping the map current

`docs/architecture.svg` is drawn by hand. It is plain text, so it diffs in git, and GitHub,
PyCharm and any browser display it. Update it in the same change that adds or moves a page,
a job, a data source or a store, and change its "as of" date.
