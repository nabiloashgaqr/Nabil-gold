#!/usr/bin/env python3
"""Backfill regime_composite and market_phase for existing trades.

For trades that have volatility_regime=NULL or regime_composite=NULL,
this script reads the signal_snapshot.market_context.technical_regime
and fills in:
  - volatility_regime
  - market_phase
  - regime_composite (e.g. "NORMAL TRENDING", "HIGH RANGING", "SQUEEZE")

Usage:
    python scripts/backfill_regime_composite.py          # dry-run (preview only)
    python scripts/backfill_regime_composite.py --apply   # actually update DB
"""
import argparse
import json
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.database import DatabaseService


def build_regime_composite(tech_regime: dict) -> str:
    """Build a composite regime label from volatility_regime + market_phase."""
    return DatabaseService._build_regime_composite(tech_regime)


def extract_regime_from_snapshot(trade: dict) -> dict:
    """Extract regime info from signal_snapshot for a trade."""
    snap = trade.get("signal_snapshot") or {}
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except Exception:
            snap = {}

    mc = snap.get("market_context") or {}
    tech_regime = mc.get("technical_regime") or {}
    if not isinstance(tech_regime, dict):
        tech_regime = {}

    return {
        "volatility_regime": tech_regime.get("volatility_regime") or trade.get("volatility_regime"),
        "market_phase": tech_regime.get("market_phase") or trade.get("market_phase"),
    }


def main():
    parser = argparse.ArgumentParser(description="Backfill regime_composite for existing trades")
    parser.add_argument("--apply", action="store_true", help="Actually update the database (default: dry-run)")
    args = parser.parse_args()

    db = Database()

    # Load all trades (both local and Supabase)
    if db.use_supabase and db.client:
        print("Connected to Supabase")
        try:
            result = db.client.table("trades").select("*").execute()
            trades = result.data or []
        except Exception as e:
            print(f"Error loading trades from Supabase: {e}")
            return
    else:
        from utils.helpers import load_trades
        trades = load_trades(db.local_path)

    print(f"Total trades: {len(trades)}")

    needs_update = []
    for trade in trades:
        tid = trade.get("id", "?")
        vol = trade.get("volatility_regime")
        phase = trade.get("market_phase")
        composite = trade.get("regime_composite")

        # Already has composite → skip
        if composite and str(composite).strip().upper() not in ("UNKNOWN", "NONE", ""):
            continue

        # Try to extract from snapshot
        regime_info = extract_regime_from_snapshot(trade)
        new_vol = regime_info["volatility_regime"]
        new_phase = regime_info["market_phase"]
        new_composite = build_regime_composite({
            "volatility_regime": new_vol,
            "market_phase": new_phase,
        })

        if new_composite in ("UNKNOWN", "LEGACY"):
            # Still can't determine → skip
            continue

        needs_update.append({
            "id": tid,
            "volatility_regime": new_vol,
            "market_phase": new_phase,
            "regime_composite": new_composite,
            "old_vol": vol,
            "old_phase": phase,
        })

    print(f"Trades needing update: {len(needs_update)}")

    if not needs_update:
        print("Nothing to update ✅")
        return

    # Show preview
    print("\n── Preview ──")
    for u in needs_update[:20]:
        print(f"  {u['id'][:8]}…  vol: {u['old_vol']} → {u['volatility_regime']}  phase: {u['old_phase']} → {u['market_phase']}  composite: {u['regime_composite']}")
    if len(needs_update) > 20:
        print(f"  … and {len(needs_update) - 20} more")

    if not args.apply:
        print("\n⚠️  DRY RUN — no changes made. Use --apply to update.")
        return

    # Apply updates
    updated = 0
    failed = 0
    for u in needs_update:
        try:
            if db.use_supabase and db.client:
                db.client.table("trades").update({
                    "volatility_regime": u["volatility_regime"],
                    "market_phase": u["market_phase"],
                    "regime_composite": u["regime_composite"],
                }).eq("id", u["id"]).execute()
            updated += 1
        except Exception as e:
            print(f"  ❌ Failed to update {u['id']}: {e}")
            failed += 1

    print(f"\n✅ Updated: {updated}  ❌ Failed: {failed}")


if __name__ == "__main__":
    main()
