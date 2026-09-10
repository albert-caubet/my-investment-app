"""The shipped catalog must load, and the validator must catch the mistakes that matter."""

import pytest

from invest.data.sources import Fetcher
from invest.macro.catalog import GROUPS, CatalogError, load_catalog, parse_catalog


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_shipped_catalog_loads_with_many_series(catalog):
    assert len(catalog) > 100
    assert catalog["US_10Y"].critical
    assert catalog["US_10Y"].source == "fred" and catalog["US_10Y"].key == "DGS10"


def test_every_group_in_use_is_known(catalog):
    assert set(s.group for s in catalog) <= set(GROUPS)


def test_derived_order_puts_inputs_first(catalog):
    order = [s.id for s in catalog.derived_in_order()]
    assert order.index("US_CLAIMS_4WK") < order.index("US_CLAIMS_4WK_YOY")
    assert order.index("US_NET_LIQUIDITY") < order.index("US_NET_LIQUIDITY_13W")
    assert order.index("US_BUFFETT") < order.index("US_BUFFETT_VS_TREND")


def test_fallbacks_point_at_existing_series(catalog):
    for spec in catalog:
        if spec.fallback:
            assert spec.fallback in catalog.by_id


def test_release_series_have_no_staleness_limit(catalog):
    assert catalog["US_ISM_MFG_PMI"].stale_after_days is None
    assert catalog["EA_DFR"].stale_after_days is None  # irregular


def test_staleness_limit_is_period_plus_lag_plus_grace(catalog):
    # two periods (the newest value is the previous period's until the next release) plus lag plus grace
    assert catalog["US_CPI"].stale_after_days == 2 * 31 + 13 + 7
    assert catalog["US_10Y"].stale_after_days == 2 * 1 + 1 + 7


def test_derived_series_inherit_the_slowest_input_limit(catalog):
    # SP500_NOMINAL_EPS_GROWTH is yoy(SHILLER_EARNINGS); earnings arrive a quarter late
    assert catalog.stale_after(catalog["SP500_NOMINAL_EPS_GROWTH"]) == catalog["SHILLER_EARNINGS"].stale_after_days
    assert catalog.stale_after(catalog["US_10Y"]) == catalog["US_10Y"].stale_after_days
    # a derived series whose only inputs are releases has no limit
    assert catalog.stale_after(catalog["US_ISM_NEW_ORDERS_MINUS_INVENTORIES"]) is None


def test_dispatcher_handles_every_fetched_source(catalog):
    fetcher = Fetcher()
    handled = {"fred", "ecb", "eurostat", "oecd", "bis", "yahoo", "shiller", "ebp", "philly",
               "nyfed_recprob", "hlw", "cot"}
    assert {s.source for s in catalog.fetched()} <= handled
    assert fetcher.yahoo_frames == {}


def _one(**over):
    base = {"id": "X", "label": "x", "group": "rates", "source": "fred", "key": "DGS10", "frequency": "D"}
    base.update(over)
    return {"series": [base]}


def test_unknown_source_is_refused():
    with pytest.raises(CatalogError, match="unknown source"):
        parse_catalog(_one(source="bloomberg"))


def test_fetched_series_needs_a_key():
    with pytest.raises(CatalogError, match="needs a key"):
        parse_catalog(_one(key=None))


def test_release_series_needs_no_key():
    catalog = parse_catalog(_one(source="release", key=None, frequency="M"))
    assert catalog["X"].is_release


def test_derived_needs_formula_and_inputs():
    with pytest.raises(CatalogError, match="needs a formula"):
        parse_catalog(_one(source="derived", key=None))
    with pytest.raises(CatalogError, match="unknown formula"):
        parse_catalog(_one(source="derived", key=None, formula="magic", inputs=["X"]))


def test_duplicate_ids_are_refused():
    payload = {"series": [_one()["series"][0], _one()["series"][0]]}
    with pytest.raises(CatalogError, match="duplicate"):
        parse_catalog(payload)


def test_unknown_fallback_and_input_are_refused():
    with pytest.raises(CatalogError, match="fallback"):
        parse_catalog(_one(fallback="NOPE"))
    payload = {"series": [_one()["series"][0],
                          {"id": "D", "label": "d", "group": "rates", "source": "derived", "formula": "yoy", "inputs": ["NOPE"]}]}
    with pytest.raises(CatalogError, match="input"):
        parse_catalog(payload)


def test_cycles_between_derived_series_are_refused():
    payload = {"series": [
        {"id": "A", "label": "a", "group": "rates", "source": "derived", "formula": "yoy", "inputs": ["B"]},
        {"id": "B", "label": "b", "group": "rates", "source": "derived", "formula": "yoy", "inputs": ["A"]},
    ]}
    with pytest.raises(CatalogError, match="cycle"):
        parse_catalog(payload)


@pytest.mark.parametrize("field,value", [("frequency", "H"), ("direction", "up"), ("transform", "log"), ("group", "misc")])
def test_enumerated_fields_are_validated(field, value):
    with pytest.raises(CatalogError):
        parse_catalog(_one(**{field: value}))
