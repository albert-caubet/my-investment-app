import streamlit as st

# Define the pages
portfolio_page = st.Page("pages/portfolio.py", title="Current Portfolio", icon="💰")
transactions_page = st.Page("pages/transactions.py", title="Log Transactions", icon="📝")
# analysis_page = st.Page("pages/analysis.py", title="AI Analysis", icon="📈")

# Create Navigation
# pg = st.navigation([portfolio_page, transactions_page, analysis_page])
pg = st.navigation([portfolio_page, transactions_page])

# Run the selected page
pg.run()