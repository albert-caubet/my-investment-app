"""Headless smoke tests for both pages, on canned data. No network, no Firestore.

``AppTest`` runs the entry point in-process. Every network and database function
the pages call is replaced before the run, so what is exercised is the page code
itself: loading, replaying, rendering, and the create / edit / delete flows.
"""

import base64
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import charts
import database
import market_data as md

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "transactions_sample.json").read_text("utf-8"))
DAYS = pd.bdate_range("2024-06-03", periods=300)

# Newest trade in the fixture, so it is row 0 of the date-descending history log.
NEWEST_ID = "eur_drifted_cost"


CASH = [
    {"name": "Main account", "category": "Cash", "balance_eur": 5000.0, "updated": "2026-09-20"},
    {"name": "Broker cash", "category": "Dry Powder", "balance_eur": 2500.0, "updated": "2026-09-20"},
]


@pytest.fixture
def canned(monkeypatch):
    """Canned data sources, plus a recorder for every write the pages attempt."""
    writes = {"added": [], "updated": [], "deleted": [], "cash": [], "design": []}

    monkeypatch.setattr(database, "get_cash_accounts", lambda: [dict(a) for a in CASH])
    monkeypatch.setattr(database, "save_cash_accounts", lambda accounts: writes["cash"].append(accounts))
    monkeypatch.setattr(database, "get_portfolio_design", lambda: None)  # none saved; tests override
    monkeypatch.setattr(database, "save_portfolio_design", lambda design: writes["design"].append(design))
    monkeypatch.setattr(database, "get_all_transactions", lambda: [dict(d) for d in FIXTURE])
    monkeypatch.setattr(database, "clear_transaction_cache", lambda: None)
    monkeypatch.setattr(database, "record_transaction", lambda data: writes["added"].append(data))
    monkeypatch.setattr(
        database, "update_transaction", lambda doc_id, data: writes["updated"].append((doc_id, data))
    )
    monkeypatch.setattr(database, "delete_transaction", lambda doc_id: writes["deleted"].append(doc_id))

    def price_history(tickers, period, *, adjusted=True):
        rng = np.random.default_rng(0)
        return pd.DataFrame(
            {t: 100.0 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(DAYS))) for t in tickers},
            index=DAYS,
        )

    monkeypatch.setattr(
        md, "fetch_listing_currency", lambda tickers: {t: ("USD" if t == "USST" else "EUR") for t in tickers}
    )
    monkeypatch.setattr(md, "fetch_listing_name", lambda symbol: "Canned Name")
    monkeypatch.setattr(md, "fetch_spot_prices", lambda tickers: {t: 12.0 for t in tickers})
    monkeypatch.setattr(md, "fetch_spot_fx", lambda currencies: {"EUR": 1.0, "USD": 1.17})
    monkeypatch.setattr(md, "fetch_historical_fx", lambda date_iso, ccy: 1.0 if ccy == "EUR" else 1.17)
    monkeypatch.setattr(md, "fetch_price_history", price_history)
    monkeypatch.setattr(md, "fetch_fx_history", lambda currencies, period: pd.DataFrame({"USD": 1.1}, index=DAYS))
    monkeypatch.setattr(
        md,
        "fetch_hicp_index",
        lambda area=None: {"2025-01": 125.0, "2025-02": 125.3, "2025-06": 126.5, "2025-09": 127.17, "2025-12": 128.28},
    )
    monkeypatch.setattr(md, "fetch_hicp_annual_rate", lambda area=None: (0.03, "2025-12"))
    monkeypatch.setattr(md, "resolve_isin", lambda isin: None)
    return writes


def _app() -> AppTest:
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)


def _transactions_page(at: AppTest) -> AppTest:
    return at.switch_page("pages/transactions.py").run()


def _select_history_row(at: AppTest, row: int) -> AppTest:
    """Run with `row` selected in the history table.

    AppTest has no frontend to hold a dataframe selection, so a seeded selection
    is consumed by exactly one run and reads back empty on the next. Call this
    for every run that should still have the row selected -- after a click, not
    just once -- which mirrors the browser, where the widget keeps the selection.
    """
    at.session_state["tx_log_0"] = {"selection": {"rows": [row], "columns": []}}
    return at.run()


# --- portfolio ---------------------------------------------------------------


def test_portfolio_page_renders_on_canned_data(canned):
    at = _app().run()
    assert not at.exception
    labels = [m.label for m in at.metric]
    for expected in ("Total Value (EUR)", "Unrealised PnL (EUR)", "Realised PnL (EUR)", "Total PnL (EUR)"):
        assert expected in labels
    # Nothing was left unvalued: every canned symbol had a price and a currency.
    assert not any("could not be valued" in w.value for w in at.warning)
    assert canned == {"added": [], "updated": [], "deleted": [], "cash": [], "design": []}


def test_portfolio_refuses_to_value_an_unknown_listing_currency(canned, monkeypatch):
    monkeypatch.setattr(md, "fetch_listing_currency", lambda tickers: {t: None for t in tickers})
    at = _app().run()
    assert not at.exception
    # The fixture stores no listing currency, so every position is unvalued rather
    # than quietly treated as EUR.
    assert any("could not be valued" in w.value for w in at.warning)


def _price_chart_cells(at: AppTest) -> list:
    """The columns holding an asset's price-history expander, in page order."""
    return [c for c in at.columns if any(e.label.startswith("📈") for e in c.expander)]


def test_price_charts_sit_two_to_a_row_by_default(canned):
    at = _app().run()
    assert not at.exception
    assert at.button_group(key="charts_per_row").value == 2
    cells = _price_chart_cells(at)
    assert len(cells) > 2  # enough open positions to fill more than one row
    assert {c.weight for c in cells} == {1 / 2}
    assert all(len(c.expander) == 1 for c in cells)  # one chart to a cell


@pytest.mark.parametrize("per_row", [1, 3])
def test_charts_per_row_rearranges_the_price_charts(canned, per_row):
    at = _app().run()
    n_charts = len(_price_chart_cells(at))
    at.button_group(key="charts_per_row").set_value(per_row).run()
    assert not at.exception
    cells = _price_chart_cells(at)
    assert len(cells) == n_charts
    assert {round(c.weight, 6) for c in cells} == {round(1 / per_row, 6)}


def test_charts_per_row_is_read_from_the_url(canned):
    at = _app()
    at.query_params["charts_per_row"] = "3"
    at.run()
    assert not at.exception
    assert at.button_group(key="charts_per_row").value == 3


def _price_chart_heights(at: AppTest) -> set[int]:
    charts_shown = [chart for cell in _price_chart_cells(at) for chart in cell.get("plotly_chart")]
    assert charts_shown
    return {json.loads(chart.proto.spec)["layout"]["height"] for chart in charts_shown}


def test_price_charts_are_medium_height_by_default(canned):
    at = _app().run()
    assert not at.exception
    assert at.button_group(key="chart_height").value == "Medium"
    assert _price_chart_heights(at) == {charts.HEIGHTS["Medium"]}


@pytest.mark.parametrize("size", ["Small", "Large"])
def test_chart_height_sets_every_price_chart(canned, size):
    at = _app().run()
    at.button_group(key="chart_height").set_value(size).run()
    assert not at.exception
    assert _price_chart_heights(at) == {charts.HEIGHTS[size]}
    # the pies above are not price charts and keep their height
    pies = [c for c in at.get("plotly_chart") if json.loads(c.proto.spec)["data"][0]["type"] == "pie"]
    scaled_default = round(charts.DEFAULT_HEIGHT * charts.HEIGHT_SCALE)
    assert {json.loads(c.proto.spec)["layout"]["height"] for c in pies} == {scaled_default}


def test_chart_height_is_read_from_the_url(canned):
    at = _app()
    at.query_params["chart_height"] = "Large"
    at.run()
    assert not at.exception
    assert at.button_group(key="chart_height").value == "Large"


def _price_figures(at: AppTest) -> list[dict]:
    return [json.loads(chart.proto.spec) for cell in _price_chart_cells(at) for chart in cell.get("plotly_chart")]


def _numbers(values) -> np.ndarray:
    """A trace's numbers, written by Plotly as a plain list or as a base64 typed array."""
    if isinstance(values, dict):
        return np.frombuffer(base64.b64decode(values["bdata"]), dtype=values["dtype"]).astype(float)
    return np.asarray(values, dtype=float)


def test_the_y_axis_shows_prices_by_default(canned):
    at = _app().run()
    assert not at.exception
    assert at.button_group(key="y_axis").value == "Price"
    for fig in _price_figures(at):
        assert fig["layout"]["yaxis"]["title"]["text"].startswith("Unadjusted close")
        assert "tickformat" not in fig["layout"]["yaxis"]
    assert not any("dividends paid out" in c.value for c in at.caption)


def test_percent_change_puts_everything_on_the_first_close_of_the_range(canned):
    at = _app().run()
    before = _price_figures(at)
    assert any(len(fig["data"]) > 1 for fig in before)  # trade markers to convert
    assert any(fig["layout"].get("shapes") for fig in before)  # cost lines to convert
    at.button_group(key="y_axis").set_value("Change").run()
    assert not at.exception
    after = _price_figures(at)
    assert len(after) == len(before)
    for price_fig, pct_fig in zip(before, after):
        first = _numbers(price_fig["data"][0]["y"])[0]
        for price_trace, pct_trace in zip(price_fig["data"], pct_fig["data"], strict=True):  # line, markers
            np.testing.assert_allclose(_numbers(pct_trace["y"]), _numbers(price_trace["y"]) / first - 1)
        price_lines = [shape["y0"] for shape in price_fig["layout"].get("shapes", [])]
        pct_lines = [shape["y0"] for shape in pct_fig["layout"].get("shapes", [])]
        np.testing.assert_allclose(pct_lines, np.asarray(price_lines, dtype=float) / first - 1)
        assert pct_fig["layout"]["yaxis"]["tickformat"] == "+.1~%"
        assert pct_fig["layout"]["yaxis"]["title"]["text"].startswith("Change since")
    assert any("dividends paid out are not counted" in c.value for c in at.caption)


def test_y_axis_is_read_from_the_url(canned):
    at = _app()
    at.query_params["y_axis"] = "Change"
    at.run()
    assert not at.exception
    assert at.button_group(key="y_axis").value == "Change"


# --- transactions: create ----------------------------------------------------


def test_transactions_page_renders_the_history(canned):
    at = _transactions_page(_app().run())
    assert not at.exception
    # The history log first, then the cash-accounts editor at the bottom of the page.
    assert [d.key for d in at.dataframe] == ["tx_log_0", "cash_editor_0"]
    assert len(at.dataframe[0].value) == len(FIXTURE)


def test_logging_a_trade_writes_a_schema_v2_document(canned):
    at = _transactions_page(_app().run())
    at.text_input[0].set_value("ABC")  # ticker
    at.number_input[0].set_value(10.0)  # quantity
    at.number_input[1].set_value(100.0)  # price
    at.number_input[2].set_value(9.95)  # fees
    at.button(key="save_trade").click().run()

    assert not at.exception
    assert at.success
    assert len(canned["added"]) == 1
    doc = canned["added"][0]
    assert doc["ticker"] == "ABC"
    assert doc["currency"] == "EUR"
    assert doc["fx_rate"] == 1.0
    assert doc["cost_eur"] == pytest.approx(1009.95)
    assert doc["schema_version"] == 2
    assert "timestamp" in doc


def test_logging_without_an_identifier_is_refused(canned):
    at = _transactions_page(_app().run())
    at.number_input[0].set_value(10.0)
    at.number_input[1].set_value(100.0)
    at.button(key="save_trade").click().run()
    assert at.error
    assert canned["added"] == []


# --- transactions: edit and delete -------------------------------------------


def test_selecting_a_row_opens_the_edit_form_prefilled(canned):
    at = _select_history_row(_transactions_page(_app().run()), 0)
    assert not at.exception
    original = next(d for d in FIXTURE if d["_doc_id"] == NEWEST_ID)
    assert at.number_input(key=f"edit_quantity_{NEWEST_ID}").value == pytest.approx(original["quantity"])
    assert at.number_input(key=f"edit_price_{NEWEST_ID}").value == pytest.approx(original["price_nominal"])
    assert at.text_input(key=f"edit_isin_{NEWEST_ID}").value == original["isin"]


def test_saving_an_edit_rewrites_the_document_and_keeps_its_timestamp(canned):
    at = _select_history_row(_transactions_page(_app().run()), 0)
    at.number_input(key=f"edit_quantity_{NEWEST_ID}").set_value(300.0)
    at.button(key=f"edit_save_{NEWEST_ID}").click()
    at = _select_history_row(at, 0)

    assert not at.exception
    assert len(canned["updated"]) == 1
    doc_id, doc = canned["updated"][0]
    assert doc_id == NEWEST_ID
    assert doc["quantity"] == 300.0
    assert doc["price_nominal"] == pytest.approx(8.026)
    assert doc["currency"] == "EUR" and doc["fx_rate"] == 1.0
    # The fixture row stored a drifted cost_eur; the rewrite recomputes it.
    assert doc["cost_eur"] == pytest.approx(300.0 * 8.026)
    assert doc["schema_version"] == 2
    assert "updated_at" in doc
    # The rerun after saving shows the outcome and, by re-keying the table,
    # clears the selection so the form is gone.
    assert at.success
    assert not any(b.label == "Save changes" for b in at.button)


def test_editing_a_usd_trade_keeps_its_stored_rate_when_date_is_unchanged(canned):
    at = _transactions_page(_app().run())
    log = at.dataframe[0].value
    row = int(log.index[log["_doc_id"] == "usd_buy"][0])
    at = _select_history_row(at, row)
    at.number_input(key="edit_fees_usd_buy").set_value(5.0)
    at.button(key="edit_save_usd_buy").click()
    at = _select_history_row(at, row)

    assert not at.exception
    (doc_id, doc), = canned["updated"]
    assert doc_id == "usd_buy"
    assert doc["fx_rate"] == pytest.approx(1.17)  # stored rate, not re-fetched
    assert doc["cost_eur"] == pytest.approx((11.7 * 100.0 + 5.0) / 1.17)


def test_delete_requires_the_confirmation_box(canned):
    at = _select_history_row(_transactions_page(_app().run()), 0)
    at.button(key=f"edit_delete_{NEWEST_ID}").click()
    at = _select_history_row(at, 0)
    assert at.error
    assert canned["deleted"] == []

    at.checkbox(key=f"edit_confirm_{NEWEST_ID}").check()
    at.button(key=f"edit_delete_{NEWEST_ID}").click()
    at = _select_history_row(at, 0)
    assert not at.exception
    assert canned["deleted"] == [NEWEST_ID]
    assert at.success


def test_a_legacy_document_can_be_opened_and_upgraded(canned):
    """The legacy row has `price` and no currency; saving it writes schema v2."""
    at = _transactions_page(_app().run())
    log = at.dataframe[0].value
    row = int(log.index[log["_doc_id"] == "legacy_schema"][0])
    at = _select_history_row(at, row)
    assert at.number_input(key="edit_price_legacy_schema").value == pytest.approx(50.0)

    at.button(key="edit_save_legacy_schema").click()
    at = _select_history_row(at, row)
    assert not at.exception
    (doc_id, doc), = canned["updated"]
    assert doc_id == "legacy_schema"
    assert doc["price_nominal"] == 50.0 and "price" not in doc
    assert doc["currency"] == "EUR" and doc["schema_version"] == 2


# --- cash accounts and portfolio design --------------------------------------


def _euros(metric) -> float:
    return float(metric.value.replace("€", "").replace(",", ""))


def _metric(at: AppTest, label: str):
    return next(m for m in at.metric if m.label == label)


def test_dashboard_keeps_the_cash_reserve_outside_the_portfolio(canned):
    at = _app().run()
    assert not at.exception
    total_value = _euros(_metric(at, "Total Value (EUR)"))
    # Portfolio value is the investments plus Dry Powder (2,500); the 5,000 reserve is not in it.
    assert _euros(_metric(at, "Portfolio value (EUR)")) == pytest.approx(total_value + 2500, abs=1)
    assert _euros(_metric(at, "Cash reserve (EUR)")) == 5000

    table = at.dataframe[0].value
    assert "Broker cash" in set(table["name"])  # Dry Powder is listed with the holdings
    assert "Main account" not in set(table["name"])  # the reserve is not
    # Weight is a share of the portfolio, so it sums to 100 without the reserve.
    assert table["Weight (%)"].sum() == pytest.approx(100.0)
    investments = table.loc[table["category"] != "Dry Powder", "Market Value (EUR)"].sum()
    assert total_value == pytest.approx(investments, abs=1)


def test_transactions_page_lists_the_cash_accounts(canned):
    at = _transactions_page(_app().run())
    assert not at.exception
    assert "Cash accounts" in [h.value for h in at.subheader]
    editor = next(d for d in at.dataframe if d.key == "cash_editor_0").value
    assert list(editor["name"]) == ["Main account", "Broker cash"]
    assert list(editor["balance_eur"]) == [5000.0, 2500.0]
    assert any("Cash €5,000.00 · Dry Powder €2,500.00 · last saved 2026-09-20" in c.value for c in at.caption)
    assert canned["cash"] == []  # nothing saved without pressing Save


SAVED_DESIGN = {
    "targets": {"Dry Powder": 10, "Bonds": 20, "Equity funds": 50, "Stocks": 20},
    "saved": "2026-09-25",
}
BANDS = ("Dry Powder", "Bonds", "Equity funds", "Stocks")


def _design_page(dashboard: AppTest | None = None) -> AppTest:
    return (dashboard or _app().run()).switch_page("pages/design.py").run()


def _sliders(at: AppTest) -> dict[str, int]:
    return {band: at.slider(key=f"design_{band}").value for band in BANDS}


def test_design_page_values_the_portfolio_without_the_reserve(canned):
    dashboard = _app().run()
    total_value = _euros(_metric(dashboard, "Total Value (EUR)"))  # before switch_page reuses the app
    at = _design_page(dashboard)
    assert not at.exception

    investments = _euros(_metric(at, "Investments (EUR)"))
    # The same valuation code as the dashboard, so the same number.
    assert investments == pytest.approx(total_value, abs=1)
    assert _euros(_metric(at, "Dry Powder (EUR)")) == 2500
    assert _euros(_metric(at, "Portfolio value (EUR)")) == pytest.approx(investments + 2500, abs=1)
    assert "Cash" not in _sliders(at)  # no slider for the reserve
    assert at.get("plotly_chart")


def test_sliders_start_from_todays_allocation_when_nothing_is_saved(canned):
    at = _design_page()
    sliders = _sliders(at)
    assert sum(sliders.values()) == 100  # rounded so it can be saved as is
    table = at.dataframe[0].value
    today = dict(zip(table["Category"], table["Today (%)"]))
    for band in BANDS:
        assert sliders[band] == pytest.approx(today[band], abs=1)
    assert not at.button(key="save_design").disabled


def test_sliders_start_from_the_saved_design(canned, monkeypatch):
    monkeypatch.setattr(database, "get_portfolio_design", lambda: dict(SAVED_DESIGN))
    at = _design_page()
    assert _sliders(at) == SAVED_DESIGN["targets"]
    assert any("This is your saved design (saved 2026-09-25)" in c.value for c in at.caption)


def test_moving_a_slider_rebalances_the_others_in_proportion(canned, monkeypatch):
    monkeypatch.setattr(database, "get_portfolio_design", lambda: dict(SAVED_DESIGN))
    at = _design_page()
    at.slider(key="design_Stocks").set_value(40).run()
    assert not at.exception
    # 10 : 20 : 50 share the remaining 60 the same way.
    assert _sliders(at) == {"Dry Powder": 8, "Bonds": 15, "Equity funds": 37, "Stocks": 40}
    design = at.dataframe[0].value.set_index("Category")["Design (%)"]
    assert design["Stocks"] == 40 and design.sum() == 100  # the table follows the sliders
    assert any("not saved yet" in c.value for c in at.caption)


def test_a_locked_category_stays_put_and_cannot_be_dragged(canned, monkeypatch):
    monkeypatch.setattr(database, "get_portfolio_design", lambda: dict(SAVED_DESIGN))
    at = _design_page()
    at.checkbox(key="lock_Equity funds").check().run()
    assert at.slider(key="design_Equity funds").disabled

    at.slider(key="design_Stocks").set_value(40).run()
    sliders = _sliders(at)
    assert sliders["Equity funds"] == 50 and sliders["Stocks"] == 40
    assert sum(sliders.values()) == 100


def test_with_one_category_unlocked_nothing_can_move(canned, monkeypatch):
    monkeypatch.setattr(database, "get_portfolio_design", lambda: dict(SAVED_DESIGN))
    at = _design_page()
    for band in ("Dry Powder", "Bonds", "Equity funds"):
        at.checkbox(key=f"lock_{band}").check()
    at.run()
    assert any("Unlock at least two categories" in c.value for c in at.caption)
    at.slider(key="design_Stocks").set_value(60).run()
    assert _sliders(at) == SAVED_DESIGN["targets"]  # snapped back: nothing could absorb it


def test_saving_the_sliders(canned, monkeypatch):
    monkeypatch.setattr(database, "get_portfolio_design", lambda: dict(SAVED_DESIGN))
    at = _design_page()
    at.slider(key="design_Stocks").set_value(40).run()
    at.button(key="save_design").click().run()
    assert not at.exception
    (saved,) = canned["design"]
    assert saved["targets"] == {"Dry Powder": 8, "Bonds": 15, "Equity funds": 37, "Stocks": 40}
    assert saved["saved"]  # dated
    assert any("Design saved" in s.value for s in at.success)


def test_design_page_without_anything_to_show(canned, monkeypatch):
    monkeypatch.setattr(database, "get_all_transactions", lambda: [])
    monkeypatch.setattr(database, "get_cash_accounts", lambda: [])
    at = _design_page()
    assert not at.exception
    assert any("Nothing to design yet" in i.value for i in at.info)


def test_a_cash_reserve_alone_is_not_a_portfolio(canned, monkeypatch):
    monkeypatch.setattr(database, "get_all_transactions", lambda: [])
    monkeypatch.setattr(database, "get_cash_accounts", lambda: [dict(CASH[0])])  # the Cash account only
    at = _design_page()
    assert not at.exception
    assert any("Nothing to design yet" in i.value for i in at.info)


def test_dashboard_without_a_design_points_to_the_design_page(canned):
    at = _app().run()
    assert not at.exception
    assert _metric(at, "Largest gap vs design").value == "–"
    assert "Allocation vs. design" in [h.value for h in at.subheader]
    assert any("No portfolio design saved yet" in i.value for i in at.info)


def test_dashboard_measures_the_allocation_against_the_design(canned, monkeypatch):
    monkeypatch.setattr(database, "get_portfolio_design", lambda: dict(SAVED_DESIGN))
    at = _app().run()
    assert not at.exception

    gaps = next(d for d in at.dataframe if "Off by (pp)" in d.value.columns).value
    assert list(gaps["Category"])[:4] == list(BANDS)
    assert "Cash" not in set(gaps["Category"])
    assert gaps["Design (%)"].tolist()[:4] == [10, 20, 50, 20]
    # Every category is today minus design, and the moves only shift money around.
    assert (gaps["Off by (pp)"] == gaps["Today (%)"] - gaps["Design (%)"]).all()
    assert gaps["To reach design (EUR)"].sum() == pytest.approx(0.0, abs=0.01)

    widest = gaps.loc[gaps["Off by (pp)"].abs().idxmax()]
    assert _metric(at, "Largest gap vs design").value == f"{widest['Off by (pp)']:+.1f} pp"


def test_dry_powder_follows_portfolio_value_in_the_top_metrics(canned):
    at = _app().run()
    labels = [m.label for m in at.metric]
    assert labels[labels.index("Portfolio value (EUR)") + 1] == "Dry Powder (EUR)"
    assert _euros(_metric(at, "Dry Powder (EUR)")) == 2500


def test_deleting_a_cash_account_needs_the_confirmation(canned):
    at = _transactions_page(_app().run())
    at.selectbox(key="cash_delete_pick_0").select("Broker cash")
    at.button(key="cash_delete_0").click().run()
    assert not at.exception
    assert any("Tick the confirmation box" in e.value for e in at.error)
    assert canned["cash"] == []


def test_deleting_a_cash_account_keeps_the_others_as_they_were(canned):
    at = _transactions_page(_app().run())
    at.selectbox(key="cash_delete_pick_0").select("Broker cash")
    at.checkbox(key="cash_delete_confirm_0").check()
    at.button(key="cash_delete_0").click().run()
    assert not at.exception
    (saved,) = canned["cash"]
    # Only the deleted account is gone; the other keeps its balance and its saved date.
    assert saved == [{"name": "Main account", "category": "Cash", "balance_eur": 5000.0, "updated": "2026-09-20"}]
    assert any("Deleted the account Broker cash" in s.value for s in at.success)


def test_design_table_shows_percentages_before_euros(canned):
    at = _design_page()
    assert list(at.dataframe[0].value.columns) == [
        "Category", "Today (%)", "Design (%)", "Off by (pp)", "Today (EUR)", "Design (EUR)", "To reach design (EUR)",
    ]
