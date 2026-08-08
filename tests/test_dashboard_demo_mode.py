"""The dashboard must label the stream it reads (demo vs paper).

Paper trading is stopped; the dashboard reads trades_demo. A dashboard that
says "Paper Trading" while showing demo rows is a lie, and a Telegram card
without the 🧪 DEMO marker would be misread as the old paper system.
These tests must FAIL if the demo labelling is removed or unwired.
"""
from services.dashboard import format_dashboard_telegram, render_dashboard, summarize_trades


def _sample():
    return [{
        "id": "t1", "symbol": "XAU/USD", "type": "BUY", "status": "TP1_HIT",
        "entry_price": 4300.0, "current_price": 4310.0, "stop_loss": 4290.0,
        "tp1": 4310.0, "tp2": 4330.0, "pnl_points": 100.0, "confidence": 70,
        "created_at": "2026-08-08T10:00:00Z", "closed_at": "2026-08-08T11:00:00Z",
        "close_reason": "TP1",
    }]


def test_demo_dashboard_says_demo_not_paper():
    html_text = render_dashboard(_sample(), demo=True)
    assert "DEMO" in html_text
    assert "MT5 Demo Trading" in html_text
    assert "Paper Trading" not in html_text


def test_paper_dashboard_still_says_paper():
    html_text = render_dashboard(_sample(), demo=False)
    assert "Paper Trading" in html_text
    assert "MT5 Demo Trading" not in html_text


def test_demo_telegram_card_is_marked():
    text = format_dashboard_telegram(summarize_trades(_sample()), demo=True)
    assert "DEMO" in text.splitlines()[0]


def test_paper_telegram_card_has_no_demo_marker():
    text = format_dashboard_telegram(summarize_trades(_sample()), demo=False)
    assert "DEMO" not in text


def test_default_is_paper_label():
    """Unlabelled call = old behavior = paper. No silent mode flip."""
    html_text = render_dashboard(_sample())
    assert "Paper Trading" in html_text
