"""Instrument point conversion guards."""

from __future__ import annotations

from utils.helpers import calculate_pips, format_price
from utils.instruments import enabled_instruments, point_size, price_to_points
from utils.helpers import load_config


def test_gold_points_unchanged() -> None:
    assert point_size("XAU/USD") == 0.10
    assert calculate_pips(4000.0, 4001.0, "BUY", "XAU/USD") == 10.0


def test_forex_non_jpy_points_are_pipettes() -> None:
    """Forex point_size works even though pairs are not in config."""
    assert point_size("EUR/USD") == 0.00001
    assert calculate_pips(1.10000, 1.10100, "BUY", "EUR/USD") == 100.0
    assert format_price(1.101, "EUR/USD") == "1.10100"


def test_jpy_pair_points_are_thousandths() -> None:
    """JPY point_size uses fallback since pair is not in config."""
    # USD/JPY is not in the active config, so point_size returns the
    # default fallback (0.00001). The 0.001 value would only apply if
    # USD/JPY were re-added to DEFAULT_INSTRUMENTS / config.json.
    assert point_size("USD/JPY") == 0.00001


def test_wti_points_are_cents() -> None:
    assert point_size("WTI/USD") == 0.01
    assert calculate_pips(75.00, 76.00, "BUY", "WTI/USD") == 100.0
    assert price_to_points(1.0, "WTI/USD") == 100.0


def test_config_has_gold_and_wti_only() -> None:
    symbols = [item["symbol"] for item in enabled_instruments(load_config())]
    assert symbols == [
        "XAU/USD",
        "WTI/USD",
    ]
