param([ValidateSet('Start','Stop','Status')][string]$Action='Start')
$ErrorActionPreference='Stop'
$repository=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtime=Join-Path $repository '.runtime/tunnel'
$manifestPath=Join-Path $runtime 'process.json'
$cloudflared='C:\Program Files (x86)\cloudflared\cloudflared.exe'
$origin='http://127.0.0.1:8879'
function Get-OwnedTunnel {
    if (!(Test-Path -LiteralPath $manifestPath)) { return $null }
    $record=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $process=Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)" -ErrorAction SilentlyContinue
    if ($process -and $process.Name -eq 'cloudflared.exe' -and
        $process.CommandLine -and $process.CommandLine.Contains($origin) -and
        [Math]::Abs(($process.CreationDate.ToUniversalTime()-([DateTime]$record.created_at).ToUniversalTime()).TotalSeconds) -lt 2) {
        return $process
    }
    return $null
}
$owned=Get-OwnedTunnel
if ($Action -eq 'Status') {
    $record=if (Test-Path -LiteralPath $manifestPath) { Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json } else { $null }
    [pscustomobject]@{running=($null -ne $owned); backend=$record.public_url; origin=$origin}
    exit 0
}
if ($Action -eq 'Stop') {
    if ($owned) { Stop-Process -Id $owned.ProcessId -ErrorAction Stop }
    Write-Output 'Owned gateway tunnel stopped.'
    exit 0
}
$stdout=Join-Path $runtime 'stdout.log'
$stderr=Join-Path $runtime 'stderr.log'
if ($owned) {
    $record=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($record.public_url) { Get-Content -LiteralPath $manifestPath -Raw; exit 0 }
    # A previous hostname wait may have timed out while this process kept
    # connecting. Recover its URL from the existing log without replacing it.
    $process=Get-Process -Id $owned.ProcessId -ErrorAction Stop
} else {
    if (!(Test-Path -LiteralPath $cloudflared)) { throw 'Cloudflared is not installed at the expected official vendor path.' }
    $null=Invoke-RestMethod -Uri ($origin+'/user/health') -TimeoutSec 10
    $null=[IO.Directory]::CreateDirectory($runtime)
    $process=Start-Process -FilePath $cloudflared -ArgumentList @('tunnel','--no-autoupdate','--protocol','http2','--url',$origin) -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $instance=Get-CimInstance Win32_Process -Filter "ProcessId=$($process.Id)"
    $record=[ordered]@{pid=$process.Id; created_at=$instance.CreationDate.ToUniversalTime().ToString('o'); origin=$origin; public_url=$null; temporary_hostname=$true}
    $record | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding utf8
}
for ($attempt=0; $attempt -lt 120; $attempt++) {
    if ($process.HasExited) { throw 'Tunnel exited; inspect .runtime/tunnel/stderr.log.' }
    $log=Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue
    $match=[regex]::Match([string]$log,'https://[a-z0-9-]+\.trycloudflare\.com')
    if ($match.Success) {
        $record.public_url=$match.Value
        $record | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding utf8
        Write-Output $match.Value
        exit 0
    }
    Start-Sleep -Milliseconds 250
}
throw 'Tunnel hostname was not received; inspect .runtime/tunnel logs.'
