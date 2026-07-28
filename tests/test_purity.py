"""portfolio_math must stay testable without Streamlit, Firestore or a network."""

import ast
from pathlib import Path

FORBIDDEN = {"streamlit", "yfinance", "firebase_admin", "google", "database", "market_data",
             "requests", "urllib"}


def test_no_io_imports():
    src = Path(__file__).resolve().parents[1] / "portfolio_math.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    leaked = imported & FORBIDDEN
    assert not leaked, f"portfolio_math imports {leaked}; it must stay pure"
