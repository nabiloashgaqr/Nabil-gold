# MT5 Demo-Execution Migration — Technical Implementation Plan (Agent Handoff)

Repo: `nabiloashgaqr/Nabil-gold` (public). Branch strategy: implement EVERYTHING
below on a NEW branch `demo/mt5` cut from `main`. `main` stays the paper-trading
truth and must NEVER receive execution code except by a reviewed Pull Request
at the very end (Phase 5). Do not merge to main during this plan.

Operator timezone: Asia/Jerusalem (UTC+3). All logs/timestamps in UTC.

---

## 0. Rules of engagement for the implementing agent

1. Read this document fully before touching code. Every file path below is
   relative to repo root.
2. Do not change any behaviour on `main`. All changes live on `demo/mt5`.
3. The codebase has a SINGLE SOURCE OF TRUTH for risk maths:
   `utils/trading_rules.py` (`stop_rule`, `stop_from_liquidity_points`,
   `target_ratios`, `targets_law`, `trailing_params`, `post_tp2_rule`).
   The executor MUST call these functions; re-implementing any formula is a
   plan violation.
4. Points convention: the codebase uses 10 points = 1.00 USD on XAU/USD
   (`utils/instruments.py::price_to_points` / `points_to_price`). MT5's
   `symbol_info().point` is 0.01 (100 points per USD). NEVER mix the two;
   always convert through price, e.g. `price_delta = points_to_price(pts, "XAU/USD")`.
5. Paper mode must remain the default everywhere (`execution_mode=paper`).
6. Run `python -m pytest -q` (full suite, currently 1641 passed + 3 skipped)
   and the CI barrier file list from `.github/workflows/analyze.yml` after
   every commit; both must stay green.

---

## 1. Current architecture (what exists on main today)

- Entry points: `scripts/run_analysis.py::run_analysis_async()` (signal
  generation, 5-min cadence via external cron) and
  `scripts/run_trade_updates.py::main()` (open-trade management loop).
- Prices: `services/market_data.py::MarketDataService.get_gold_data()`
  returns `{"data": [ {time,open,high,low,close}, ... ], "source": ...,
  "source_integrity": {...}}` from TwelveData.
- Storage: Supabase PostgREST via `services/database.py::DatabaseService`,
  table literal `trades` (lines 786, 853, 942, 1056, ...).
- Cards: `services/telegram_bot.py::TelegramService.send_signal()` (line 852),
  `send_error_alert()` (1527).
- Management engine: `agents/open_trades_manager.py::OpenTradesManager
  .evaluate_trade(...)` produces `updates` (status/stop_loss/partial flags)
  per trade per cycle.
- Risk laws: `utils/trading_rules.py` (see §0.3).
- Config: `config.json`; secrets via env (`SUPABASE_URL`, `SUPABASE_KEY`,
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TWELVEDATA_API_KEY`,
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`).

---

## 2. Goals / non-goals

Goals: (a) branch `demo/mt5` with an MT5 price feed + demo-account executor;
(b) strict isolation: demo trades live in table `trades_demo`, demo cards in a
separate Telegram chat; (c) reconciliation every cycle with hard halt on
mismatch; (d) two-week shadow metrics; (e) PR-ready code with tests.
Non-goals: live/real-money execution (separate future decision); changes to
paper logic; new risk maths.

---

## 3. Repo operations (exact commands)

```bash
git fetch origin
git checkout -b demo/mt5 origin/main
# ... implement ...
git commit -m "demo/mt5: <component>"
git push -u origin demo/mt5
```

Branch protection: never force-push; PR to main only at Phase 5 with
`execution_mode` default still `paper`.

---

## 4. Config & environment contract

Add to `config.json` (branch only):

```json
"execution": {
  "execution_mode": "paper",          // "paper" | "mt5_demo"
  "trades_table_override_env": "TRADES_TABLE",
  "demo": {
    "symbol_map": {"XAU/USD": "XAUUSD"},
    "lot_size": 0.10,
    "max_open_demo_trades": 3,
    "max_new_orders_per_day": 6,
    "deviation_points_mt5": 30,       // MT5 slippage deviation (MT5 points)
    "reconcile_halt_on_mismatch": true
  }
}
```

`.env` on the VPS (never committed):

```
EXECUTION_MODE=mt5_demo
DATA_SOURCE=mt5
TRADES_TABLE=trades_demo
TELEGRAM_DEMO_CHAT_ID=<demo chat id>
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_LOGIN=<demo login>
MT5_PASSWORD=<demo password>
MT5_SERVER=<demo server name>
# plus all existing keys from §1
```

Rule: `DatabaseService` reads table name from env `TRADES_TABLE`
(default `trades`). In `services/database.py`, replace EVERY literal
`table("trades")` with `table(self.trades_table)` where
`self.trades_table = os.environ.get("TRADES_TABLE") or "trades"` set in
`__init__`. Grep-verify zero remaining `table("trades")` literals afterwards.

SQL (run once in Supabase SQL editor):

```sql
CREATE TABLE IF NOT EXISTS trades_demo (LIKE trades INCLUDING ALL);
ALTER TABLE trades_demo ADD COLUMN IF NOT EXISTS mt5_ticket BIGINT;
ALTER TABLE trades_demo ADD COLUMN IF NOT EXISTS mt5_account  BIGINT;
ALTER TABLE trades_demo ADD COLUMN IF NOT EXISTS execution_mode TEXT DEFAULT 'mt5_demo';
CREATE TABLE IF NOT EXISTS demo_metrics (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT NOW(),
  trade_id TEXT, event TEXT, slippage_points NUMERIC(18,6),
  latency_ms INTEGER, mismatch TEXT
);
```

---

## 5. Component A — `services/mt5_feed.py`

Public API (must match `MarketDataService` payload contract EXACTLY):

```python
def get_candles(symbol: str, timeframe_min: int, count: int) -> dict | None:
    # returns {"data": [...], "source": "mt5", "source_integrity": {...}}
```

Implementation requirements:
- `import MetaTrader5 as mt5`; `mt5.initialize(path=os.environ["MT5_PATH"])`,
  `mt5.login(login, password, server)` with 3 retries / exponential backoff;
  on permanent failure return None so the caller falls back to TwelveData.
- Timezone: MT5 `copy_rates_from` times are SERVER time. Compute offset once
  per session: `off = mt5.time() - int(time.time())` snapped to nearest 900s;
  store `utc = rate.time - off` in every candle dict. Unit-test the snapping
  with offsets 0, 7200, 10800.
- Timeframe map: `mt5.TIMEFRAME_M5/M15/H1`; use `copy_rates_from(sym, tf, 1, count)`.
- Symbol map from `config["execution"]["demo"]["symbol_map"]`.
- Integrity: `source="mt5"`, `source_type="historical_ohlc"`, grade HIGH when
  `len(data)==count` and last candle age < 2*timeframe.
- Wiring in `services/market_data.py::get_gold_data` (line ~165): at top,
  `if (cfg data_source == "mt5"): payload = mt5_feed.get_candles(...)`; on
  None fall through to TwelveData; set `source` accordingly. Keep the
  `enrich_payload_integrity` path untouched.

---

## 6. Component B — `services/mt5_executor.py`

Class `Mt5DemoExecutor` with methods (all return typed results, all log):

- `connect() / shutdown()`; `alive()` heartbeat.
- `ensure_ticket(trade_id, side, entry_price, sl, tp, order_kind)`:
  idempotent by magic number `magic = 1000000 + (zlib.crc32(trade_id.encode()) % 8999999)`;
  if a position with that magic exists return its ticket; if `order_kind`
  LIMIT send `TRADE_ACTION_PENDING`; else market deal.
  Market request skeleton:
  ```python
  {"action": mt5.TRADE_ACTION_DEAL, "symbol": mt5_sym, "volume": lot,
   "type": mt5.ORDER_TYPE_SELL if side=="SELL" else mt5.ORDER_TYPE_BUY,
   "price": tick.bid/ask, "deviation": dev, "magic": magic,
   "type_filling": mt5.ORDER_FILLING_IOC, "comment": "SS-demo"}
  ```
  Then `trade_position_modify` for SL/TP.
- `apply_stop(trade_id, new_sl, tp2)`: `TRADE_ACTION_SL_TP` on the position.
- `partial_close_at_tp1(trade_id, fraction=0.5)`: opposite-side
  `TRADE_ACTION_DEAL` with `position=ticket`, `volume=round(lot*fraction,2)`.
  If broker rejects partial (retcode != TRADE_RETCODE_DONE) log metric
  `partial_unsupported` and fall back to full-close + reopen remainder at
  market with same SL/TP; record metric.
- `reconcile(open_db_rows) -> list[mismatch]`: compare sets of magics and,
  per ticket, |sl_diff| <= 0.05 USD and side/volume equality. Any mismatch:
  telegram alert via `send_error_alert` prefixed `🧪 DEMO HALT`, write
  `demo_metrics` row, set halt flag (module-level + env file
  `.demo_halt`), refuse new orders until operator clears.
- All prices converted via `points_to_price`/raw USD; volume fixed
  `lot_size`; refuse new orders when `max_new_orders_per_day` exceeded
  (count from `demo_metrics`).

## 7. Wiring the executor into the cycle

In `scripts/run_trade_updates.py::main()` AFTER
`OpenTradesManager.evaluate_trade(...)` results are applied to DB:

```python
if execution_mode == "mt5_demo":
    ex = Mt5DemoExecutor(...)
    for trade, result in evaluated:
        if result has TP1 event and ticket: ex.partial_close_at_tp1(...)
        if result["updates"] has stop_loss and ticket: ex.apply_stop(...)
        if trade newly OPEN/PENDING and no ticket: ex.ensure_ticket(...)
    mism = ex.reconcile(open_rows)
```

The SAME unified laws drive values: stops/targets already computed by
`open_trades_manager` via `utils/trading_rules`; the executor only TRANSMITS.

Telegram: in `telegram_bot.py`, when env `TELEGRAM_DEMO_CHAT_ID` set and
`EXECUTION_MODE=mt5_demo`, send every card to that chat with prefix
`🧪 DEMO `; else unchanged.

## 8. Scheduler & watchdog (Windows VPS)

`scripts/run_demo_loop.py`: infinite loop, every 300s call
`run_trade_updates.main()` then a lightweight analysis call; write
`heartbeat.json` with UTC ts each cycle. Windows Task Scheduler second task
every 1 min runs `scripts/demo_watchdog.py`: if heartbeat older than 420s →
restart loop service + telegram alert. MT5 terminal auto-start with VPS.

## 9. Tests the agent MUST add (all with a fake `MetaTrader5` module
injected via `sys.modules`; no network):

- `tests/test_mt5_feed_timezone.py` (offset snapping 0/7200/10800).
- `tests/test_mt5_executor_idempotent_magic.py` (second ensure_ticket reuses).
- `tests/test_mt5_partial_close_fallback.py` (retcode fail → full+reopen).
- `tests/test_reconcile_halt.py` (SL drift 1.0 USD → halt + alert).
- `tests/test_demo_table_routing.py` (env TRADES_TABLE routes all DB calls).
- `tests/test_demo_cards_go_to_demo_chat.py`.
All existing 1641 tests stay green.

## 10. Shadow phase & Go/No-Go (operator gates, not code)

Run paper (main, Actions) and demo (VPS) in parallel 14 days. Daily compare
from `demo_metrics` + `trades_demo` vs `trades`: slippage avg ≤10 pts,
zero wrong-side/size, 5 consecutive zero-mismatch days, written operator
approval → PR to main with `execution_mode=paper` default. Rollback = stop
VPS service; paper unaffected; `trades_demo` remains for audit.

## 11. Definition of done (agent must output)

1. `git log --oneline demo/mt5` list. 2. Full pytest + barrier output tails.
3. Table of new env vars. 4. Demo chat screenshot description. 5. This doc
   updated with deviations, if any (deviations require operator approval).
