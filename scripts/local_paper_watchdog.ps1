param(
    [int]$CheckIntervalSeconds = 20,
    [int]$MainPort = 8878,
    [int]$GatewayPort = 8879,
    [switch]$Probe
)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$startScript = Join-Path $PSScriptRoot 'start_local_paper.ps1'
$runner = Join-Path $PSScriptRoot 'local_paper_service.py'
$services = Join-Path $repository '.runtime/accounts/services'
$pauseMarker = Join-Path $services 'watchdog.pause'
$logPath = Join-Path $services 'watchdog.log'

function Write-Log([string]$Message) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Get-Status {
    $items = @(& $startScript -Action Status)
    return @($items | Where-Object { $null -ne $_ -and $null -ne $_.PSObject.Properties['service'] })
}

function Test-Listening([int]$Port) {
    $connection = Test-NetConnection -ComputerName '127.0.0.1' -Port $Port `
        -InformationLevel Quiet -WarningAction SilentlyContinue
    return [bool]$connection
}

function Get-ListeningPorts([int]$ProcessId) {
    return @(Get-NetTCPConnection -State Listen -OwningProcess $ProcessId -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty LocalPort | Sort-Object -Unique)
}

function Get-EngineReport([int]$GatewayPid) {
    # Per-user engines are spawned, health-checked and revived by the owned
    # gateway (backend/user_gateway.py). They run the same runner without a
    # --service argument and have no manifest record, so the watchdog reports
    # them for visibility and never starts or stops one itself.
    if ($GatewayPid -le 0) { return @() }
    $runners = @(Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%local_paper_service.py%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$runner*" -and $_.CommandLine -notmatch '(?:^|\s)--service\s' })
    $byPid = @{}
    $launchers = @{}
    foreach ($process in $runners) {
        $byPid[[int]$process.ProcessId] = $process
        $launchers[[int]$process.ParentProcessId] = $true
    }
    $report = @()
    foreach ($process in $runners) {
        # The venv python.exe launcher re-executes the runner; report the worker only.
        if ($launchers.ContainsKey([int]$process.ProcessId)) { continue }
        $ancestor = [int]$process.ParentProcessId
        $owned = $false
        for ($depth = 0; $depth -lt 4 -and $ancestor -gt 0; $depth++) {
            if ($ancestor -eq $GatewayPid) { $owned = $true; break }
            if (!$byPid.ContainsKey($ancestor)) { break }
            $ancestor = [int]$byPid[$ancestor].ParentProcessId
        }
        if (!$owned) { continue }
        $report += [pscustomobject]@{ pid = [int]$process.ProcessId; ports = @(Get-ListeningPorts ([int]$process.ProcessId)) }
    }
    return @($report | Sort-Object pid)
}

function Get-EngineSignature($Engines) {
    return (@($Engines | ForEach-Object { "$($_.pid):$($_.ports -join '/')" }) -join ' ')
}

if ($Probe) {
    # Read-only one-shot report of what the loop supervises and observes:
    # no log line, no lock, and nothing is started or stopped.
    $status = @(Get-Status)
    $rows = @(foreach ($record in $status) {
        $port = $null
        if ($record.service -eq 'main') { $port = $MainPort } elseif ($record.service -eq 'gateway') { $port = $GatewayPort }
        $listening = $null
        if ($null -ne $port) { $listening = [bool]$record.running -and (Test-Listening $port) }
        [pscustomobject]@{ service = $record.service; owner = 'watchdog'; pid = $record.pid
                           running = [bool]$record.running; port = $port; listening = $listening }
    })
    $gateway = @($status | Where-Object { $_.service -eq 'gateway' -and $_.running })
    $gatewayPid = 0
    if ($gateway.Count -eq 1) { $gatewayPid = [int]$gateway[0].pid }
    $rows += @(foreach ($engine in @(Get-EngineReport $gatewayPid)) {
        [pscustomobject]@{ service = 'engine'; owner = 'gateway'; pid = $engine.pid; running = $true
                           port = ($engine.ports -join ','); listening = ($engine.ports.Count -gt 0) }
    })
    $rows
    exit 0
}

$mutex = [Threading.Mutex]::new($false, 'Local\NeoLocalPaperWatchdog')
$operationMutex = [Threading.Mutex]::new($false, 'Local\NeoLocalPaperOperation')

function Invoke-Start([string]$Action) {
    Write-Log "Recovery: requesting $Action."
    & $startScript -Action $Action -MainPort $MainPort -GatewayPort $GatewayPort -WatchdogRecovery | Out-Null
}

$watchdogOwned = $false
try { $watchdogOwned = $mutex.WaitOne(0) }
catch [Threading.AbandonedMutexException] { $watchdogOwned = $true }
if (!$watchdogOwned) {
    $operationMutex.Dispose()
    $mutex.Dispose()
    exit 0
}
try {
    [IO.Directory]::CreateDirectory($services) | Out-Null
    Write-Log 'Watchdog started in PAPER-only mode.'
    $engineSignature = $null
    while ($true) {
        $operationOwned = $false
        try {
            # Acquire before the probe, not merely around Stop/Start: a decision
            # made from an earlier partial manifest must never stop a new cohort.
            try { $operationOwned = $operationMutex.WaitOne(1000) }
            catch [Threading.AbandonedMutexException] { $operationOwned = $true }
            if (!$operationOwned) { continue }
            if (Test-Path -LiteralPath $pauseMarker) {
                continue
            }

            $status = @(Get-Status)
            $required = @('main', 'tape', 'lab', 'gateway')
            $counts = @{}
            foreach ($name in $required) {
                $counts[$name] = @($status | Where-Object service -eq $name)
            }
            $manifestValid = @($required | Where-Object { $counts[$_].Count -ne 1 }).Count -eq 0
            $ownedRunning = @($status | Where-Object { $_.running -eq $true })
            $coreOwned = $manifestValid -and $counts['main'][0].running -and $counts['gateway'][0].running
            $coreListening = $coreOwned -and (Test-Listening $MainPort) -and (Test-Listening $GatewayPort)
            $allServices = $manifestValid -and @($required | Where-Object { !$counts[$_][0].running }).Count -eq 0

            # Observe-only: gateway-owned per-user engines, logged when the set changes.
            $gatewayPid = 0
            if ($coreOwned) { $gatewayPid = [int]$counts['gateway'][0].pid }
            $engines = @(Get-EngineReport $gatewayPid)
            $signature = Get-EngineSignature $engines
            if ($signature -ne $engineSignature) {
                $engineSignature = $signature
                $detail = 'none'
                if ($signature) { $detail = $signature }
                Write-Log "Per-user engines (gateway-owned, observe-only): $($engines.Count) running; pid:ports $detail."
            }

            if ($allServices -and $coreListening) {
                continue
            }

            if ($coreListening) {
                Invoke-Start 'StartMissing'
            } elseif ($ownedRunning.Count -gt 0) {
                Write-Log 'A core service is missing or not listening; gracefully stopping owned PAPER services before restart.'
                & $startScript -Action Stop -MainPort $MainPort -GatewayPort $GatewayPort -WatchdogRecovery | Out-Null
                Invoke-Start 'Start'
            } else {
                Invoke-Start 'Start'
            }
        } catch {
            Write-Log "Recovery check failed safely: $($_.Exception.Message)"
        } finally {
            if ($operationOwned) { $operationMutex.ReleaseMutex() }
            # Finally also runs for continue, so healthy/paused loops release
            # the operation lock before sleeping and do not poll continuously.
            Start-Sleep -Seconds $CheckIntervalSeconds
        }
    }
} finally {
    Write-Log 'Watchdog stopped.'
    $mutex.ReleaseMutex()
    $mutex.Dispose()
    $operationMutex.Dispose()
}
