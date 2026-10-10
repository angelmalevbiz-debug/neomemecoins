param(
    [ValidateSet('Start', 'StartMissing', 'Stop', 'Status')][string]$Action = 'Start',
    [int]$MainPort = 8878,
    [int]$GatewayPort = 8879,
    [string]$SupabasePublishableKey = '',
    [switch]$WatchdogRecovery
)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtime = Join-Path $repository '.runtime/accounts'
$services = Join-Path $runtime 'services'
$manifestPath = Join-Path $services 'processes.json'
$stopMarker = Join-Path $services 'stop.request'
$watchdogPauseMarker = Join-Path $services 'watchdog.pause'
$runner = Join-Path $PSScriptRoot 'local_paper_service.py'
$python = Join-Path $repository '.venv/Scripts/python.exe'

function Get-OwnedProcess($record) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)" -ErrorAction SilentlyContinue
    if ($null -ne $process -and $process.CommandLine -like "*$runner*") {
        if ($process.CommandLine -notmatch "(?:^|\s)--service\s+$([regex]::Escape($record.service))(?:\s|$)") { return $null }
        if ($record.PSObject.Properties['run_token'] -and
            $process.CommandLine -notmatch "(?:^|\s)--run-token\s+$([regex]::Escape($record.run_token))(?:\s|$)") { return $null }
        # ConvertFrom-Json may parse an ISO timestamp with a trailing Z into a
        # DateTime object, whose string form is local time. Compare instants,
        # with a small tolerance for CIM timestamp precision, rather than text.
        $expectedCreated = ([DateTime]$record.created_at).ToUniversalTime()
        $actualCreated = $process.CreationDate.ToUniversalTime()
        if ([Math]::Abs(($actualCreated - $expectedCreated).TotalSeconds) -lt 2) { return $process }
    }
    return $null
}

function Wait-WorkerRecord([string]$Service, [string]$Token, [string]$ReadyFile,
                           [string]$Stdout, [string]$Stderr) {
    # Windows venv python.exe can launch another Python process. The launcher
    # PID is not the service PID; only the runner can identify its actual worker.
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $ReadyFile) {
            $ready = Get-Content -LiteralPath $ReadyFile -Raw | ConvertFrom-Json
            if ($ready.service -ne $Service -or $ready.run_token -ne $Token -or
                ($ready.pid -isnot [long] -and $ready.pid -isnot [int])) {
                throw "Invalid startup identity for $Service; review $Stderr."
            }
            $worker = Get-CimInstance Win32_Process -Filter "ProcessId=$($ready.pid)" -ErrorAction SilentlyContinue
            if ($null -eq $worker) { throw "The $Service worker exited during startup; review $Stderr." }
            $record = [pscustomobject]@{
                service=$Service; pid=$ready.pid; run_token=$Token;
                created_at=$worker.CreationDate.ToUniversalTime().ToString('o');
                stdout=$Stdout; stderr=$Stderr
            }
            if ($null -eq (Get-OwnedProcess $record)) { throw "The $Service startup PID is not the owned runner." }
            $port = if ($Service -eq 'main') { $MainPort } elseif ($Service -eq 'gateway') { $GatewayPort } else { $null }
            if ($null -eq $port -or (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
                Where-Object OwningProcess -eq $record.pid)) { return $record }
        }
        Start-Sleep -Milliseconds 200
    }
    throw "The $Service worker did not become ready within 30 seconds; review $Stderr."
}

if ($Action -eq 'Status') {
    if (Test-Path -LiteralPath $manifestPath) {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        foreach ($record in $manifest.processes) {
            [pscustomobject]@{ service=$record.service; pid=$record.pid; running=($null -ne (Get-OwnedProcess $record)); stdout=$record.stdout; stderr=$record.stderr }
        }
    } else { Write-Output 'No local PAPER process manifest exists.' }
    exit 0
}
# The watchdog holds this same reentrant mutex from its health probe through
# recovery. Manual mutations therefore cannot expose a partial startup manifest
# to a competing recovery decision, or be stopped by a stale watchdog probe.
$operationMutex = [Threading.Mutex]::new($false, 'Local\NeoLocalPaperOperation')
$operationOwned = $false
try {
    try { $operationOwned = $operationMutex.WaitOne(30000) }
    catch [Threading.AbandonedMutexException] { $operationOwned = $true }
    if (!$operationOwned) { throw 'Another PAPER lifecycle operation is in progress; no services were changed.' }
if ($Action -eq 'Stop') {
    if (!(Test-Path -LiteralPath $manifestPath)) { throw 'No owned local PAPER process manifest exists.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if (!$WatchdogRecovery) {
        [IO.File]::WriteAllText($watchdogPauseMarker, [DateTime]::UtcNow.ToString('o'))
    }
    [IO.File]::WriteAllText($stopMarker, [DateTime]::UtcNow.ToString('o'))
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        $remaining = @($manifest.processes | Where-Object { $null -ne (Get-OwnedProcess $_) })
        if ($remaining.Count -eq 0) { Write-Output 'All owned PAPER services stopped and flushed.'; exit 0 }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Owned services are still flushing; no forced termination performed. Review service logs.'
}

if (!(Test-Path -LiteralPath $python)) { throw 'Install the isolated .venv dependencies before starting PAPER services.' }
if ($Action -eq 'StartMissing') {
    if (!(Test-Path -LiteralPath $manifestPath)) { throw 'StartMissing requires a verified existing local PAPER manifest.' }
    $previous = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    foreach ($requiredService in @('main', 'gateway')) {
        $required = @($previous.processes | Where-Object service -eq $requiredService)
        if ($required.Count -ne 1 -or $null -eq (Get-OwnedProcess $required[0])) {
            throw "The existing owned $requiredService service is not running; no existing process was stopped."
        }
    }
    $records = @($previous.processes | Where-Object {
        $_.service -in @('main', 'gateway') -and $null -ne (Get-OwnedProcess $_)
    })
    $servicesToStart = @()
    foreach ($candidate in @('tape', 'lab')) {
        $instances = @($previous.processes | Where-Object service -eq $candidate)
        $owned = @($instances | Where-Object { $null -ne (Get-OwnedProcess $_) })
        if ($owned.Count -gt 1) { throw "Multiple owned $candidate processes are running; no process was stopped." }
        if ($owned.Count -eq 1) { $records += $owned[0] }
        else { $servicesToStart += $candidate }
    }
    if ($servicesToStart.Count -eq 0) { Write-Output 'All local PAPER services are already running.'; exit 0 }
} else {
    foreach ($port in @($MainPort, $GatewayPort)) {
        if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "Port $port is already occupied; no existing process will be stopped." }
    }
    if (Test-Path -LiteralPath $manifestPath) {
        $previous = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        if (@($previous.processes | Where-Object { $null -ne (Get-OwnedProcess $_) }).Count -gt 0) { throw 'An owned PAPER service is already running.' }
    }
    $records = @()
    $servicesToStart = @('main', 'tape', 'lab', 'gateway')
}
# This is the public Auth verification key already distributed to browsers.
if (!$SupabasePublishableKey) {
    $serviceConfig = Get-Content -LiteralPath (Join-Path $repository 'backend/neo-user-gateway.service')
    $setting = $serviceConfig | Where-Object { $_ -match '^Environment=SUPABASE_PUBLISHABLE_KEY=' } | Select-Object -First 1
    if ($setting) { $SupabasePublishableKey = $setting.Substring('Environment=SUPABASE_PUBLISHABLE_KEY='.Length) }
}
if (!$SupabasePublishableKey.StartsWith('sb_publishable_')) { throw 'A Supabase public publishable key is required.' }
[IO.Directory]::CreateDirectory($services) | Out-Null
if ($Action -eq 'Start' -and !$WatchdogRecovery) {
    [IO.File]::WriteAllText($watchdogPauseMarker, [DateTime]::UtcNow.ToString('o'))
}
if (Test-Path -LiteralPath $stopMarker) { Remove-Item -LiteralPath $stopMarker }
$settings = @{
    PYTHONUTF8='1'; PYTHONUNBUFFERED='1'; NEO_ENGINE_MODE='PAPER'; NEO_EXECUTION_MODE='PAPER';
    NEO_LOCAL_RUNTIME_ROOT=$runtime; NEO_LOCAL_STOP_FILE=$stopMarker; NEO_MARKET_ROOT=$repository;
    NEO_MONITOR_HOST='127.0.0.1'; NEO_MONITOR_PORT="$MainPort";
    NEO_USER_GATEWAY_HOST='127.0.0.1'; NEO_USER_GATEWAY_PORT="$GatewayPort";
    NEO_MARKET_UPSTREAM="http://127.0.0.1:$MainPort"; NEO_LOCAL_API="http://127.0.0.1:$MainPort/state";
    NEO_MARKET_STATE_PATH=(Join-Path $runtime 'state.json'); NEO_MARKET_AUDIT_PATH=(Join-Path $runtime 'audit.jsonl');
    NEO_LIVE_TAPE_PATH=(Join-Path $runtime 'live_tape.json'); NEO_TAPE_DB_PATH=(Join-Path $runtime 'live_tape.sqlite3');
    # Spread the existing 48-transaction body budget over four market candidates.
    # Candidate-first ranking avoids spending coverage on busy pools the entry policy would reject.
    # Pools outside this bounded set have unavailable flow until monitored.
    NEO_LAB_AUTHORIZED_CAPITAL_USD='1000';
    # Quality-first PAPER admissions; prior load-test lots retain stored exits.
    NEO_LAB_CAPACITY_TEST_ENABLED='0';
    NEO_LAB_QUALITY_ENABLED='1';
    # Owner: $250 new entries in all four; soft capacity caps off only for M/P.
    NEO_LAB_PAPER_250_ENABLED='1';
    # Owner-authorized M/P PAPER experiment; main/personal and E/U keep the veto.
    NEO_LAB_TICKER_WARNING_ENABLED='1';
    # Repair sizing/signal coupling: restore $300 buys/$100 net, keep $250 costs.
    NEO_LAB_FLOW_SIZE_DECOUPLED_ENABLED='1';
    NEO_LAB_BASE_FLOW_RESTORED_ENABLED='1';
    NEO_TAPE_MAX_PAIRS='4'; NEO_TAPE_PAGE_SIZE='1000'; NEO_TAPE_PAGES_PER_POLL='1';
    NEO_TAPE_TX_PER_POLL='48'; NEO_TAPE_HISTORICAL_TX_PER_POLL='1'; NEO_TAPE_RPC_BATCH_SIZE='20'; NEO_TAPE_RPC_TX_CONCURRENCY='4'; NEO_TAPE_POLL_SECONDS='2.0';
    NEO_USER_STATE_PATH=(Join-Path $runtime 'user_accounts.json'); NEO_USER_ENGINE_ROOT=(Join-Path $runtime 'users');
    NEO_STRATEGY_LAB_PATH=(Join-Path $runtime 'strategy_lab.json');
    NEO_STRATEGY_LAB_COMPACT_PATH=(Join-Path $runtime 'strategy_lab_compact.json');
    NEO_LAB_FUNDED_ACTIVE_ENABLED='1';
    NEO_RISK_CACHE_DIR=(Join-Path $runtime 'risk'); NEO_PRICE_CHECK_DIR=(Join-Path $runtime 'price-check');
    NEO_ENGINE_BLOCKLIST_PATH=(Join-Path $runtime 'token_blocklist.json');
    NEO_STRATEGY_LAB_RESET_FLAG=(Join-Path $runtime 'strategy_lab.reset');
    NEO_JUPITER_LOCK_PATH=(Join-Path $runtime 'quote.lock'); NEO_JUPITER_STAMP_PATH=(Join-Path $runtime 'quote-stamp.txt');
    NEO_TRAINING_ROOT=(Join-Path $runtime 'training');
    # Shared services always run the default strategy; personal engines get theirs
    # from the account registry through the gateway. An empty value removes it.
    NEO_SIGNAL_STRATEGY='';
    SUPABASE_URL='https://qziuovwcauaklgqscqys.supabase.co'; SUPABASE_PUBLISHABLE_KEY=$SupabasePublishableKey
}
$original = @{}
try {
    foreach ($name in $settings.Keys) {
        $original[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
    }
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff')
    foreach ($service in $servicesToStart) {
        $stdout = Join-Path $services "$service-$stamp.stdout.log"
        $stderr = Join-Path $services "$service-$stamp.stderr.log"
        $token = [Guid]::NewGuid().ToString('N')
        $readyFile = Join-Path $services "startup-$service-$token.json"
        Start-Process -FilePath $python -ArgumentList @('"'+$runner+'"', '--service', $service,
            '--run-token', $token, '--ready-file', '"'+$readyFile+'"') -WorkingDirectory $repository -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
        $records += Wait-WorkerRecord $service $token $readyFile $stdout $stderr
        @{mode='PAPER';runtime_root=$runtime;main_port=$MainPort;gateway_port=$GatewayPort;processes=$records} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
        Remove-Item -LiteralPath $readyFile
    }
    if (@($records | Where-Object { $null -eq (Get-OwnedProcess $_) }).Count -gt 0) {
        throw 'A PAPER worker exited before startup completed; review its stderr log.'
    }
    if ($Action -eq 'Start' -and !$WatchdogRecovery -and (Test-Path -LiteralPath $watchdogPauseMarker)) {
        Remove-Item -LiteralPath $watchdogPauseMarker
    }
} catch {
    # A startup timeout must also stop a late runner whose import has not yet
    # completed, rather than letting an unrecorded worker start afterward.
    [IO.File]::WriteAllText($stopMarker, [DateTime]::UtcNow.ToString('o'))
    throw
} finally {
    foreach ($name in $original.Keys) { [Environment]::SetEnvironmentVariable($name, $original[$name], 'Process') }
}
$records | Select-Object service,pid,stdout,stderr
} finally {
    if ($operationOwned) { $operationMutex.ReleaseMutex() }
    $operationMutex.Dispose()
}
