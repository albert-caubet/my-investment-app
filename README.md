# My Investment App

A modern, Streamlit-based investment portfolio dashboard. This application allows users to track their investments across different asset classes, log transactions (buys and sells), and visualize their portfolio performance in real-time.

## Features

- **Portfolio Dashboard**:
  - View total net worth and daily performance metrics.
  - Track asset distribution via interactive pie charts.
  - Analyze asset performance history with cost basis comparison (Average Buy Price).
- **Transaction Logging**:
  - Record trades for various asset classes (Stocks, ETFs, Funds, Crypto, etc.).
  - Support for Tickers and ISIN identifiers.
  - Automatic timestamping and secure storage in Firebase Firestore.
- **Real-Time Market Data**:
  - Integration with `yfinance` to fetch live prices and historical performance.
- **Cloud Database**:
  - Powered by Google Firebase for reliable, multi-device data synchronization.
  - [Homepage](https://console.firebase.google.com/u/1/)
  - [Project](https://console.firebase.google.com/u/1/project/my-portfolio-7d821/overview)
  - [Database](https://console.firebase.google.com/u/1/project/my-portfolio-7d821/firestore/databases/-default-/data/~2Ftransactions~2FFNjXuqNnzeW1BQCx6D2f)

## Prerequisites

Before you begin, ensure you have the following:

- **Python 3.13** installed.
- A **Google Firebase** project with Firestore enabled.
- A Firebase Service Account Key (JSON file).

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
   - Place your Firebase Service Account JSON file in the project root.
   - Rename it to `firebaseServiceAccountKey.json`.

## How to Run

Launch the application using Streamlit:

```bash
streamlit run app.py
```

The app will open in your default web browser (usually at `http://localhost:8501`).

## Project Structure

- `app.py`: Main entry point and navigation setup.
- `database.py`: Firebase initialization and Firestore helper functions.
- `pages/`:
  - `portfolio.py`: The main dashboard logic and visualizations.
  - `transactions.py`: The interface for logging new trades.
- `requirements.txt`: List of Python dependencies.
- `firebaseServiceAccountKey.json`: (Not included in repo) Your private Firebase credentials.
