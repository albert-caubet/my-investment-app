"""Fundamental metrics against hand-computed numbers."""

import pytest

from invest.fundamentals import metrics as m


def test_capital_structure():
    assert m.total_debt(100, 400) == 500
    assert m.total_debt(None, 400) == 400
    assert m.total_debt(None, None) is None
    assert m.net_debt(500, 200, 50) == 250
    assert m.enterprise_value(1000, 500, 200, 50) == 1250
    assert m.enterprise_value(1000, None, None) == 1000
    assert m.enterprise_value(None, 1, 1) is None


def test_greenblatt_measures():
    assert m.earnings_yield(125, 1250) == pytest.approx(0.10)
    assert m.earnings_yield(125, 0) is None
    # NWC: (current assets 300 - cash 50 - investments 20) - (current liabilities 150 - short debt 30) = 110
    assert m.net_working_capital(300, 150, cash=50, short_term_investments=20, debt_current=30) == 110
    assert m.net_working_capital(100, 400) == 0.0  # floored
    assert m.roic(125, 110, 390) == pytest.approx(0.25)
    assert m.roic(125, 0, 0) is None


def test_cash_and_margins():
    assert m.free_cash_flow(200, 60) == 140
    assert m.free_cash_flow(200, -60) == 140  # capex sign convention does not matter
    assert m.fcf_yield(140, 2000) == pytest.approx(0.07)
    assert m.gross_margin(1000, gross_profit=400) == pytest.approx(0.4)
    assert m.gross_margin(1000, cost_of_revenue=600) == pytest.approx(0.4)
    assert m.gross_margin(0, gross_profit=1) is None
    assert m.operating_margin(1000, 150) == pytest.approx(0.15)


def test_slope_cagr_and_stability():
    assert m.slope_per_year({2021: 0.40, 2022: 0.42, 2023: 0.44}) == pytest.approx(0.02)
    assert m.slope_per_year({2021: 0.40, 2022: 0.42}) is None
    assert m.cagr(100, 133.1, 3) == pytest.approx(0.10, abs=1e-9)
    assert m.cagr(-5, 100, 3) is None
    assert m.stability([0.10, 0.10, 0.10]) == pytest.approx(0.0, abs=1e-12)
    assert m.stability([0.10, 0.20]) is None


def test_leverage_and_quality():
    assert m.ebitda(125, 25) == 150
    assert m.net_debt_to_ebitda(300, 150) == pytest.approx(2.0)
    assert m.net_debt_to_ebitda(300, 0) is None
    assert m.interest_coverage(125, 25) == pytest.approx(5.0)
    assert m.interest_coverage(125, 0) is None
    # Altman with A = 0.1, B = 0.2, C = 0.1, D = 2.0, E = 1.0
    z = m.altman_z(100, 200, 100, 1000, 500, 1000, 1000)
    assert z == pytest.approx(1.2 * 0.1 + 1.4 * 0.2 + 3.3 * 0.1 + 0.6 * 2.0 + 1.0 * 1.0)
    assert m.accruals_ratio(120, 100, 1100, 900) == pytest.approx(20 / 1000)
    assert m.share_count_change(110, 100) == pytest.approx(0.10)
    assert m.shareholder_yield(30, 70, 2000) == pytest.approx(0.05)
    assert m.shareholder_yield(None, None, 2000) == 0.0


def test_piotroski_all_nine_pass_and_fail():
    good_prev = {"net_income": 50, "cfo": 60, "total_assets": 1000, "total_assets_prev": 950, "debt_noncurrent": 300,
                 "current_assets": 400, "current_liabilities": 200, "shares_outstanding": 100, "gross_margin": 0.40,
                 "revenue": 900}
    good_curr = {"net_income": 80, "cfo": 120, "total_assets": 1100, "debt_noncurrent": 250, "current_assets": 500,
                 "current_liabilities": 200, "shares_outstanding": 95, "gross_margin": 0.45, "revenue": 1100}
    score, checks = m.piotroski(good_curr, good_prev)
    assert score == 9 and all(checks.values())
    bad_curr = {"net_income": -10, "cfo": -20, "total_assets": 1100, "debt_noncurrent": 400, "current_assets": 300,
                "current_liabilities": 200, "shares_outstanding": 120, "gross_margin": 0.30, "revenue": 700}
    score, checks = m.piotroski(bad_curr, good_prev)
    assert score == 0
    # cfo > net income still passes when both are negative but cash beats earnings
    mixed = dict(bad_curr, cfo=-5)
    score, checks = m.piotroski(mixed, good_prev)
    assert checks["cfo_exceeds_net_income"] is True and score == 1


def test_piotroski_with_too_little_data_is_none():
    score, checks = m.piotroski({"net_income": 1, "cfo": 2}, {})
    assert score is None
    assert checks["cfo_positive"] is True and checks["cfo_exceeds_net_income"] is True
    assert checks["roa_positive"] is None  # no total assets to divide by
    assert checks["leverage_fell"] is None


def test_multiples():
    assert m.price_to_earnings(2000, 100) == 20
    assert m.price_to_earnings(2000, -100) is None
    assert m.price_to_book(2000, 500) == 4
    assert m.ev_to_ebitda(2500, 250) == 10
    assert m.ev_to_sales(2500, 1000) == 2.5
    assert m.price_to_fcf(2000, 100) == 20
    assert m.price_to_fcf(2000, 0) is None


def test_value_trap_flags():
    flags = m.value_trap_flags(
        revenue_by_year={2022: 100, 2023: 95, 2024: 90, 2025: 85},
        fcf_by_year={2021: -1, 2022: 1, 2023: -1, 2024: -1, 2025: 1},
        net_debt_ebitda=5.0, f_score=3, dilution_3y=0.25,
    )
    assert len(flags) == 5
    assert m.value_trap_flags(revenue_by_year={2024: 100, 2025: 110}, fcf_by_year=None, net_debt_ebitda=1.0,
                              f_score=7, dilution_3y=-0.05) == []


def test_compute_all_end_to_end():
    latest = {"revenue": 1000, "gross_profit": 400, "operating_income": 150, "net_income": 100, "cfo": 180,
              "capex": 40, "depreciation": 30, "total_assets": 1200, "current_assets": 400, "current_liabilities": 200,
              "total_liabilities": 700, "cash": 100, "short_term_investments": 0, "debt_current": 50,
              "debt_noncurrent": 350, "ppe_net": 500, "equity": 500, "retained_earnings": 300,
              "shares_outstanding": 100, "dividends": 20, "buybacks": 30, "interest_expense": 20}
    previous = {"revenue": 900, "gross_profit": 340, "operating_income": 120, "net_income": 80, "cfo": 150,
                "total_assets": 1100, "current_assets": 350, "current_liabilities": 200, "debt_noncurrent": 380,
                "shares_outstanding": 102, "total_assets_prev": 1000}
    history = {
        "revenue": {2022: 800, 2023: 900, 2024: 950, 2025: 1000},
        "net_income": {2022: 60, 2023: 70, 2024: 80, 2025: 100},
        "gross_margin": {2023: 0.36, 2024: 0.38, 2025: 0.40},
        "operating_margin": {2023: 0.12, 2024: 0.13, 2025: 0.15},
        "fcf": {2021: 90, 2022: 100, 2023: 110, 2024: 120, 2025: 140},
        "shares_outstanding": {2022: 110, 2023: 106, 2024: 102, 2025: 100},
    }
    ms = m.compute_all(latest, previous, history, market_cap=2000)
    v = ms.values
    # EV = 2000 + (400 debt - 100 cash) = 2300; EY = 150 / 2300
    assert v["enterprise_value"] == 2300 and v["earnings_yield"] == pytest.approx(150 / 2300)
    # NWC = (400 - 100) - (200 - 50) = 150; ROIC = 150 / (150 + 500)
    assert v["roic"] == pytest.approx(150 / 650)
    assert v["fcf"] == 140 and v["fcf_yield"] == pytest.approx(0.07)
    assert v["net_debt_to_ebitda"] == pytest.approx(300 / 180)
    assert v["accruals"] == pytest.approx((100 - 180) / 1150)
    assert v["shareholder_yield"] == pytest.approx(0.025)
    assert v["pe"] == 20 and v["pb"] == 4
    assert v["gross_margin_slope"] == pytest.approx(0.02)
    assert v["revenue_cagr_3y"] == pytest.approx((1000 / 800) ** (1 / 3) - 1)
    assert v["dilution_3y"] == pytest.approx(100 / 110 - 1)
    assert v["f_score"] == 9
    assert v["value_trap_flags"] == []
    assert ms.notes == []
    without_price = m.compute_all(latest, previous, history, market_cap=None)
    assert without_price["earnings_yield"] is None and without_price.notes
