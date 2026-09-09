# My Investment App

A Streamlit investment portfolio dashboard. Track holdings across asset classes, log buys
and sells, and see valuation and performance in EUR.

## Features

- **Portfolio Dashboard**
  - Cost basis, market value, and unrealised / realised / total P&L, all in EUR.
  - Open positions with per-asset weight, plus a separate table of closed positions.
  - Asset distribution by category and by holding.
  - Per-asset price history with buy/sell markers and an average-cost line.
  - Beta, Alpha and R² against the S&P 500, estimated in EUR over two years.
- **Transaction Logging**
  - Buys and sells across Stocks, ETFs, Funds, Crypto and more.
  - Ticker or ISIN, with ISINs resolved to a Yahoo symbol once at entry.
  - Historical FX captured on the trade date; fees included in the cost basis.
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
     `firebaseServiceAccountKey.json`, or point `FIREBASE_CREDENTIALS` at it.

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

## Project Structure

- `app.py`: Entry point and navigation.
- `portfolio_math.py`: Pure calculation engine — positions, cost basis, valuation, CAPM.
  No I/O, so every figure it produces is unit-testable.
- `market_data.py`: All network access and caching (prices, FX, metadata, ISIN lookup).
- `database.py`: Firestore initialisation and helpers.
- `pages/portfolio.py`: Dashboard — loads, computes, renders.
- `pages/transactions.py`: Trade entry and history log.
- `tests/`: pytest suite over `portfolio_math.py`, including a golden fixture.
- `requirements.txt` / `requirements-dev.txt`: Runtime and test dependencies.
- `firebaseServiceAccountKey.json`: (Not in repo) Your private Firebase credentials.
