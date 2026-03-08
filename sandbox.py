import yfinance as yf
import numpy as np
import plotly.matplotlylib as pl

data = yf.download('MSFT', period="5d", progress=True)['Close'].ffill()
ticker_obj = yf.Ticker('MSFT')
info = ticker_obj.info

def fetch_rich_metadata(tickers):
    """Fetches Analyst Targets, Currency, and Full Name."""
    metadata = {}
    for t in tickers:
        try:
            if "^" in t or "=" in t: continue
            ticker_obj = yf.Ticker(t)
            info = ticker_obj.info

            metadata[t] = {
                "Market Currency": info.get("currency", "???"),
                "Company Name": info.get("displayName", t),  # shortName, longName
                "Current Price": info.get("currentPrice") or info.get("regularMarketPrice")
            }
        except Exception:
            metadata[t] = {"Market Currency": "???", "Analyst Target": np.nan, "Company Name": t}
    return metadata