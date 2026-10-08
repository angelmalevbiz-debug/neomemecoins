"""Isolated PowerShell watchdog task tests; no real tasks, mutexes or services are touched."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe')


@unittest.skipUnless(os.name == 'nt' and POWERSHELL, 'Windows task supervisor')
class LocalPaperWatchdogTaskTests(unittest.TestCase):
    def run_fixture(self, body):
        with tempfile.TemporaryDirectory(prefix='neo-watchdog-task-test-') as directory:
            fixture = Path(directory)
            scripts = fixture/'scripts'
            scripts.mkdir()
            (fixture/'.runtime/accounts/services').mkdir(parents=True)
            for name in ('local_paper_watchdog.ps1', 'install_local_paper_watchdog.ps1', 'start_local_paper.ps1'):
                shutil.copyfile(ROOT/'scripts'/name, scripts/name)
            # Production may be running during local checks. Keep real mutex
            # names unique to the fixture so nothing competes with its watchdog.
            for name, mutexes in (('local_paper_watchdog.ps1', (r'Local\NeoLocalPaperWatchdog', r'Local\NeoLocalPaperOperation')),
                                  ('start_local_paper.ps1', (r'Local\NeoLocalPaperOperation',))):
                script = scripts/name
                source = script.read_text(encoding='utf-8')
                for mutex in mutexes:
                    self.assertEqual(source.count(mutex), 1, f'{name} should name {mutex} once')
                    source = source.replace(mutex, mutex + '-test-' + uuid.uuid4().hex)
                script.write_text(source, encoding='utf-8')
            controller = fixture/'controller.ps1'
            controller.write_text("param([string]$Fixture)\n$ErrorActionPreference='Stop'\n" + body, encoding='utf-8')
            result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                                     'Bypass', '-File', str(controller), '-Fixture', str(fixture)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('FIXTURE_OK', result.stdout)
            return result.stdout

    def test_scripts_parse_without_execution(self):
        self.run_fixture(r'''
foreach ($name in @('local_paper_watchdog.ps1','install_local_paper_watchdog.ps1')) {
    $tokens=$null; $errors=$null
    $null=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Fixture "scripts/$name"),[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
}
Write-Output 'FIXTURE_OK'
''')

    def test_installer_registers_logon_and_repeating_triggers_and_preserves_running_task(self):
        self.run_fixture(r'''
$global:Registered=$null; $global:RegisterCalls=0; $global:Starts=0
function Get-ScheduledTask { param($TaskName) return $global:Registered }
function Get-ScheduledTaskInfo { param($TaskName) return [pscustomobject]@{NextRunTime=(Get-Date).AddMinutes(5)} }
function New-ScheduledTaskAction { param($Execute,$Argument) return [pscustomobject]@{Execute=$Execute;Arguments=$Argument} }
function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel) return [pscustomobject]@{UserId=$UserId;LogonType=$LogonType;RunLevel=$RunLevel} }
function New-ScheduledTaskTrigger {
    param([switch]$AtLogOn,$User,[switch]$Once,$At,$RepetitionInterval,$RepetitionDuration)
    if ($AtLogOn) {
        if ($User -ne [Security.Principal.WindowsIdentity]::GetCurrent().Name) { throw 'Logon trigger is not scoped to the current user' }
        return [pscustomobject]@{Kind='logon';User=$User}
    }
    if (!$Once -or $null -eq $At -or $At -gt (Get-Date).AddMinutes(1)) { throw 'Repeating trigger must be a Once trigger starting now' }
    if ($RepetitionInterval -ne (New-TimeSpan -Minutes 5) -or $PSBoundParameters.ContainsKey('RepetitionDuration')) { throw 'Repetition must be every 5 minutes without an end' }
    return [pscustomobject]@{Kind='repeat';Interval=$RepetitionInterval}
}
function New-ScheduledTaskSettingsSet {
    param([switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,[switch]$StartWhenAvailable,$ExecutionTimeLimit,$MultipleInstances,$RestartCount,$RestartInterval)
    if (!$AllowStartIfOnBatteries -or !$DontStopIfGoingOnBatteries -or !$StartWhenAvailable -or $ExecutionTimeLimit -ne [TimeSpan]::Zero -or $MultipleInstances -ne 'IgnoreNew') { throw 'Unexpected task settings' }
    return [pscustomobject]@{RestartCount=$RestartCount}
}
function Register-ScheduledTask {
    param($TaskName,$Action,$Trigger,$Principal,$Settings,$Description,[switch]$Force)
    $global:RegisterCalls++
    if ($PSBoundParameters.ContainsKey('Password')) { throw 'Stored password' }
    $triggers=@($Trigger)
    if ($triggers.Count -ne 2 -or @($triggers | Where-Object Kind -eq 'logon').Count -ne 1 -or @($triggers | Where-Object Kind -eq 'repeat').Count -ne 1) { throw 'Expected exactly one logon and one repeating trigger' }
    if ($Principal.RunLevel -ne 'Limited' -or $Principal.LogonType -ne 'Interactive' -or $Principal.UserId -ne [Security.Principal.WindowsIdentity]::GetCurrent().Name) { throw 'Unexpected principal' }
    if ($Action.Arguments -notmatch '-WindowStyle Hidden' -or $Action.Arguments -notmatch 'local_paper_watchdog.ps1') { throw 'Unexpected task action' }
    # Re-registration updates the definition; a running instance keeps running.
    $state='Ready'
    if ($null -ne $global:Registered -and $global:Registered.State -eq 'Running') { $state='Running' }
    $global:Registered=[pscustomobject]@{TaskName=$TaskName;Actions=@($Action);Principal=$Principal;Settings=$Settings;Triggers=$triggers;State=$state}
    return $global:Registered
}
function Start-ScheduledTask { param($TaskName) $global:Starts++; $global:Registered.State='Running' }
function Start-Sleep { param($Seconds, $Milliseconds) }
$summary = & (Join-Path $Fixture 'scripts/install_local_paper_watchdog.ps1')
if ($global:RegisterCalls -ne 1 -or $global:Starts -ne 1) { throw 'Installer did not register once and start the idle task once' }
if ($summary -notmatch 'state: Running' -or $summary -notmatch 'every 5 min') { throw "Unexpected summary: $summary" }
& (Join-Path $Fixture 'scripts/install_local_paper_watchdog.ps1') | Out-Null
if ($global:RegisterCalls -ne 2 -or $global:Starts -ne 1) { throw 'Re-installation restarted a running watchdog instead of only updating the definition' }
Write-Output 'FIXTURE_OK'
''')

    def test_probe_reports_services_and_gateway_owned_engines_read_only(self):
        output = self.run_fixture(r'''
$runner = Join-Path (Join-Path $Fixture 'scripts') 'local_paper_service.py'
$created = [DateTime]::UtcNow
function Make-Process([int]$ProcessId,[int]$Parent,[string]$Arguments) {
    return [pscustomobject]@{ProcessId=$ProcessId;ParentProcessId=$Parent;Name='python.exe';CreationDate=$created;CommandLine=('"python.exe" "'+$runner+'"'+$Arguments)}
}
$global:Processes = @(
    (Make-Process 101 1 ' --service main --run-token aaaa'),
    (Make-Process 102 1 ' --service tape --run-token bbbb'),
    (Make-Process 103 1 ' --service lab --run-token cccc'),
    (Make-Process 104 1 ' --service gateway --run-token dddd'),
    (Make-Process 201 104 ''),   # venv launcher spawned by the gateway
    (Make-Process 202 201 ''),   # engine worker, listening
    (Make-Process 301 999 '')    # foreign runner, not gateway-owned
)
function Get-CimInstance {
    param($ClassName,$Filter)
    if ($Filter -match '^ProcessId=(\d+)$') { return @($global:Processes | Where-Object ProcessId -eq ([int]$Matches[1])) }
    if ($Filter -match 'local_paper_service') { return $global:Processes }
    throw "Unexpected CIM query: $Filter"
}
function Get-NetTCPConnection {
    param($State,$OwningProcess,$LocalPort)
    $all = @([pscustomobject]@{LocalPort=8878;OwningProcess=101},[pscustomobject]@{LocalPort=8879;OwningProcess=104},[pscustomobject]@{LocalPort=18800;OwningProcess=202})
    if ($null -ne $OwningProcess) { return @($all | Where-Object OwningProcess -eq $OwningProcess) }
    return $all
}
function Test-NetConnection { param($ComputerName,$Port,$InformationLevel) return $true }
$records = foreach ($p in $global:Processes | Where-Object { $_.CommandLine -match '--service (\w+) --run-token (\w+)' }) {
    $null = $p.CommandLine -match '--service (\w+) --run-token (\w+)'
    @{service=$Matches[1];pid=$p.ProcessId;run_token=$Matches[2];created_at=$created.ToString('o');stdout='';stderr=''}
}
@{mode='PAPER';processes=@($records)} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $Fixture '.runtime/accounts/services/processes.json') -Encoding UTF8
$rows = @(& (Join-Path $Fixture 'scripts/local_paper_watchdog.ps1') -Probe)
if ($LASTEXITCODE -ne 0) { throw 'Probe did not exit 0' }
if (Test-Path -LiteralPath (Join-Path $Fixture '.runtime/accounts/services/watchdog.log')) { throw 'Probe wrote the watchdog log' }
foreach ($name in @('main','tape','lab','gateway')) {
    $row = @($rows | Where-Object service -eq $name)
    if ($row.Count -ne 1 -or !$row[0].running -or $row[0].owner -ne 'watchdog') { throw "Service $name was not reported as running" }
}
if (-not (@($rows | Where-Object service -eq 'main')[0].listening) -or -not (@($rows | Where-Object service -eq 'gateway')[0].listening)) { throw 'Core listening state missing' }
$engines = @($rows | Where-Object service -eq 'engine')
if ($engines.Count -ne 1 -or $engines[0].pid -ne 202 -or $engines[0].port -ne '18800' -or !$engines[0].listening -or $engines[0].owner -ne 'gateway') { throw 'Gateway-owned engine worker was not reported exactly once' }
$rows | Format-Table -AutoSize | Out-String -Width 200 | Write-Output
Write-Output 'FIXTURE_OK'
''')
        self.assertIn('engine', output)


if __name__ == '__main__':
    unittest.main()
