"""VPS-local dashboard API: no Supabase/GitHub runtime dependency."""
from __future__ import annotations

import json
from pathlib import Path

from services.dashboard_local_api import build_dashboard_payload


def _row(tid, status, pnl=0, **extra):
    row = {
        "id": tid, "symbol": "XAU/USD", "type": "BUY", "status": status,
        "entry_price": 4300.0, "stop_loss": 4260.0, "tp1": 4332.0,
        "tp2": 4364.0, "created_at": "2026-08-11T08:00:00+00:00",
        "closed_at": "2026-08-11T09:00:00+00:00" if status not in {"OPEN", "PENDING"} else None,
        "pnl_points": pnl,
        "signal_snapshot": {
            "secret_strategy": "MUST_NOT_LEAVE_VPS",
            "session_plan": {"primary_poi": {"entry": 9999}},
            "agent_details": {
                "technical": {"direction": "BUY", "confidence": 80},
            },
        },
        "reasons": ["private reasoning"],
    }
    row.update(extra)
    return row


def test_local_dashboard_reads_local_book_and_sanitizes(tmp_path) -> None:
    (tmp_path / "storage").mkdir()
    rows = [
        _row("C1", "TP2_HIT", 120),
        _row("C2", "SL_HIT", -40),
        _row("O1", "OPEN", 15),
        _row("P1", "PENDING"),
    ]
    (tmp_path / "storage" / "trades.json").write_text(
        json.dumps(rows), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({
        "agent_weights": {"technical": 0.2, "classical": 0.25,
                          "smc": 0.2, "price_action": 0.2,
                          "multitimeframe": 0.15}
    }), encoding="utf-8")

    payload = build_dashboard_payload(tmp_path)
    assert payload["ok"] is True
    assert payload["source"] == "vps-local-json"
    assert len(payload["closedTrades"]) == 2
    assert len(payload["liveTrades"]) == 1
    assert len(payload["pendingOrders"]) == 1
    assert payload["summary"]["netPoints"] == 80
    assert payload["dailyReports"]
    public = payload["closedTrades"][0]
    assert "signal_snapshot" not in public
    assert "reasons" not in public
    assert "secret_strategy" not in json.dumps(payload)


def test_dashboard_frontend_has_no_hosting_or_cdn_runtime_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    index = (root / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "cdn.jsdelivr.net" not in index
    assert "/_vercel/" not in index
    assert 'src="assets/chart.umd.min.js"' in index
    assert (root / "dashboard" / "assets" / "chart.umd.min.js").stat().st_size > 100_000


def test_public_server_is_get_only_and_sanitized() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts" / "run_dashboard_server.py").read_text(encoding="utf-8")
    assert "def do_GET" in source
    assert "def do_POST" not in source
    assert "DASHBOARD_PUBLIC_READONLY" in source
    assert "build_dashboard_payload" in source


def test_vps_dashboard_task_is_registered_in_setup() -> None:
    root = Path(__file__).resolve().parents[1]
    setup = (root / "deploy" / "vps_setup.ps1").read_text(encoding="utf-8")
    installer = (root / "deploy" / "setup_local_dashboard.ps1").read_text(encoding="utf-8")
    wrapper = (root / "deploy" / "tasks" / "dashboard_api.bat").read_text(encoding="utf-8")
    assert "SS_DashboardAPI" in setup
    assert "dashboard_api.bat" in setup
    assert "run_dashboard_server.py" in wrapper
    assert "New-NetFirewallRule" in installer
    assert "DASHBOARD_PUBLIC_READONLY=true" in installer
