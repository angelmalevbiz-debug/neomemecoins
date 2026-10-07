param([switch]$Start)
$ErrorActionPreference = 'Stop'
$taskName = 'NEO Local Gateway Tunnel'
$runner = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'run_local_tunnel_task.ps1'))
$powershell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$userId = $identity.Name
$arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`""
if (!(Test-Path -LiteralPath $runner)) { throw 'Tunnel supervisor script is missing.' }

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    $principalId = [string]$existing.Principal.UserId
    $principalSid = if ($principalId.StartsWith('S-1-')) { $principalId } else {
        ([Security.Principal.NTAccount]::new($principalId)).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    $actions = @($existing.Actions)
    $triggers = @($existing.Triggers | Where-Object { $null -ne $_ })
    if ($principalSid -ne $identity.User.Value -or $existing.Principal.RunLevel -ne 'Limited' -or
        $existing.Principal.LogonType -ne 'Interactive' -or $actions.Count -ne 1 -or
        $actions[0].Execute -ine $powershell -or $actions[0].Arguments -cne $arguments -or
        $triggers.Count -ne 0 -or $existing.Settings.RestartCount -gt 0) {
        throw 'An unexpected scheduled task uses this name; no task was changed.'
    }
    if ($existing.State -eq 'Running') {
        Write-Output "Task: $taskName; already running; existing tunnel preserved."
        exit 0
    }
}
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
# On demand only: no logon/time triggers or automatic failure restarts that could
# rotate the public hostname without an explicit verified Pages deployment.
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings `
    -Description 'Owns the local authenticated gateway tunnel outside Codex tool jobs; on-demand start only.' `
    -Force | Out-Null
if ($Start) { Start-ScheduledTask -TaskName $taskName }
Write-Output "Task: $taskName; user: $userId; limited interactive run; no automatic triggers or restarts."
