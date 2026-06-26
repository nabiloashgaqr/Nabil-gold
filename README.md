# Nabil Gold — Multi-Asset Paper Trading Bot

Automated trading signal system for **Gold (XAU/USD)** and **WTI Oil** using a 5-agent weighted consensus. Runs on GitHub Actions with zero cost. No external AI APIs required.

> Paper trading only — not financial advice.

---

## How It Works

```
Twelve Data API
  → 5 Analysis Agents
  → Weighted Consensus
  → News / Session / Risk Filters
  → Telegram Signal
  → Supabase Trade Record
  → Trade Management (SL / TP / Trailing)
```

## Agents

| Agent | Role |
|---|---|
| Technical | RSI, EMA, MACD, ATR, Bollinger |
| Classical | Support/Resistance, Patterns, Fibonacci |
| SMC | Order Blocks, Liquidity, FVG |
| Price Action | Candlestick Patterns, Rejection |
| Multi-Timeframe | 5m/15m/1H/4H Alignment |

## Decision Rules

- Minimum **2 agents** must agree on direction
- Net weighted confidence must be **≥ 65%**
- Counter-trend trades against Daily Bias need **≥ 75%**
- Agents below 60% confidence are excluded

## Instruments

| Symbol | Type | Point Size |
|---|---|---|
| XAU/USD | Gold | $0.10 |
| WTI/USD | Oil | $0.01 |

## Trade Management

| Event | Action |
|---|---|
| +100 points | Move SL to entry |
| After breakeven | Trailing stop (100pt gap, 30pt step) |
| TP1 | Partial close (50%) |
| TP2 | Full close |
| 24 hours | Expire (if not protected) |

## Schedule

| Job | Frequency |
|---|---|
| Analysis | Every 5 min, 3AM–10PM |
| Trade Update | Every 5 min (offset by 1 min) |
| Daily Report | 11:00 PM |
| Weekly Report | Saturday 10:00 AM |

## Setup

### 1. Get API Key (Free)

Register at [twelvedata.com/register](https://twelvedata.com/register) — 800 calls/day

### 2. Add GitHub Secrets

`Settings → Secrets and variables → Actions`

| Secret | Required |
|---|---|
| `TWELVEDATA_API_KEY` | ✅ |
| `TELEGRAM_BOT_TOKEN` | ✅ |
| `TELEGRAM_CHAT_ID` | ✅ |
| `SUPABASE_URL` | ✅ |
| `SUPABASE_KEY` | ✅ |

### 3. Setup Supabase

Run `supabase_schema_unified.sql` in Supabase SQL Editor.

### 4. Setup Cron Jobs

**Analysis** (cron-job.org):
```
*/5 3-22 * * 1-5
```

**Trade Update** (cron-job.org):
```
1/5 3-22 * * 1-5
```

## GitHub Actions

| Workflow | Trigger |
|---|---|
| `analyze.yml` | cron-job.org |
| `update_trades.yml` | cron-job.org |
| `daily_report.yml` | Schedule |
| `weekly_report.yml` | Schedule |
| `tests.yml` | Push/PR |

## Local Run

```bash
pip install -r requirements.txt
python -m pytest -q
python scripts/run_analysis.py
```

## Project Structure

```
Nabil-gold/
├── agents/           # Analysis + decision agents
├── services/         # Market data, DB, Telegram
├── scripts/          # Entry points for workflows
├── tests/            # 299 tests
├── config.json       # All settings
└── supabase_schema_unified.sql
```

## Tech Stack

- **Python 3.11+** — zero external AI APIs
- **Twelve Data** — market data (free tier)
- **Supabase** — PostgreSQL persistence
- **Telegram Bot** — notifications
- **GitHub Actions** — stateless runner

---

**Paper first. Measure everything.**
