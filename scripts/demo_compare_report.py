"""Phase 3 (no-VPS shadow): daily paper-vs-demo book comparison.

The demo branch runs the SAME rules into trades_demo; the two books must be
identical. Any divergence = code drift between branches = alert. Real
slippage measurement begins when the VPS (mt5_demo) arrives; demo_metrics
already carries its columns.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple


def compare_books(paper_rows: List[Dict[str, Any]],
                  demo_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    p_by_id = {str(r.get("id")): r for r in paper_rows}
    d_by_id = {str(r.get("id")): r for r in demo_rows}
    only_paper = sorted(set(p_by_id) - set(d_by_id))
    only_demo = sorted(set(d_by_id) - set(p_by_id))
    field_diffs: List[str] = []
    for tid in sorted(set(p_by_id) & set(d_by_id)):
        p, d = p_by_id[tid], d_by_id[tid]
        for field in ("status", "stop_loss", "tp1", "tp2", "entry_price",
                      "closed_at", "final_pnl_points"):
            pv, dv = p.get(field), d.get(field)
            if str(pv) != str(dv):
                field_diffs.append(f"{tid[-8:]}:{field} paper={pv} demo={dv}")
    return {
        "paper_count": len(p_by_id),
        "demo_count": len(d_by_id),
        "only_paper": only_paper,
        "only_demo": only_demo,
        "field_diffs": field_diffs,
        "identical": not (only_paper or only_demo or field_diffs),
    }


def report_text(cmp: Dict[str, Any], day: str) -> str:
    if cmp["identical"]:
        return (f"🧪 DEMO shadow {day}: books identical "
                f"({cmp['paper_count']} trades) ✅")
    lines = [f"🧪 DEMO shadow {day}: DIVERGENCE ⚠️",
             f"paper={cmp['paper_count']} demo={cmp['demo_count']}"]
    if cmp["only_paper"]:
        lines.append("only in paper: " + ", ".join(t[-8:] for t in cmp["only_paper"]))
    if cmp["only_demo"]:
        lines.append("only in demo: " + ", ".join(t[-8:] for t in cmp["only_demo"]))
    lines.extend(cmp["field_diffs"][:10])
    return "\n".join(lines)


def run_compare(day: str | None = None) -> Tuple[Dict[str, Any], str]:
    """Read both books (paper + trades_demo) and build the report."""
    from services.database import DatabaseService
    from services.telegram_bot import TelegramService
    from utils.helpers import load_config

    cfg = load_config()
    paper = DatabaseService(cfg)
    paper.trades_table = "trades"
    demo = DatabaseService(cfg)
    demo.trades_table = "trades_demo"
    day = day or __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).strftime("%Y-%m-%d")
    p_rows = paper.get_recent_trades(limit=200) or []
    d_rows = demo.get_recent_trades(limit=200) or []
    cmp = compare_books(p_rows, d_rows)
    text = report_text(cmp, day)
    if os.environ.get("TELEGRAM_DEMO_CHAT_ID"):
        TelegramService(cfg).send_message(text)
    return cmp, text


if __name__ == "__main__":
    run_compare()
