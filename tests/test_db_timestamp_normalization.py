"""SQLSTATE 22008 regression (live incident 2026-08-10): epoch values must be
normalized to ISO before hitting Supabase timestamptz columns."""
from services.database import _iso_ts


def test_epoch_int_to_iso():
    out = _iso_ts(1786348800)
    assert out == "2026-08-10T00:00:00+00:00" or out.startswith("2026-08-")


def test_epoch_digit_string_to_iso():
    out = _iso_ts("1786348800")
    assert "T" in out and "+00:00" in out


def test_iso_passthrough():
    iso = "2026-08-10T06:16:00+00:00"
    assert _iso_ts(iso) == iso


def test_none_and_empty():
    assert _iso_ts(None) is None
    assert _iso_ts("") is None
