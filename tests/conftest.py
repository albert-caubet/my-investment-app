import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio_math import Transaction  # noqa: E402


@pytest.fixture
def tx():
    """Terse Transaction factory so each test reads as a single line."""

    def _make(
        action="Buy",
        qty=100.0,
        price=10.0,
        day=1,
        ccy="EUR",
        fx=1.0,
        fees=0.0,
        asset="TEST",
        seq=0,
        listing_ccy=None,
    ):
        return Transaction(
            asset_id=asset,
            # `day` is an offset, not a calendar day, so sequences can run long.
            trade_date=date(2025, 1, 1) + timedelta(days=day - 1),
            action=action,
            quantity=qty,
            price_nominal=price,
            currency=ccy,
            fx_rate=fx,
            fees=fees,
            seq=seq,
            listing_ccy=listing_ccy,
        )

    return _make
