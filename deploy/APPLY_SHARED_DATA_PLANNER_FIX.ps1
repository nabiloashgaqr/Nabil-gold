# Atomic post-deployment fix. No calibration rebuild and no manual Tick Manager.
param([string]$Root = "C:\Nabil-gold")
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$release = Split-Path -Parent $MyInvocation.MyCommand.Path
$payload = if (Test-Path (Join-Path $release "payload")) { Join-Path $release "payload" } else { (Resolve-Path (Join-Path $release "..")).Path }
if (-not (Test-Path $Root)) { throw "Project root not found: $Root" }
if (-not (Test-Path (Join-Path $payload "config.json"))) { throw "Payload missing config.json" }
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run PowerShell as Administrator" }

Set-Location $Root
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:EXECUTION_MODE = "mt5_demo"
$env:DATA_SOURCE_PRIMARY = "mt5"
$env:TRADES_TABLE = "trades_demo"

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backup = Join-Path $Root "storage\deployment_backups\shared_data_planner_fix_$stamp"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Start-Transcript -Path (Join-Path $backup "install.log") -Force | Out-Null
$taskNames = @("SS_DemoWatchdog", "SS_DemoAnalysis", "SS_TickManager", "SS_MarketStatus")
$taskState = @{}
$fileState = @{}
$activated = $false

function Stop-TickOwner {
    try { Stop-ScheduledTask -TaskName "SS_TickManager" -ErrorAction SilentlyContinue } catch {}
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { [string]$_.CommandLine -match "run_tick_manager\.py" }
    foreach ($proc in $procs) { Invoke-CimMethod -InputObject $proc -MethodName Terminate | Out-Null }
    Start-Sleep -Seconds 2
    Remove-Item (Join-Path $Root "tick_manager.pid") -Force -ErrorAction SilentlyContinue
}

function Restore-Tasks {
    foreach ($name in $taskNames) {
        if (-not $taskState.ContainsKey($name)) { continue }
        try {
            if ($taskState[$name].Enabled) { Enable-ScheduledTask -TaskName $name | Out-Null }
            else { Disable-ScheduledTask -TaskName $name | Out-Null }
            if ($taskState[$name].WasRunning) { Start-ScheduledTask -TaskName $name }
        } catch { Write-Warning "Could not restore task $name : $($_.Exception.Message)" }
    }
}

function Restore-Files {
    Stop-TickOwner
    foreach ($rel in $fileState.Keys) {
        $target = Join-Path $Root $rel
        $saved = Join-Path $backup (Join-Path "files" $rel)
        if ($fileState[$rel]) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $saved -Destination $target -Force
        } elseif (Test-Path $target) { Remove-Item -LiteralPath $target -Force }
    }
    Restore-Tasks
}

function Invoke-IsolatedTests {
    $names = @("EXECUTION_MODE", "DATA_SOURCE_PRIMARY", "TRADES_TABLE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_DEMO_CHAT_ID", "SUPABASE_URL", "SUPABASE_KEY", "PYTHON_DOTENV_DISABLED", "PYTEST_RUNNING")
    $saved = @{}
    foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process"); [Environment]::SetEnvironmentVariable($name, $null, "Process") }
    [Environment]::SetEnvironmentVariable("PYTHON_DOTENV_DISABLED", "1", "Process")
    [Environment]::SetEnvironmentVariable("PYTEST_RUNNING", "true", "Process")
    try {
        & python -m pytest -q tests\test_unified_five_agent_system.py tests\test_planner_execution_gate.py tests\test_live_opposition_enforced.py tests\test_session_plan_telegram.py | Out-Host
        return $LASTEXITCODE
    } finally {
        foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process") }
        $env:EXECUTION_MODE = "mt5_demo"; $env:DATA_SOURCE_PRIMARY = "mt5"; $env:TRADES_TABLE = "trades_demo"
    }
}

try {
    Write-Host "[1/8] Verifying existing calibrations (no rebuild)..." -ForegroundColor Cyan
    foreach ($rel in @("storage\model_calibration\unified_trend_v1.json", "storage\model_calibration\auction_flow_v1.json", "storage\auction_flow.sqlite3")) {
        if (-not (Test-Path (Join-Path $Root $rel))) { throw "Required existing runtime artifact missing: $rel" }
    }

    Write-Host "[2/8] Stopping scheduled writers and Tick owner..." -ForegroundColor Cyan
    foreach ($name in $taskNames) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $task) { continue }
        $taskState[$name] = @{ Enabled = ($task.Settings.Enabled -ne $false); WasRunning = ($task.State -eq "Running") }
        try { Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue } catch {}
        Disable-ScheduledTask -TaskName $name | Out-Null
    }
    Stop-TickOwner

    Write-Host "[3/8] Backing up and applying payload..." -ForegroundColor Cyan
    $files = Get-ChildItem $payload -Recurse -File | Where-Object { -not $_.FullName.EndsWith(".env") }
    foreach ($file in $files) {
        $rel = $file.FullName.Substring($payload.Length).TrimStart('\')
        $target = Join-Path $Root $rel
        $exists = Test-Path $target
        $fileState[$rel] = $exists
        if ($exists) {
            $saved = Join-Path $backup (Join-Path "files" $rel)
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $saved) | Out-Null
            Copy-Item -LiteralPath $target -Destination $saved -Force
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }
    $activated = $true

    Write-Host "[4/8] Syntax, policy and targeted regression tests..." -ForegroundColor Cyan
    python -m py_compile services\agent_confidence_audit.py services\map_quality.py services\shared_market_data.py services\auction_flow_store.py services\session_planner.py services\telegram_bot.py services\timeframe_fusion.py agents\unified_trend_agent.py agents\auction_flow_agent.py scripts\run_analysis.py scripts\check_agents_now.py
    if ($LASTEXITCODE -ne 0) { throw "Syntax validation failed" }
    $testExit = Invoke-IsolatedTests
    if ($testExit -ne 0) { throw "Targeted regression tests failed" }

    Write-Host "[5/8] Restarting scheduled Tick Manager with existing Auction DB..." -ForegroundColor Cyan
    Enable-ScheduledTask -TaskName "SS_TickManager" | Out-Null
    Start-ScheduledTask -TaskName "SS_TickManager"
    $healthy = $false
    for ($i=0; $i -lt 36; $i++) {
        Start-Sleep -Seconds 5
        python -c "from utils.helpers import load_config,is_market_open; from services.auction_flow_store import AuctionFlowStore; c=load_config(); h=AuctionFlowStore(c['auction_flow']['storage_path']).health(); ok=(not is_market_open()) or (h['last_tick_age_seconds']<=5 and h['heartbeat_age_seconds']<=10 and h['unique_ticks_5m']>=60); print('AUCTION',h,'OK',ok); raise SystemExit(0 if ok else 1)"
        if ($LASTEXITCODE -eq 0) { $healthy = $true; break }
    }
    if (-not $healthy) { throw "Auction collector did not become healthy" }

    Write-Host "[6/8] Running one scheduled analysis cycle..." -ForegroundColor Cyan
    Enable-ScheduledTask -TaskName "SS_DemoAnalysis" | Out-Null
    Start-ScheduledTask -TaskName "SS_DemoAnalysis"
    for ($i=0; $i -lt 36; $i++) { Start-Sleep -Seconds 5; if ((Get-ScheduledTask -TaskName "SS_DemoAnalysis").State -ne "Running") { break } }
    $shared = Join-Path $Root "storage\shared_market_data.json"
    if (-not (Test-Path $shared)) { throw "Shared stored market-data snapshot was not created" }
    python -c "import json; x=json.load(open('storage/shared_market_data.json',encoding='utf-8')); m=x['shared_market_data']; assert m['fetched_once'] and m['stored']; assert set(m['timeframes'])=={'5m','15m','1H','4H'}; print('ONE SHARED STORED DATA SOURCE OK',m['snapshot_id'])"
    if ($LASTEXITCODE -ne 0) { throw "Shared market-data verification failed" }

    Write-Host "[7/8] Restoring task states..." -ForegroundColor Cyan
    Restore-Tasks
    Enable-ScheduledTask -TaskName "SS_TickManager" | Out-Null
    if ((Get-ScheduledTask -TaskName "SS_TickManager").State -ne "Running") { Start-ScheduledTask -TaskName "SS_TickManager" }

    Write-Host "[8/8] COMPLETE" -ForegroundColor Green
    Write-Host "One shared stored MT5 snapshot feeds all five agents." -ForegroundColor Green
    Write-Host "Planner READY loophole closed; weights/qualification/admission are explicit in Telegram." -ForegroundColor Green
    "Success $(Get-Date -Format o)" | Set-Content (Join-Path $backup "SUCCESS") -Encoding UTF8
    Stop-Transcript | Out-Null
    exit 0
}
catch {
    $failure = $_
    Write-Host "FIX FAILED: $($failure.Exception.Message)" -ForegroundColor Red
    try { if ($activated) { Restore-Files } else { Restore-Tasks }; Write-Host "ROLLBACK COMPLETE" -ForegroundColor Yellow } catch { Write-Host "ROLLBACK ERROR: $($_.Exception.Message)" -ForegroundColor Red }
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}
