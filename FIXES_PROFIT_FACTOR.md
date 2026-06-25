# Profit Factor 0 Bug + Telegram Report Cleanup (2026-06-25)

## Root Cause (Fixed)
- `gross_loss == 0` → old code always returned `0` (division avoided)
- Dashboard (`services/dashboard.py:77`), DailyReport (`agents/daily_report_agent.py:42`), Weekly
- Exact data: 8 trades, 100% WR, +3250.3 net pts, 0 losses → PF should be ∞

## Changes Implemented
### 1. Core Calculation (consistent)
- `services/dashboard.py` (summarize_trades + _render_cards + format_dashboard_telegram)
- `agents/daily_report_agent.py` (_stats + _format_report)
- `services/weekly_report.py` (WeeklyStats + collect_stats + fallback + prompt)

Logic:
```python
pf = 99.9 if gross_loss == 0 and gross_profit > 0 else round(...) if gross_loss else 0
pf_display = "∞" if pf >= 99 else pf
```

### 2. Telegram Reports — Clean & Organized
**Dashboard Telegram (format_dashboard_telegram):**
```
📊 Dashboard Updated
━━━━━━━━━━━━━━━━━━━━
Trades: 8 | Open: 0
Win Rate: 100.0%
Net Points: +3250.3
Profit Factor: ∞ (All profitable trades → ∞)
...
📌 Note: PF=∞ when no losing trades (gross_loss=0).
```

**Daily Report (run_daily_report + agent):**
- Compact Statistics + Performance
- Data Quality note
- Max 4 recommendations
- No duplication (removed junk comment blocks)
- Clear "Best: X | Worst: Y | PF: ∞"

**Weekly Report:**
- Now includes `profit_factor` in JSON + fallback
- Groq prompt updated to handle ∞ case

### 3. Dashboard HTML
- PF card shows `∞` when applicable
- Preserved exact look

## Preserved
- entry_style=fixed_risk (max_risk=300, scale_in_max=1, trigger=50, size_ratio=1.0)
- All prior cleanups (pending orders removed, SQL unified)
- Precise values: gross_profit / gross_loss / 99.9 / ∞

## Testing
```bash
python3 -c "
from services.dashboard import summarize_trades, format_dashboard_telegram
from agents.daily_report_agent import DailyReportAgent
trades = [{'final_pnl':406.3, 'status':'TP2_HIT', 'type':'BUY'}]*8
print(format_dashboard_telegram(summarize_trades(trades)))
print(DailyReportAgent({}).generate(trades)['text'])
"
# Output: PF=∞ + clean report ✅
```

## Suggestions Implemented
1. ✅ Consistent PF logic everywhere (99.9 internal → ∞ display)
2. ✅ Added Data Quality note to prevent user confusion
3. ✅ Removed repetition (consolidated daily report already good, cleaned further)
4. ✅ Added explicit note in dashboard + daily
5. ✅ Organized sections (Statistics → Performance → Direction → Sources → Recs + DQ)
6. ✅ Actionable "Best source" always shown
7. ✅ Weekly now includes PF

All Telegram reports now:
- No repetition
- Logical reasons only
- Highly organized
- No "Profit Factor: 0"

Next run of daily report / dashboard will show correct ∞.
