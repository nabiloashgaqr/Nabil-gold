from services.macro_data_provider import MacroDataProvider


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, _url, params=None, timeout=20):
        symbol = params["symbol"]
        self.calls.append(symbol)
        # Twelve Data returns newest first; provider reverses to old->new.
        if symbol in {"EUR/USD", "GBP/USD"}:
            values = [{"close": "1.00"}, {"close": "1.02"}]
        elif symbol in {"USD/JPY", "USD/CNY"}:
            values = [{"close": "100.00"}, {"close": "99.00"}]
        else:  # SPY risk proxy
            values = [{"close": "500.00"}, {"close": "510.00"}]
        return FakeResponse({"values": values})


def test_macro_data_provider_builds_context_with_hourly_quota(monkeypatch):
    monkeypatch.setenv("TWELVEDATA_API_KEY", "demo")
    session = FakeSession()
    provider = MacroDataProvider({}, session=session)
    provider.request_pause_seconds = 0

    context = provider.build_context()

    assert len(session.calls) == 5
    assert context["source"] == "twelvedata_hourly_macro_proxy"
    assert context["quota_policy"]["credits_used_estimate"] == 5
    assert context["quota_policy"]["daily_estimate_at_hourly"] == 120
    assert context["dxy_trend"] in {"rising", "falling", "flat"}
    assert context["risk_sentiment"] in {"risk_on", "risk_off", "neutral"}
    assert context["data_quality"]["usable_symbols"] == 5


def test_database_macro_context_falls_back_local(tmp_path):
    from services.database import DatabaseService
    import json

    db = DatabaseService({"database": {"local_fallback_file": str(tmp_path / "trades.json")}})
    local = tmp_path.parent / "does_not_matter"
    # Directly verify local storage is optional and missing returns safe empty.
    assert db.get_macro_context() == {}
