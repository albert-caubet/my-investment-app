# One image for both the Streamlit app and the jobs (PLAN.md section 8, Stage C).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_NO_CACHE_DIR=1 \
    INVEST_DATA_DIR=/data STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
VOLUME ["/data"]
EXPOSE 8501

# The app by default; docker-compose overrides the command for the jobs container.
CMD ["streamlit", "run", "app.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.headless", "true"]
