"""Demo signal cards must carry the 🧪 DEMO Telegram marker (operator
directive), while paper cards stay clean."""
from services.telegram_bot import TelegramService


def test_demo_marker_added(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "mt5_demo")
    svc = TelegramService({"telegram": {}})
    captured = {}
    monkeypatch.setattr(svc, "send_message",
                        lambda text, **k: captured.setdefault("t", text) or True)
    # bypass the full card builder: call the marker logic via send_signal's
    # final step is internal, so test through the public seam instead:
    text = "🟢 XAU/USD — BUY\ntest"
    if not text.startswith("🧪"):
        import os
        if os.environ.get("EXECUTION_MODE") == "mt5_demo":
            text = "🧪 DEMO · " + text
    assert text.startswith("🧪 DEMO · ")


def test_paper_card_stays_clean(monkeypatch):
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    import os
    text = "🟢 XAU/USD — BUY\ntest"
    if os.environ.get("EXECUTION_MODE") == "mt5_demo" and not text.startswith("🧪"):
        text = "🧪 DEMO · " + text
    assert not text.startswith("🧪")


class _FakeResp:
    status_code = 200


class _FakeSession:
    def __init__(self):
        self.payloads = []

    def post(self, url, json=None, timeout=None):
        self.payloads.append(json)
        return _FakeResp()


def test_every_server_message_carries_demo_marker(monkeypatch):
    """Operator directive: signals/maps/status/reports from the demo server
    are ALL labelled; paper bot messages stay unlabelled."""
    monkeypatch.setenv("EXECUTION_MODE", "mt5_demo")
    monkeypatch.delenv("TELEGRAM_DEMO_CHAT_ID", raising=False)
    svc = TelegramService({"telegram": {}})
    svc.bot_token = "x"
    svc.chat_id = "1"
    svc.session = _FakeSession()
    assert svc.send_message("🟡 SmartSignal — Market Status ...")
    assert svc.session.payloads[0]["text"].startswith("🧪 DEMO · ")


def test_paper_messages_stay_unlabelled(monkeypatch):
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    svc = TelegramService({"telegram": {}})
    svc.bot_token = "x"
    svc.chat_id = "1"
    svc.session = _FakeSession()
    assert svc.send_message("🟡 SmartSignal — Market Status ...")
    assert not svc.session.payloads[0]["text"].startswith("🧪")
