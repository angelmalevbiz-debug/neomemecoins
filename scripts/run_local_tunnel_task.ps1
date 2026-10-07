param()
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtime = Join-Path $repository '.runtime/tunnel'
$manifestPath = Join-Path $runtime 'process.json'
$launcher = Join-Path $PSScriptRoot 'start_local_tunnel.ps1'
$cloudflared = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$origin = 'http://127.0.0.1:8879'
$mutex = [Threading.Mutex]::new($false, 'Local\NeoLocalGatewayTunnelTask')
$owned = $false

function Write-TunnelLog([string]$Message) {
    [IO.Directory]::CreateDirectory($runtime) | Out-Null
    Add-Content -LiteralPath (Join-Path $runtime 'supervisor.log') `
        -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message" -Encoding UTF8
}

function Read-TunnelRecord {
    if (!(Test-Path -LiteralPath $manifestPath)) { return $null }
    $record = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if (($record.pid -isnot [int] -and $record.pid -isnot [long]) -or
        $record.pid -le 0 -or $record.pid -gt [int]::MaxValue -or
        $record.origin -cne $origin -or $record.temporary_hostname -isnot [bool] -or
        !$record.temporary_hostname -or !$record.created_at) {
        throw 'Tunnel manifest identity is invalid; no process was changed.'
    }
    $null = ([DateTime]$record.created_at).ToUniversalTime()
    if ($record.public_url -and $record.public_url -cnotmatch '^https://[a-z0-9-]+\.trycloudflare\.com$') {
        throw 'Tunnel manifest public hostname is invalid; no process was changed.'
    }
    return $record
}

function Find-RecordedTunnel($record) {
    if ($null -eq $record) { return $null }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)" -ErrorAction Stop
    if ($null -eq $process) { return $null } # A dead previous instance may be explicitly restarted.
    $command = '^"?' + [regex]::Escape($cloudflared) + '"?\s+tunnel\s+--no-autoupdate\s+--protocol\s+http2\s+--url\s+"?' + [regex]::Escape($origin) + '"?\s*$'
    if ($process.Name -ine 'cloudflared.exe' -or !$process.ExecutablePath -or
        ![string]::Equals([IO.Path]::GetFullPath($process.ExecutablePath), $cloudflared,
                         [StringComparison]::OrdinalIgnoreCase) -or
        !$process.CommandLine -or $process.CommandLine -notmatch $command -or
        [Math]::Abs(($process.CreationDate.ToUniversalTime() -
                    ([DateTime]$record.created_at).ToUniversalTime()).TotalSeconds) -ge 2) {
        throw 'Recorded PID belongs to an unexpected process; no process was changed.'
    }
    return $process
}

function Find-GatewayTunnels {
    $pattern = '(?:^|\s)--url(?:=|\s+)["'']?' + [regex]::Escape($origin) + '["'']?(?=\s|$)'
    return @(Get-CimInstance Win32_Process -Filter "name='cloudflared.exe'" -ErrorAction Stop |
        Where-Object { $_.CommandLine -and $_.CommandLine -match $pattern })
}

try {
    try { $owned = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $owned = $true }
    if (!$owned) { throw 'A tunnel supervisor is already running; no process was changed.' }
    $previous = Read-TunnelRecord
    $running = Find-RecordedTunnel $previous
    if ($running) {
        # Waiting for an existing process does not remove its old Windows job.
        # An explicit reviewed cutover must stop it with the existing launcher.
        throw 'An owned tunnel is already running. Stop that owned tunnel before moving it to Task Scheduler.'
    }
    if (@(Find-GatewayTunnels).Count -ne 0) {
        throw 'An unrecorded tunnel already targets this gateway; no process was changed.'
    }
    Write-TunnelLog 'Starting an explicitly requested gateway tunnel from Task Scheduler.'
    & $launcher -Action Start | Out-Null
    $record = Read-TunnelRecord
    $instance = Find-RecordedTunnel $record
    if ($null -eq $instance -or $instance.ParentProcessId -ne $PID -or !$record.public_url) {
        throw 'Launcher did not produce this supervisor''s verified owned tunnel.'
    }
    $duplicates = @(Find-GatewayTunnels)
    if ($duplicates.Count -ne 1 -or $duplicates[0].ProcessId -ne $record.pid) {
        throw 'Gateway tunnel ownership is ambiguous; no process was stopped.'
    }
    $process = Get-Process -Id $record.pid -ErrorAction Stop
    if ([Math]::Abs(($process.StartTime.ToUniversalTime() -
                    ([DateTime]$record.created_at).ToUniversalTime()).TotalSeconds) -ge 2) {
        throw 'Tunnel identity changed before supervision; no process was stopped.'
    }
    Write-TunnelLog "Supervising owned PID $($record.pid); hostname $($record.public_url)."
    # Hold a handle to the actual owned process, keeping the scheduled task alive.
    # Core PAPER Stop markers do not affect this supervisor. No automatic restart
    # changes the temporary hostname or publishes a new Pages backend URL.
    $process.WaitForExit()
    Write-TunnelLog 'Owned tunnel exited. Explicit task start and Pages URL verification are required.'
    exit 1
} catch {
    Write-TunnelLog $_.Exception.Message
    throw
} finally {
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
