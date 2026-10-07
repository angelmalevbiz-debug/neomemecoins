param([ValidateSet('Start','Stop','Status')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtime = Join-Path $repository '.runtime/dashboard'
$manifestPath = Join-Path $runtime 'process.json'
$vite = Join-Path $repository 'node_modules/vite/bin/vite.js'

function Get-OwnedDashboard {
    if (!(Test-Path -LiteralPath $manifestPath)) { return $null }
    $record = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)" -ErrorAction SilentlyContinue
    if ($process -and $process.CommandLine.Contains($vite) -and
        [Math]::Abs(($process.CreationDate.ToUniversalTime() - ([DateTime]$record.created_at).ToUniversalTime()).TotalSeconds) -lt 2) {
        return $process
    }
    return $null
}

$owned = Get-OwnedDashboard
if ($Action -eq 'Status') {
    [pscustomobject]@{running=($null -ne $owned); url='http://127.0.0.1:5173'; backend='http://127.0.0.1:8879'}
    exit 0
}
if ($Action -eq 'Stop') {
    if ($owned) { Stop-Process -Id $owned.ProcessId -ErrorAction Stop }
    Write-Output 'Owned local dashboard stopped.'
    exit 0
}
if ($owned) { Write-Output 'Local dashboard is already running at http://127.0.0.1:5173'; exit 0 }
if (!(Test-Path -LiteralPath $vite)) { throw 'Run npm ci in this checkout before starting the dashboard.' }
if (Get-NetTCPConnection -State Listen -LocalPort 5173 -ErrorAction SilentlyContinue) {
    throw 'Port 5173 is occupied; no existing process was stopped.'
}
$null = Invoke-RestMethod -Uri 'http://127.0.0.1:8879/user/health' -TimeoutSec 10
$node = (Get-Command node -ErrorAction Stop).Source
$null = [IO.Directory]::CreateDirectory($runtime)
$previousApi = [Environment]::GetEnvironmentVariable('VITE_NEO_API_URL','Process')
try {
    [Environment]::SetEnvironmentVariable('VITE_NEO_API_URL','http://127.0.0.1:8879','Process')
    $process = Start-Process -FilePath $node -ArgumentList @(('"' + $vite + '"'),'--host','127.0.0.1','--port','5173','--strictPort') -WorkingDirectory $repository -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'stdout.log') -RedirectStandardError (Join-Path $runtime 'stderr.log')
} finally {
    [Environment]::SetEnvironmentVariable('VITE_NEO_API_URL',$previousApi,'Process')
}
$record = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.Id)"
[pscustomobject]@{pid=$process.Id; created_at=$record.CreationDate.ToUniversalTime().ToString('o'); url='http://127.0.0.1:5173'; backend='http://127.0.0.1:8879'} | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding utf8
for ($attempt=0; $attempt -lt 40; $attempt++) {
    if ($process.HasExited) { throw 'Dashboard exited; inspect .runtime/dashboard/stderr.log.' }
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:5173' -TimeoutSec 2 -UseBasicParsing
        if ($response.StatusCode -eq 200) { Write-Output 'Local dashboard ready at http://127.0.0.1:5173 (local PAPER backend).'; exit 0 }
    } catch { }
    Start-Sleep -Milliseconds 250
}
throw 'Dashboard readiness timed out; inspect .runtime/dashboard logs.'
