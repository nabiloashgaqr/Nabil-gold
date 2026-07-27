"""Guard the public dashboard API against leaking the decision model.

The trades table stores signal_snapshot as the entire decision object: the
session plan with its POI zones and scores, target liquidity, invalidation,
manual-plan labels, supporting/opposing agents, execution readiness and day
archetype. The dashboard endpoint is unauthenticated, so spreading a raw row
published the strategy itself, not merely trade data.

These tests run the real JavaScript through node so the guarantee is checked
against shipped code rather than a reimplementation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_FILES = [ROOT / "api" / "dashboard.js", ROOT / "dashboard" / "api" / "dashboard.js"]

RAW_ROW = {
    "id": "TRADE_LEAK_TEST",
    "symbol": "XAU/USD",
    "type": "BUY",
    "status": "PENDING",
    "entry_price": 4100.28,
    "stop_loss": 4060.28,
    "tp1": 4150.28,
    "tp2": 4190.28,
    "confidence": 79,
    "planned_rr": 2.25,
    "created_at": "2026-07-27T12:41:28Z",
    "signal_snapshot": {
        "session_plan": {
            "plan_id": "PLAN::CONFIDENTIAL",
            "planner_confidence": 67.7,
            "primary_poi": {
                "poi_zone": {"top": 4101.44, "bottom": 4099.13},
                "thesis_dominance_score": 64.0,
                "return_probability_score": 42.9,
                "quality_score": 92.0,
            },
            "standby_poi": {"entry_price": 4055.92},
            "target_liquidity": 4104.25,
            "manual_plan": {"execution_priority_label": "Continuation priority"},
            "plan_narrative": "CONFIDENTIAL NARRATIVE",
            "supporting_agents": ["technical", "smc"],
            "execution_readiness": {"state": "PENDING_EXECUTION_READY"},
            "day_archetype": "CONTINUATION_AFTER_SWEEP_DAY",
            "preferred_execution_family": "MITIGATION_LADDER",
        },
        "setup_context": {"poi_type": "equilibrium"},
        "planner_execution_gate": {"support_agents": ["technical"]},
        "agent_details": {"technical": {"confidence": 92}},
        "session_info": {"current_session": "London + New York Afternoon"},
        "news_context": {"rule_based": {"market_status": "CAUTION"}},
        "market_context": {"technical_regime": {"volatility_regime": "NORMAL", "market_phase": "TRENDING"}},
        "signal": {"rr_ratio": 2.25},
    },
}

FORBIDDEN = [
    "signal_snapshot",
    "session_plan",
    "manual_plan",
    "primary_poi",
    "standby_poi",
    "thesis_dominance_score",
    "return_probability_score",
    "plan_narrative",
    "planner_execution_gate",
    "setup_context",
    "agent_details",
    "target_liquidity",
    "preferred_execution_family",
    "execution_readiness",
    "CONFIDENTIAL",
]


def _run_normalize(api_file: Path) -> dict:
    script = f"""
const src = require('fs').readFileSync({json.dumps(str(api_file))}, 'utf8');
const mod = {{ exports: {{}} }};
new Function('module','exports','require','process',
  src + '\\nmodule.exports._t = {{ normalizeTrade }};'
)(mod, mod.exports, require, {{ env: {{ SUPABASE_URL: 'x', SUPABASE_KEY: 'y' }} }});
const row = {json.dumps(RAW_ROW)};
process.stdout.write(JSON.stringify(mod.exports._t.normalizeTrade(row)));
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required to execute the API module")
@pytest.mark.parametrize("api_file", API_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_public_trade_payload_excludes_the_decision_model(api_file: Path) -> None:
    payload = json.dumps(_run_normalize(api_file))
    leaked = [token for token in FORBIDDEN if token in payload]
    assert not leaked, f"{api_file.relative_to(ROOT)} leaks: {leaked}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required to execute the API module")
@pytest.mark.parametrize("api_file", API_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_public_trade_payload_keeps_fields_the_dashboard_renders(api_file: Path) -> None:
    out = _run_normalize(api_file)
    # Trade basics.
    assert out["id"] == "TRADE_LEAK_TEST"
    assert out["symbol"] == "XAU/USD"
    assert out["status"] == "PENDING"
    assert out["entry_price"] == 4100.28
    assert out["planned_rr"] == 2.25
    # Values the UI previously dug out of the snapshot, now flattened safely.
    assert out["session_label"] == "London + New York Afternoon"
    assert out["news_status_at_entry"] == "CAUTION"
    assert out["volatility_regime"] == "NORMAL"
    assert out["market_phase"] == "TRENDING"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required to execute the API module")
@pytest.mark.parametrize("api_file", API_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_new_database_columns_are_not_exposed_by_default(api_file: Path) -> None:
    """The allowlist must be opt-in, so a future column cannot leak silently."""
    script = f"""
const src = require('fs').readFileSync({json.dumps(str(api_file))}, 'utf8');
const mod = {{ exports: {{}} }};
new Function('module','exports','require','process',
  src + '\\nmodule.exports._t = {{ normalizeTrade }};'
)(mod, mod.exports, require, {{ env: {{ SUPABASE_URL: 'x', SUPABASE_KEY: 'y' }} }});
const row = {json.dumps({**RAW_ROW, "internal_future_column": "should-not-ship"})};
process.stdout.write(JSON.stringify(mod.exports._t.normalizeTrade(row)));
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "internal_future_column" not in result.stdout
    assert "should-not-ship" not in result.stdout
