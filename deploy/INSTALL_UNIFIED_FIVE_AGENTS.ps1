# One-command atomic switch to Unified Trend + Auction Flow (MT5 DEMO only).
param(
    [string]$Root = "C:\Nabil-gold",
    [switch]$SkipFullTests
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$release = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path (Join-Path $release "payload")) {
    $payload = Join-Path $release "payload"
} else {
    # Allows the same script to be syntax-checked from deploy/ in the repo.
    $payload = (Resolve-Path (Join-Path $release "..")).Path
}
if (-not (Test-Path $Root)) { throw "Project root not found: $Root" }
if (-not (Test-Path (Join-Path $payload "config.json"))) { throw "Release payload missing config.json: $payload" }

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run PowerShell as Administrator."
}

Set-Location $Root
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:EXECUTION_MODE = "mt5_demo"
$env:DATA_SOURCE_PRIMARY = "mt5"
$env:TRADES_TABLE = "trades_demo"

function Restore-PreviousIncompleteSwitch {
    # v1 of this installer used Write-Error inside catch while ErrorAction=Stop,
    # which could interrupt its own rollback. Detect that exact partial state:
    # new five-agent config is present but one/both mandatory calibrations are
    # missing. Restore the latest unfinished backup before attempting v2.
    $configPath = Join-Path $Root "config.json"
    $partial = $false
    try {
        $cfg = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $newBook = $null -ne $cfg.agent_weights.unified_trend
        $uCal = Test-Path (Join-Path $Root "storage\model_calibration\unified_trend_v1.json")
        $aCal = Test-Path (Join-Path $Root "storage\model_calibration\auction_flow_v1.json")
        $partial = $newBook -and (-not ($uCal -and $aCal))
    } catch {}
    if (-not $partial) { return }

    $base = Join-Path $Root "storage\deployment_backups"
    $candidate = Get-ChildItem $base -Directory -Filter "unified_five_agents_*" -ErrorAction SilentlyContinue |
        Where-Object { -not (Test-Path (Join-Path $_.FullName "SUCCESS")) -and -not (Test-Path (Join-Path $_.FullName "ROLLED_BACK")) } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $candidate) {
        throw "Incomplete prior switch detected, but no rollback backup was found in $base"
    }
    Write-Host "Recovering incomplete previous switch from $($candidate.FullName)..." -ForegroundColor Yellow
    $fileJson = Join-Path $candidate.FullName "file_state.json"
    if (-not (Test-Path $fileJson)) { throw "Previous backup has no file_state.json: $($candidate.FullName)" }
    $state = Get-Content $fileJson -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($prop in $state.PSObject.Properties) {
        $rel = $prop.Name
        $target = Join-Path $Root $rel
        $saved = Join-Path $candidate.FullName (Join-Path "files" $rel)
        if ([bool]$prop.Value) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $saved -Destination $target -Force
        } elseif (Test-Path $target) {
            Remove-Item -LiteralPath $target -Force
        }
    }
    foreach ($rel in @(
        "storage\model_calibration\unified_trend_v1.json",
        "storage\model_calibration\auction_flow_v1.json",
        "storage\auction_flow.sqlite3", "storage\auction_flow.sqlite3-wal", "storage\auction_flow.sqlite3-shm"
    )) {
        $target = Join-Path $Root $rel
        $saved = Join-Path $candidate.FullName (Join-Path "runtime" $rel)
        if (Test-Path $saved) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $saved -Destination $target -Force
        } elseif (Test-Path $target) {
            Remove-Item -LiteralPath $target -Force
        }
    }
    $taskJson = Join-Path $candidate.FullName "task_state.json"
    if (Test-Path $taskJson) {
        $tasks = Get-Content $taskJson -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($prop in $tasks.PSObject.Properties) {
            $name = $prop.Name; $s = $prop.Value
            try {
                if ([bool]$s.Enabled) { Enable-ScheduledTask -TaskName $name | Out-Null }
                else { Disable-ScheduledTask -TaskName $name | Out-Null }
                if ([bool]$s.WasRunning) { Start-ScheduledTask -TaskName $name }
            } catch { Write-Warning "Could not restore prior task $name : $($_.Exception.Message)" }
        }
    }
    "Recovered automatically by installer v2 at $(Get-Date -Format o)" |
        Set-Content (Join-Path $candidate.FullName "AUTO_RECOVERED") -Encoding UTF8
}

Restore-PreviousIncompleteSwitch

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backup = Join-Path $Root "storage\deployment_backups\unified_five_agents_$stamp"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
$log = Join-Path $backup "install.log"
Start-Transcript -Path $log -Force | Out-Null

$taskNames = @(
    "SS_DemoWatchdog", "SS_DemoAnalysis", "SS_DemoLoop", "SS_TickManager",
    "SS_MarketStatus", "SS_MacroContext", "SS_Dashboard", "SS_DashboardAPI",
    "SS_DailyReport", "SS_WeeklyReport"
)
$taskState = @{}
$fileState = @{}
$activated = $false
$dashboardTaskCreated = $false

function Get-ReleaseFiles {
    $skip = @("README_AR.md", "CHANGES.patch", "SHA256SUMS.json")
    Get-ChildItem -Path $payload -Recurse -File | Where-Object {
        $rel = $_.FullName.Substring($payload.Length).TrimStart('\')
        ($skip -notcontains $rel) -and (-not $rel.StartsWith("storage\")) -and (-not $rel.EndsWith(".env"))
    }
}

function Stop-TickManagerProcesses {
    try { Stop-ScheduledTask -TaskName "SS_TickManager" -ErrorAction SilentlyContinue } catch {}
    try {
        $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { [string]$_.CommandLine -match "run_tick_manager\.py" }
        foreach ($proc in $procs) {
            Write-Host "Stopping stale Tick Manager PID $($proc.ProcessId)..." -ForegroundColor Yellow
            Invoke-CimMethod -InputObject $proc -MethodName Terminate | Out-Null
        }
    } catch { Write-Warning "Could not enumerate/stop Tick Manager process: $($_.Exception.Message)" }
    Start-Sleep -Seconds 2
    $still = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { [string]$_.CommandLine -match "run_tick_manager\.py" }
    if (-not $still) {
        Remove-Item (Join-Path $Root "tick_manager.pid") -Force -ErrorAction SilentlyContinue
    }
}

function Grant-AuctionStorageAccess {
    $storage = Join-Path $Root "storage"
    New-Item -ItemType Directory -Force -Path $storage | Out-Null
    Get-ChildItem $storage -Filter "auction_flow.sqlite3*" -Force -ErrorAction SilentlyContinue |
        ForEach-Object {
            try { $_.IsReadOnly = $false } catch {}
        }

    # Calibration runs elevated, while SS_TickManager may run as SYSTEM or a
    # different scheduled-task principal. Explicitly inherit/grant Modify so
    # SQLite can create/write WAL/SHM sidecars under that identity.
    & icacls.exe $storage /inheritance:e /grant "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /T /C | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Could not grant SYSTEM/Administrators access to storage" }
    $task = Get-ScheduledTask -TaskName "SS_TickManager" -ErrorAction SilentlyContinue
    $taskUser = if ($task) { [string]$task.Principal.UserId } else { "" }
    if ($taskUser) {
        Write-Host "Tick Manager task principal: $taskUser" -ForegroundColor Cyan
        & icacls.exe $storage /grant "${taskUser}:(OI)(CI)M" /T /C | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Could not grant Tick Manager principal access to storage" }
    }
}

function Restore-Tasks {
    foreach ($name in $taskNames) {
        if (-not $taskState.ContainsKey($name)) { continue }
        $state = $taskState[$name]
        try {
            if ($state.Enabled) { Enable-ScheduledTask -TaskName $name | Out-Null }
            else { Disable-ScheduledTask -TaskName $name | Out-Null }
            if ($state.WasRunning) { Start-ScheduledTask -TaskName $name }
        } catch { Write-Warning "Could not restore task $name : $($_.Exception.Message)" }
    }
}

function Rollback-Install {
    Write-Host "ROLLBACK: stopping deployed processes before restoring files..." -ForegroundColor Yellow
    Stop-TickManagerProcesses
    Write-Host "ROLLBACK: restoring pre-install files and tasks..." -ForegroundColor Yellow
    foreach ($rel in $fileState.Keys) {
        $target = Join-Path $Root $rel
        $saved = Join-Path $backup (Join-Path "files" $rel)
        if ($fileState[$rel]) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $saved -Destination $target -Force
        } elseif (Test-Path $target) {
            Remove-Item -LiteralPath $target -Force
        }
    }
    foreach ($rel in @(
        "storage\model_calibration\unified_trend_v1.json",
        "storage\model_calibration\auction_flow_v1.json",
        "storage\auction_flow.sqlite3", "storage\auction_flow.sqlite3-wal", "storage\auction_flow.sqlite3-shm"
    )) {
        $target = Join-Path $Root $rel
        $saved = Join-Path $backup (Join-Path "runtime" $rel)
        if (Test-Path $saved) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $saved -Destination $target -Force
        } elseif (Test-Path $target) {
            Remove-Item -LiteralPath $target -Force
        }
    }
    if ($dashboardTaskCreated) {
        try { Unregister-ScheduledTask -TaskName "SS_DashboardAPI" -Confirm:$false -ErrorAction SilentlyContinue } catch {}
    }
    Restore-Tasks
}

function Invoke-IsolatedPytest([string[]]$Arguments) {
    # A live VPS carries real Telegram/MT5/Supabase variables and .env. Tests
    # intentionally assert behaviour with no token and with non-demo delivery;
    # inheriting production credentials/mode makes those tests contact the
    # wrong branches and can serialize MagicMock objects into the live book.
    $names = @(
        "EXECUTION_MODE", "DATA_SOURCE_PRIMARY", "TRADES_TABLE",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_DEMO_CHAT_ID",
        "SUPABASE_URL", "SUPABASE_KEY", "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER",
        "PYTHON_DOTENV_DISABLED", "PYTEST_RUNNING"
    )
    $saved = @{}
    foreach ($name in $names) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
    [Environment]::SetEnvironmentVariable("PYTHON_DOTENV_DISABLED", "1", "Process")
    [Environment]::SetEnvironmentVariable("PYTEST_RUNNING", "true", "Process")
    try {
        & python -m pytest @Arguments | Out-Host
        $code = $LASTEXITCODE
        return $code
    }
    finally {
        foreach ($name in $names) {
            [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process")
        }
        # The install/calibration phase itself is explicitly DEMO + native MT5.
        $env:EXECUTION_MODE = "mt5_demo"
        $env:DATA_SOURCE_PRIMARY = "mt5"
        $env:TRADES_TABLE = "trades_demo"
    }
}

try {
    Write-Host "[1/12] Verifying current MT5 account is DEMO..." -ForegroundColor Cyan
    python -c "import MetaTrader5 as m; assert m.initialize(), m.last_error(); a=m.account_info(); assert a is not None; assert int(a.trade_mode)==int(m.ACCOUNT_TRADE_MODE_DEMO), 'REAL ACCOUNT REFUSED'; print('MT5 DEMO PREFLIGHT OK',a.login,a.server)"
    if ($LASTEXITCODE -ne 0) { throw "MT5 DEMO preflight failed" }

    Write-Host "[2/12] Capturing and stopping scheduled writers..." -ForegroundColor Cyan
    foreach ($name in $taskNames) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $task) { continue }
        $taskState[$name] = @{ Enabled = ($task.Settings.Enabled -ne $false); WasRunning = ($task.State -eq "Running") }
        try { Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue } catch {}
        Disable-ScheduledTask -TaskName $name | Out-Null
    }
    Stop-TickManagerProcesses
    Start-Sleep -Seconds 3

    Write-Host "[3/12] Verifying release checksums and making rollback backup..." -ForegroundColor Cyan
    $checksumPath = Join-Path $release "SHA256SUMS.json"
    if (Test-Path $checksumPath) {
        $checksums = Get-Content $checksumPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($prop in $checksums.PSObject.Properties) {
            $source = Join-Path $payload $prop.Name
            if (-not (Test-Path $source)) { throw "Payload file missing: $($prop.Name)" }
            $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant()
            if ($actual -ne ([string]$prop.Value).ToLowerInvariant()) { throw "Checksum mismatch: $($prop.Name)" }
        }
    }
    foreach ($file in Get-ReleaseFiles) {
        $rel = $file.FullName.Substring($payload.Length).TrimStart('\')
        $target = Join-Path $Root $rel
        $existed = Test-Path $target
        $fileState[$rel] = $existed
        if ($existed) {
            $saved = Join-Path $backup (Join-Path "files" $rel)
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $saved) | Out-Null
            Copy-Item -LiteralPath $target -Destination $saved -Force
        }
    }
    foreach ($rel in @(
        "storage\model_calibration\unified_trend_v1.json",
        "storage\model_calibration\auction_flow_v1.json",
        "storage\auction_flow.sqlite3", "storage\auction_flow.sqlite3-wal", "storage\auction_flow.sqlite3-shm"
    )) {
        $target = Join-Path $Root $rel
        if (Test-Path $target) {
            $saved = Join-Path $backup (Join-Path "runtime" $rel)
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $saved) | Out-Null
            Copy-Item -LiteralPath $target -Destination $saved -Force
        }
    }
    $taskState | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $backup "task_state.json") -Encoding UTF8
    $fileState | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $backup "file_state.json") -Encoding UTF8

    Write-Host "[4/12] Applying the complete payload while writers are stopped..." -ForegroundColor Cyan
    foreach ($file in Get-ReleaseFiles) {
        $rel = $file.FullName.Substring($payload.Length).TrimStart('\')
        $target = Join-Path $Root $rel
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }
    $activated = $true

    Write-Host "[5/12] Syntax and policy verification..." -ForegroundColor Cyan
    python -m py_compile agents\unified_trend_agent.py agents\auction_flow_agent.py agents\classical_agent.py agents\smc_agent.py agents\price_action_agent.py agents\decision_agent.py agents\open_trades_manager.py services\timeframe_fusion.py services\auction_flow_store.py services\thesis_consensus.py services\session_planner.py services\telegram_bot.py scripts\run_analysis.py scripts\run_tick_manager.py scripts\build_agent_calibrations.py scripts\verify_unified_five_agents.py
    if ($LASTEXITCODE -ne 0) { throw "Python syntax verification failed" }
    python -c "import json; c=json.load(open('config.json',encoding='utf-8')); e={'unified_trend':.2,'classical':.25,'smc':.2,'price_action':.2,'auction_flow':.15}; assert c['agent_weights']==e; assert sum(e.values())==1; assert c['all_agents_timeframes']['required']==['5m','15m','1H','4H']; assert c['all_agents_timeframes']['require_native'] is True; assert c['data_source']['resample_timeframes_from_base'] is False; assert c['signal_requirements']['agent_min_confidence']==67; assert c['signal_requirements']['min_consensus_confidence']==72; assert c['signal_requirements']['min_agents_agree']==3; print('UNIFIED POLICY OK')"
    if ($LASTEXITCODE -ne 0) { throw "Unified policy verification failed" }

    Write-Host "[6/12] Running tests in an isolated environment (no live secrets/mode)..." -ForegroundColor Cyan
    $testExit = Invoke-IsolatedPytest @(
        "-q", "tests\test_unified_five_agent_system.py",
        "tests\test_canonical_agent_policy.py", "tests\test_thesis_exit_entry_parity.py",
        "tests\test_actual_fill_stop_law.py", "tests\test_telegram_polish_dedup.py"
    )
    if ($testExit -ne 0) { throw "Release regression tests failed" }
    if (-not $SkipFullTests) {
        $testExit = Invoke-IsolatedPytest @("-q")
        if ($testExit -ne 0) { throw "Full isolated test suite failed" }
    }

    Write-Host "[7/12] Loading native MT5 M5/M15/H1/H4 and building both calibrations..." -ForegroundColor Cyan
    python scripts\build_agent_calibrations.py --days 90 --tick-days 30 --reset-auction-store
    if ($LASTEXITCODE -ne 0) { throw "Calibration/data bootstrap failed" }
    Grant-AuctionStorageAccess

    Write-Host "[8/12] Starting the single scheduled Tick Manager..." -ForegroundColor Cyan
    Stop-TickManagerProcesses
    python -c "from utils.helpers import load_config; from services.auction_flow_store import AuctionFlowStore; c=load_config(); s=AuctionFlowStore(c['auction_flow']['storage_path']); s.meta(); print('AUCTION SQLITE SCHEMA OK')"
    if ($LASTEXITCODE -ne 0) { throw "Auction SQLite schema preflight failed" }
    $tickTask = Get-ScheduledTask -TaskName "SS_TickManager" -ErrorAction Stop
    Enable-ScheduledTask -TaskName "SS_TickManager" | Out-Null
    Start-ScheduledTask -TaskName "SS_TickManager"
    $ready = $false
    for ($i=0; $i -lt 42; $i++) {
        Start-Sleep -Seconds 5
        python -c "import json; from utils.helpers import load_config,is_market_open; from services.auction_flow_store import AuctionFlowStore; c=load_config(); h=AuctionFlowStore(c['auction_flow']['storage_path']).health(); ok=(not is_market_open()) or (h['last_tick_age_seconds']<=c['auction_flow']['max_tick_age_seconds'] and h['heartbeat_age_seconds']<=c['auction_flow']['max_heartbeat_age_seconds'] and h['unique_ticks_5m']>=c['auction_flow']['min_unique_ticks_5m']); print('AUCTION HEALTH',json.dumps(h,default=str),'market_open=',is_market_open(),'ok=',ok); raise SystemExit(0 if ok else 1)"
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        if (($i % 12) -eq 11) {
            $taskInfo = Get-ScheduledTaskInfo -TaskName "SS_TickManager" -ErrorAction SilentlyContinue
            Write-Host "Tick task result: $($taskInfo.LastTaskResult)" -ForegroundColor Yellow
            if (Test-Path (Join-Path $Root "logs\tick_manager.log")) {
                Get-Content (Join-Path $Root "logs\tick_manager.log") -Tail 15 -Encoding UTF8 | Out-Host
            }
        }
    }
    if (-not $ready) { throw "Scheduled Auction collector did not become healthy; health/task/log were printed above" }

    Write-Host "[9/12] Starting dashboard API and one scheduled analysis cycle..." -ForegroundColor Cyan
    if (-not (Get-ScheduledTask -TaskName "SS_DashboardAPI" -ErrorAction SilentlyContinue)) {
        & (Join-Path $Root "deploy\setup_local_dashboard.ps1") -NoRestart
        if ($LASTEXITCODE -ne 0) { throw "Could not register SS_DashboardAPI" }
        $dashboardTaskCreated = $true
    }
    foreach ($name in @("SS_DashboardAPI", "SS_Dashboard")) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Enable-ScheduledTask -TaskName $name | Out-Null
            Start-ScheduledTask -TaskName $name
        }
    }
    if (Get-ScheduledTask -TaskName "SS_DemoAnalysis" -ErrorAction SilentlyContinue) {
        Enable-ScheduledTask -TaskName "SS_DemoAnalysis" | Out-Null
        Start-ScheduledTask -TaskName "SS_DemoAnalysis"
        for ($i=0; $i -lt 36; $i++) {
            Start-Sleep -Seconds 5
            $task = Get-ScheduledTask -TaskName "SS_DemoAnalysis"
            if ($task.State -ne "Running") { break }
        }
    }

    Write-Host "[10/12] Verifying five agents, parity, Telegram and dashboard names..." -ForegroundColor Cyan
    python scripts\verify_unified_five_agents.py
    if ($LASTEXITCODE -ne 0) { throw "Post-install verification failed" }
    python -c "from services.thesis_consensus import VOTING_AGENTS; assert VOTING_AGENTS==('unified_trend','classical','smc','price_action','auction_flow'); print('ENTRY/EXIT PARITY AGENT BOOK OK')"
    if ($LASTEXITCODE -ne 0) { throw "Entry/exit parity verification failed" }

    Write-Host "[11/12] Restoring scheduled-task enable/running state..." -ForegroundColor Cyan
    Restore-Tasks
    # The two long-lived owners must be alive after a successful switch.
    foreach ($name in @("SS_TickManager", "SS_DashboardAPI")) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Enable-ScheduledTask -TaskName $name | Out-Null
            if ((Get-ScheduledTask -TaskName $name).State -ne "Running") { Start-ScheduledTask -TaskName $name }
        }
    }

    Write-Host "[12/12] COMPLETE" -ForegroundColor Green
    Write-Host "Unified Trend + Classical + SMC + Price Action + Auction Flow are active." -ForegroundColor Green
    Write-Host "Every core agent reads native M5/M15/H1/H4 and emits one vote." -ForegroundColor Green
    Write-Host "Rollback backup: $backup" -ForegroundColor Yellow
    "Successful unified five-agent switch at $(Get-Date -Format o)" |
        Set-Content (Join-Path $backup "SUCCESS") -Encoding UTF8
    Stop-Transcript | Out-Null
    exit 0
}
catch {
    $failure = $_
    # Never call Write-Error before rollback while ErrorActionPreference=Stop:
    # it throws from inside catch and would strand disabled tasks/new files.
    Write-Host "INSTALL FAILED: $($failure.Exception.Message)" -ForegroundColor Red
    try {
        if ($activated) { Rollback-Install } else { Restore-Tasks }
        "Rolled back at $(Get-Date -Format o): $($failure.Exception.Message)" |
            Set-Content (Join-Path $backup "ROLLED_BACK") -Encoding UTF8
        Write-Host "ROLLBACK COMPLETE - previous files and task states restored." -ForegroundColor Yellow
    }
    catch {
        Write-Host "ROLLBACK ERROR: $($_.Exception.Message)" -ForegroundColor Red
    }
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}
