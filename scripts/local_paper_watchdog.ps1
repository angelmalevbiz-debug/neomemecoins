param(
    [int]$CheckIntervalSeconds = 20,
    [int]$MainPort = 8878,
    [int]$GatewayPort = 8879
)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$startScript = Join-Path $PSScriptRoot 'start_local_paper.ps1'
$services = Join-Path $repository '.runtime/accounts/services'
$pauseMarker = Join-Path $services 'watchdog.pause'
$logPath = Join-Path $services 'watchdog.log'
$mutex = [Threading.Mutex]::new($false, 'Local\NeoLocalPaperWatchdog')
$operationMutex = [Threading.Mutex]::new($false, 'Local\NeoLocalPaperOperation')

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
