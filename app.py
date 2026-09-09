import streamlit as st

# Once, here, before anything renders: with st.navigation the entry point runs
# before every page, so this is the only place a layout applies to all of them.
# Setting it inside one page left the other page at the default centred width.
# Tab titles are left to each st.Page below.
st.set_page_config(layout="wide", page_icon="💰")

# Define the pages
portfolio_page = st.Page("pages/portfolio.py", title="Current Portfolio", icon="💰")
transactions_page = st.Page("pages/transactions.py", title="Log Transactions", icon="📝")
# analysis_page = st.Page("pages/analysis.py", title="AI Analysis", icon="📈")

# Create Navigation
# pg = st.navigation([portfolio_page, transactions_page, analysis_page])
pg = st.navigation([portfolio_page, transactions_page])

# Run the selected page
pg.run()