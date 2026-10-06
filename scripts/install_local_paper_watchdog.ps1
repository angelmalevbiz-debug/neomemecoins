param([switch]$Remove)
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

$action = New-ScheduledTaskAction -Execute $powershell -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watchdog`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'Keeps local NEO PAPER services available and recovers owned processes after failures.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 2
$task = Get-ScheduledTask -TaskName $taskName
Write-Output "Task: $($task.TaskName); state: $($task.State); user: $userId; logon trigger: enabled; run level: limited."
