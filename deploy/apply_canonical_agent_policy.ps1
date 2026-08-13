# Apply operator-approved canonical agent weights + unified 67% bar.
# Run as Administrator after extracting the round over C:\Nabil-gold.
param([switch]$NoRestart)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

python -m py_compile `
  agents\decision_agent.py agents\open_trades_manager.py agents\daily_report_agent.py `
  services\directional_authority.py services\session_planner.py `
  services\strategy_profiles.py services\thesis_consensus.py `
  services\telegram_bot.py services\database.py services\demo_handoff.py `
  services\weekly_report.py services\dashboard.py `
  scripts\run_analysis.py scripts\run_tick_manager.py scripts\run_daily_report.py
if ($LASTEXITCODE -ne 0) { throw "Python syntax validation failed; NOT rebooting." }

python -c "import json; c=json.load(open('config.json',encoding='utf-8')); assert c['agent_weights']=={'multitimeframe':0.15,'classical':0.25,'smc':0.2,'price_action':0.2,'technical':0.2}; assert c['signal_requirements']['agent_min_confidence']==67; assert c['session_planner']['agent_alignment_min_confidence']==67; assert c['strategy_profile_weight_overrides_enabled'] is False; assert c['unify_agent_min_confidence'] is True; assert c['trade_management']['thesis_exit']['agent_vote']['mirror_entry_admission'] is True; assert all(p['min_agents_agree']==3 and p['min_consensus_confidence']==72 for p in c['strategy_profiles'].values()); assert c['opposite_entry_guard']['close_before_new_entry'] is True; print('CANONICAL ENTRY/EXIT POLICY OK')"
if ($LASTEXITCODE -ne 0) { throw "Canonical policy validation failed; NOT rebooting." }

python -c "import json; from services.thesis_consensus import evaluate_directional_admission as e; c=json.load(open('config.json',encoding='utf-8')); d={n:{'direction':'SELL','confidence':80} for n in ('technical','classical','smc')}; r=e('SELL',d,c); assert r['allow'] and r['path']=='THREE_AGENT_CONSENSUS'; print('THESIS ENTRY/EXIT PARITY OK')"
if ($LASTEXITCODE -ne 0) { throw "Thesis parity validation failed; NOT rebooting." }

python -c "from scripts.run_tick_manager import corrected_execution_stop; s,req,act=corrected_execution_stop('BUY',4392.63,4368.0,4395.0,4368.0); assert (s,req,act)==(4365.63,270.0,246.3); print('ACTUAL-FILL STOP LAW OK')"
if ($LASTEXITCODE -ne 0) { throw "Actual-fill stop-law validation failed; NOT rebooting." }

python -c "from services.telegram_bot import TelegramService; t=TelegramService._polish_message_text('Paper trading signal\nSame\nSame',demo=True); assert 'paper' not in t.lower() and t.count('Same')==1 and t.count('DEMO')==1; print('TELEGRAM POLISH OK')"
if ($LASTEXITCODE -ne 0) { throw "Telegram polish validation failed; NOT rebooting." }

python -c "import uuid; from services.telegram_bot import TelegramService; s=TelegramService({}); k='deploy-'+uuid.uuid4().hex; assert s._reserve_delivery(k); s._finish_delivery(k,True); print('TELEGRAM SQLITE DEDUP OK')"
if ($LASTEXITCODE -ne 0) { throw "Telegram SQLite dedup validation failed; NOT rebooting." }

# Retire the old JSON lock backend that produced WinError/Errno 13. The new
# dedup store is SQLite/WAL and uses no .lock file.
Get-ChildItem "storage\telegram_delivery_dedup.json*" -ErrorAction SilentlyContinue | `
  ForEach-Object { attrib -R $_.FullName 2>$null; Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }

Write-Host "Canonical entry/exit parity, flip guard, stop law, Telegram policy, and SQLite dedup validated." -ForegroundColor Green
if ($NoRestart) {
  Write-Host "Required next command: Restart-Computer -Force" -ForegroundColor Yellow
  exit 0
}
Restart-Computer -Force
