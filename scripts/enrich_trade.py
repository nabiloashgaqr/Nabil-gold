"""One script to manage every trade — 8 actions, full price context.

Usage:
    TRADE_ID=... ACTION=trailing_sl_hit CURRENT_PRICE=4110 LOW_PRICE=4092.3 python scripts/enrich_trade.py

Actions:
    reopen         → clear close fields, set OPEN
    update_prices  → update MFE/MAE/PnL from HIGH_PRICE/LOW_PRICE/CURRENT_PRICE
    be_hit         → breakeven at entry (SL moved, price retraced)
    sl_hit         → full stop-loss (loss)
    trailing_sl_hit → trailing stop hit (profit locked)
    tp1_hit        → first target (partial close, SL→entry)
    tp2_hit        → second target (full win)
    delete         → remove trade permanently
"""

from __future__ import annotations
import os, sys, logging
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.database import DatabaseService
from utils.helpers import load_config, calculate_pips

_VALID = {"reopen", "update_prices", "be_hit", "sl_hit", "trailing_sl_hit",
          "tp1_hit", "tp2_hit", "delete"}

_CLEARABLE = {"TP2_HIT", "TP1_HIT", "SL_HIT", "TRAILING_SL_HIT", "BE_HIT",
              "EXPIRED", "MANUAL_CLOSE", "MOVE_SL_TO_BE", "TRAILING_SL_UPDATED",
              "EXIT_WARNING", "NEAR_TP1", "LONG_RUNNING", "PRICE_SANITY_FAILED",
              "PARTIAL_CLOSE", "ORDER_FILLED"}


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _opt_float(key: str) -> float | None:
    raw = _env(key)
    if not raw: return None
    try: return float(raw)
    except ValueError:
        print(f"❌ Invalid float for {key}: {raw}")
        raise SystemExit(1)


def _find(db: DatabaseService, tid: str) -> dict | None:
    for t in db.get_recent_trades(limit=300):
        if str(t.get("id")) == tid: return t
    for t in db.get_open_trades():
        if str(t.get("id")) == tid: return t
    return None


def _clean_us(trade: dict) -> list:
    old = trade.get("updates_sent") or []
    return [e for e in old if e not in _CLEARABLE]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# Columns that definitely exist in the legacy Supabase schema
_SAFE_COLS = {"status", "result", "close_price", "final_pnl", "final_pnl_points",
              "closed_at", "close_time", "stop_loss", "sl_moved_to_entry",
              "partial_close", "current_price", "current_pnl",
              "current_pnl_points", "last_updated"}


def _write(db: DatabaseService, tid: str, updates: dict) -> bool:
    """Write updates to Supabase with smart column-dropping fallbacks."""
    if not db.use_supabase or not db.client:
        print("❌ Supabase not configured")
        return False

    # Build payloads in order: full → without MFE/MAE → legacy
    full = dict(updates)
    no_mfe_mae = {k: v for k, v in updates.items()
                  if k not in ("max_favorable_excursion", "max_adverse_excursion")}
    legacy = {k: v for k, v in updates.items() if k in _SAFE_COLS}

    for label, payload in [("full", full), ("no-mfe-mae", no_mfe_mae), ("legacy", legacy)]:
        if label == "full" and payload == no_mfe_mae:
            continue  # identical, skip
        try:
            db.client.table("trades").update(payload).eq("id", tid).execute()
            if label != "full":
                print(f"   ⚠️  Used {label} fallback — some columns missing in Supabase")
            return True
        except Exception as exc:
            err = str(exc)[:120]
            if label == "legacy":
                print(f"❌ Supabase update failed even with legacy payload: {err}")
                return False
            print(f"   ⚠️  {label} payload failed ({err}) — trying next fallback...")
    return False


# ── PnL / MFE / MAE helpers ────────────────────────────────────────

def _pnl(entry: float, price: float, side: str, symbol: str) -> float:
    if entry <= 0 or price <= 0: return 0
    return round(calculate_pips(entry, price, side, symbol), 1)


def _mfe(entry: float, high: float | None, low: float | None,
         side: str, symbol: str) -> float | None:
    """Best-case excursion — the most favorable price seen."""
    best = None
    for p in (high, low):
        if p is not None and p > 0:
            exc = _pnl(entry, p, side, symbol)
            if best is None or exc > best:
                best = exc
    return best


def _mae(entry: float, high: float | None, low: float | None,
         side: str, symbol: str) -> float | None:
    """Worst-case adverse excursion — only return if negative (drawdown)."""
    worst = None
    for p in (high, low):
        if p is not None and p > 0:
            exc = _pnl(entry, p, side, symbol)
            if worst is None or exc < worst:
                worst = exc
    return worst if (worst is not None and worst < 0) else None


# ── Main ────────────────────────────────────────────────────────────

def main() -> int:
    cfg = load_config()
    db = DatabaseService(cfg)

    tid = _env("TRADE_ID")
    action = _env("ACTION", "reopen").lower()
    cur = _opt_float("CURRENT_PRICE")
    hi = _opt_float("HIGH_PRICE")
    lo = _opt_float("LOW_PRICE")
    manual_pnl = _opt_float("PNL_POINTS")

    if not tid: print("❌ TRADE_ID required"); return 1
    if action not in _VALID:
        print(f"❌ Invalid ACTION. Valid: {', '.join(sorted(_VALID))}"); return 1

    trade = _find(db, tid)
    entry = float(trade.get("entry_price", 0)) if trade else 0
    side = str(trade.get("type") or trade.get("side") or "BUY").upper() if trade else "BUY"
    symbol = str(trade.get("symbol") or "XAU/USD") if trade else "XAU/USD"
    old_status = str(trade.get("status", "?")) if trade else "?"
    tp1 = float(trade.get("tp1", 0)) if trade else 0
    tp2 = float(trade.get("tp2", 0)) if trade else 0
    sl = float(trade.get("stop_loss", 0)) if trade else 0
    old_mfe = float(trade.get("max_favorable_excursion", 0) or 0) if trade else 0
    old_mae = float(trade.get("max_adverse_excursion", 0) or 0) if trade else 0

    # Pre-compute excursions
    mfe_val = _mfe(entry, hi, lo, side, symbol)
    mae_val = _mae(entry, hi, lo, side, symbol)
    cur_pnl = _pnl(entry, cur, side, symbol) if cur else None

    print(f"🔍 {tid}  |  {side} @ {entry}  |  Status: {old_status}")
    print(f"   Action: {action}")
    if cur:  print(f"   Current Price: {cur} → PnL: {cur_pnl:+.1f} pts" if cur_pnl else f"   Current Price: {cur}")
    if hi:   print(f"   High: {hi}")
    if lo:   print(f"   Low: {lo}")
    if mfe_val is not None: print(f"   Calc MFE: {mfe_val:+.1f} pts  |  Old MFE: {old_mfe:+.1f}")
    if mae_val is not None: print(f"   Calc MAE: {mae_val:+.1f} pts  |  Old MAE: {old_mae:+.1f}")

    # ── Delete ──
    if action == "delete":
        if not trade: print("⚠️  Not found"); return 0
        if db.use_supabase and db.client:
            db.client.table("trades").delete().eq("id", tid).execute()
            print(f"✅ {tid} DELETED")
        return 0

    if not trade: print(f"❌ Trade {tid} not found!"); return 1

    updates: dict = {"last_updated": _now()}
    now = _now()

    # Helper: merge MFE/MAE
    def _merge_mfe_mae(u: dict) -> None:
        fm = old_mfe
        if mfe_val is not None: fm = max(old_mfe, mfe_val)
        u["max_favorable_excursion"] = round(fm, 1)
        if mae_val is not None:
            u["max_adverse_excursion"] = round(mae_val, 1)

    # ── update_prices ──
    if action == "update_prices":
        if cur is not None:
            updates["current_price"] = round(cur, 2)
            updates["current_pnl"] = cur_pnl
            updates["current_pnl_points"] = cur_pnl
        _merge_mfe_mae(updates)
        ok = _write(db, tid, updates)
        if ok: print(f"✅ {tid} prices updated  |  MFE: {updates.get('max_favorable_excursion', '?')}")
        return 0 if ok else 1

    # ── reopen ──
    if action == "reopen":
        updates.update(status="OPEN", result=None, closed_at=None, close_time=None,
                       close_price=None, final_pnl=None, final_pnl_points=None)
        updates["updates_sent"] = _clean_us(trade)
        if cur is not None:
            updates["current_price"] = round(cur, 2)
            updates["current_pnl"] = cur_pnl
            updates["current_pnl_points"] = cur_pnl
        _merge_mfe_mae(updates)
        ok = _write(db, tid, updates)
        if ok: print(f"✅ {tid} → OPEN")
        return 0 if ok else 1

    # ── be_hit ──
    if action == "be_hit":
        final_mfe = max(old_mfe, mfe_val) if mfe_val is not None else max(old_mfe, 150.0)
        updates.update(status="BE_HIT", result="BREAKEVEN", sl_moved_to_entry=True,
                       stop_loss=round(entry, 2), close_price=round(entry, 2),
                       final_pnl=0.0, final_pnl_points=0.0, closed_at=now, close_time=now,
                       max_favorable_excursion=round(final_mfe, 1))
        if mae_val is not None: updates["max_adverse_excursion"] = round(mae_val, 1)
        updates["updates_sent"] = [e for e in _clean_us(trade) if e != "BE_HIT"] + ["MOVE_SL_TO_BE", "BE_HIT"]

    # ── sl_hit ──
    elif action == "sl_hit":
        cp = cur or sl
        pnl = manual_pnl if manual_pnl is not None else _pnl(entry, cp, side, symbol)
        final_mfe = max(old_mfe, mfe_val) if mfe_val is not None else old_mfe
        updates.update(status="SL_HIT", result="LOSS", close_price=round(cp, 2),
                       final_pnl=round(pnl, 1), final_pnl_points=round(pnl, 1),
                       closed_at=now, close_time=now,
                       max_favorable_excursion=round(final_mfe, 1))
        updates["updates_sent"] = [e for e in _clean_us(trade) if e != "SL_HIT"] + ["SL_HIT"]

    # ── trailing_sl_hit ──
    elif action == "trailing_sl_hit":
        # Calculate close_price from the BEST price + trailing distance.
        # SELL: close = LOW + distance, BUY: close = HIGH - distance
        tm = cfg.get("trade_management", {}) or {}
        ts = cfg.get("trailing_stop", {}) or {}
        trail_pts = float(tm.get("trailing_distance_points",
                          ts.get("trailing_distance", 150)) or 150)
        trail_price = trail_pts / 10.0  # 150 pts = 15 USD

        best_price = None
        if side == "SELL" and lo is not None:
            best_price = lo
        elif side == "BUY" and hi is not None:
            best_price = hi

        if best_price is not None:
            cp = best_price + trail_price if side == "SELL" else best_price - trail_price
            print(f"   Trailing calc: best={'low' if side=='SELL' else 'high'}={best_price} + {trail_price} = cp={cp:.2f}")
        else:
            cp = cur or entry
            print(f"   No best price provided — using current: {cp:.2f}")

        pnl = manual_pnl if manual_pnl is not None else _pnl(entry, cp, side, symbol)
        final_mfe = max(old_mfe, mfe_val) if mfe_val is not None else max(old_mfe, pnl)
        updates.update(status="SL_HIT", result="WIN" if pnl > 0 else "BREAKEVEN",
                       sl_moved_to_entry=True, stop_loss=round(cp, 2),
                       close_price=round(cp, 2), final_pnl=round(pnl, 1),
                       final_pnl_points=round(pnl, 1), closed_at=now, close_time=now,
                       max_favorable_excursion=round(final_mfe, 1))
        if mae_val is not None: updates["max_adverse_excursion"] = round(mae_val, 1)
        updates["updates_sent"] = [e for e in _clean_us(trade)
                                   if e not in {"TRAILING_SL_HIT", "SL_HIT"}] + ["TRAILING_SL_HIT"]

    # ── tp1_hit ──
    elif action == "tp1_hit":
        cp = cur or tp1
        pnl = manual_pnl if manual_pnl is not None else _pnl(entry, cp, side, symbol) * 0.5
        final_mfe = max(old_mfe, _pnl(entry, cp, side, symbol))
        updates.update(status="TP1_HIT", result=None, partial_close=True,
                       sl_moved_to_entry=True, stop_loss=round(entry, 2),
                       close_price=round(cp, 2), final_pnl=round(pnl, 1),
                       final_pnl_points=round(pnl, 1),
                       max_favorable_excursion=round(final_mfe, 1))
        updates["updates_sent"] = [e for e in _clean_us(trade)
                                   if e not in {"TP1_HIT", "MOVE_SL_TO_BE"}] + ["TP1_HIT", "MOVE_SL_TO_BE"]

    # ── tp2_hit ──
    elif action == "tp2_hit":
        cp = cur or tp2
        pnl = manual_pnl if manual_pnl is not None else _pnl(entry, cp, side, symbol)
        final_mfe = max(old_mfe, pnl)
        updates.update(status="TP2_HIT", result="WIN", close_price=round(cp, 2),
                       final_pnl=round(pnl, 1), final_pnl_points=round(pnl, 1),
                       closed_at=now, close_time=now,
                       max_favorable_excursion=round(final_mfe, 1))
        updates["updates_sent"] = [e for e in _clean_us(trade) if e != "TP2_HIT"] + ["TP2_HIT"]

    ok = _write(db, tid, updates)
    if ok:
        print(f"✅ {tid} → {updates.get('status')}  |  PnL: {updates.get('final_pnl', '?')}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
