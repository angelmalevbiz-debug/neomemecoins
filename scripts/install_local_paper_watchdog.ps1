param([switch]$Remove, [int]$RepeatMinutes = 5)
$ErrorActionPreference = 'Stop'
$taskName = 'NEO Local PAPER Watchdog'
$watchdog = Join-Path $PSScriptRoot 'local_paper_watchdog.ps1'
$powershell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed scheduled task: $taskName"
    exit 0
}
if ($RepeatMinutes -lt 1) { throw 'RepeatMinutes must be at least 1.' }
if (!(Test-Path -LiteralPath $watchdog)) { throw 'Watchdog script is missing; run the installer from the checkout that owns the PAPER services.' }

$action = New-ScheduledTaskAction -Execute $powershell -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watchdog`""
# The logon trigger brings the watchdog up with the session. The repeating
# trigger re-launches it after any exit: a hidden console that is closed or
# sent Ctrl+C ends with 0xC000013A, which Task Scheduler's restart-on-failure
# did not re-launch (2026-10-07). IgnoreNew makes a tick a no-op while an
# instance is still running, and the watchdog's own named mutex guards the rest.
$logon = New-ScheduledTaskTrigger -AtLogOn -User $userId
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes $RepeatMinutes)
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($logon, $repeat) -Principal $principal `
    -Settings $settings -Description 'Keeps local NEO PAPER services available and recovers owned processes after failures.' -Force | Out-Null
$task = Get-ScheduledTask -TaskName $taskName
if ($task.State -ne 'Running') {
    # A running watchdog keeps its instance; re-registration only updates the definition.
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 2
    $task = Get-ScheduledTask -TaskName $taskName
}
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Output "Task: $($task.TaskName); state: $($task.State); user: $userId; triggers: at logon + every $RepeatMinutes min (IgnoreNew); run level: limited; next run: $($info.NextRunTime)."
