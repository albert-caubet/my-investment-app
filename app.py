import streamlit as st

# Once, here, before anything renders: with st.navigation the entry point runs
# before every page, so this is the only place a layout applies to all of them.
# Setting it inside one page left the other page at the default centred width.
# Tab titles are left to each st.Page below.
st.set_page_config(layout="wide", page_icon="💰")

# Define the pages
portfolio_page = st.Page("pages/portfolio.py", title="Current Portfolio", icon="💰")
transactions_page = st.Page("pages/transactions.py", title="Log Transactions", icon="📝")
# Portfolio design values the holdings like the dashboard does. The other analysis
# pages read the DuckDB file written by `python -m invest.jobs.refresh`; they never
# fetch from the network on their own.
design_page = st.Page("pages/design.py", title="Portfolio design", icon="🧭")
macro_page = st.Page("pages/macro.py", title="Macro", icon="🌍")
screener_page = st.Page("pages/screener.py", title="Screener", icon="🔎")
report_page = st.Page("pages/report.py", title="Weekly report", icon="📄")
# A sandbox: charts of anything found by search. Unlike the other analysis pages it
# fetches from Yahoo Finance and FRED itself, as the dashboard does.
plotter_page = st.Page("pages/plotter.py", title="Custom charts", icon="📈")

# Create Navigation
pg = st.navigation(
    {
        "Portfolio": [portfolio_page, transactions_page],
        "Analysis": [design_page, macro_page, screener_page, plotter_page, report_page],
    }
)

# Run the selected page
pg.run()