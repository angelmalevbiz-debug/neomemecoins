"""Isolated PowerShell supervisor tests; no real tasks or tunnels are touched."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe')


@unittest.skipUnless(os.name == 'nt' and POWERSHELL, 'Windows task supervisor')
class LocalTunnelTaskTests(unittest.TestCase):
    def run_fixture(self, body, *, starter='throw "Unexpected real launcher call"'):
        with tempfile.TemporaryDirectory(prefix='neo-tunnel-task-test-') as directory:
            fixture = Path(directory)
            scripts = fixture/'scripts'
            scripts.mkdir()
            (fixture/'.runtime/tunnel').mkdir(parents=True)
            for name in ('run_local_tunnel_task.ps1', 'install_local_tunnel_task.ps1'):
                shutil.copyfile(ROOT/'scripts'/name, scripts/name)
            (scripts/'start_local_tunnel.ps1').write_text(starter, encoding='utf-8')
            controller = fixture/'controller.ps1'
            controller.write_text("param([string]$Fixture)\n$ErrorActionPreference='Stop'\n" + body, encoding='utf-8')
            result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                                     'Bypass', '-File', str(controller), '-Fixture', str(fixture)],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('FIXTURE_OK', result.stdout)

    def test_scripts_parse_without_execution(self):
        self.run_fixture(r'''
foreach ($name in @('run_local_tunnel_task.ps1','install_local_tunnel_task.ps1')) {
    $tokens=$null; $errors=$null
    $null=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Fixture "scripts/$name"),[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
}
Write-Output 'FIXTURE_OK'
''')

    def test_installer_is_limited_on_demand_and_preserves_running_task(self):
        self.run_fixture(r'''
$global:Registered=$null; $global:RegisterCalls=0; $global:Starts=0
function Get-ScheduledTask { param($TaskName) return $global:Registered }
function New-ScheduledTaskAction { param($Execute,$Argument) return [pscustomobject]@{Execute=$Execute;Arguments=$Argument} }
function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel) return [pscustomobject]@{UserId=$UserId;LogonType=$LogonType;RunLevel=$RunLevel} }
function New-ScheduledTaskSettingsSet {
    param([switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,$ExecutionTimeLimit,$MultipleInstances)
    if (!$AllowStartIfOnBatteries -or !$DontStopIfGoingOnBatteries -or $ExecutionTimeLimit -ne [TimeSpan]::Zero -or $MultipleInstances -ne 'IgnoreNew') { throw 'Unexpected task settings' }
    return [pscustomobject]@{RestartCount=0}
}
function Register-ScheduledTask {
    param($TaskName,$Action,$Principal,$Settings,$Description,[switch]$Force)
    $global:RegisterCalls++
    if ($PSBoundParameters.ContainsKey('Trigger') -or $PSBoundParameters.ContainsKey('Password')) { throw 'Automatic trigger or stored password' }
    if ($Principal.RunLevel -ne 'Limited' -or $Principal.LogonType -ne 'Interactive' -or $Principal.UserId -ne [Security.Principal.WindowsIdentity]::GetCurrent().Name) { throw 'Unexpected principal' }
    if ($Action.Arguments -notmatch '-WindowStyle Hidden' -or $Action.Arguments -notmatch 'run_local_tunnel_task.ps1') { throw 'Unexpected task action' }
    $global:Registered=[pscustomobject]@{TaskName=$TaskName;Actions=@($Action);Principal=$Principal;Settings=$Settings;Triggers=@();State='Ready'}
    return $global:Registered
}
function Start-ScheduledTask { param($TaskName) $global:Starts++ }
& (Join-Path $Fixture 'scripts/install_local_tunnel_task.ps1')
if ($global:RegisterCalls -ne 1 -or $global:Starts -ne 0) { throw 'Installer started the task without a request' }
$global:Registered.State='Running'
& (Join-Path $Fixture 'scripts/install_local_tunnel_task.ps1')
if ($global:RegisterCalls -ne 1 -or $global:Starts -ne 0) { throw 'Running task was replaced or restarted' }
$global:Registered.Actions[0].Arguments='different script'
$refused=$false
try { & (Join-Path $Fixture 'scripts/install_local_tunnel_task.ps1') } catch { $refused=$_.Exception.Message -like 'An unexpected scheduled task*' }
if (!$refused -or $global:RegisterCalls -ne 1) { throw 'Conflicting scheduled task was not refused' }
Write-Output 'FIXTURE_OK'
''')

    def test_existing_mismatched_and_duplicate_processes_are_refused(self):
        self.run_fixture(r'''
$cloud='C:\Program Files (x86)\cloudflared\cloudflared.exe'
$origin='http://127.0.0.1:8879'
$global:Candidate=$null
function Get-CimInstance { param($ClassName,$Filter) return $global:Candidate }
$manifest=Join-Path $Fixture '.runtime/tunnel/process.json'
$created=[DateTime]::UtcNow
$record=@{pid=54321;created_at=$created.ToString('o');origin=$origin;public_url='https://fixture.trycloudflare.com';temporary_hostname=$true}
foreach ($mode in @('live','wrong-path','wrong-time','wrong-origin','duplicate')) {
    $global:Candidate=[pscustomobject]@{ProcessId=54321;ParentProcessId=999;Name='cloudflared.exe';ExecutablePath=$cloud;CreationDate=$created;CommandLine=('"'+$cloud+'" tunnel --no-autoupdate --protocol http2 --url '+$origin)}
    $record | ConvertTo-Json | Set-Content -LiteralPath $manifest -Encoding UTF8
    if ($mode -eq 'wrong-path') { $global:Candidate.ExecutablePath='C:\unexpected\cloudflared.exe' }
    if ($mode -eq 'wrong-time') { $global:Candidate.CreationDate=$created.AddMinutes(1) }
    if ($mode -eq 'wrong-origin') { $global:Candidate.CommandLine=$global:Candidate.CommandLine+'0' }
    if ($mode -eq 'duplicate') { Remove-Item -LiteralPath $manifest }
    $refused=$false
    try { & (Join-Path $Fixture 'scripts/run_local_tunnel_task.ps1') }
    catch { $refused=$_.Exception.Message -match 'already running|unexpected process|unrecorded tunnel' }
    if (!$refused) { throw "Unsafe process handling for $mode" }
}
Write-Output 'FIXTURE_OK'
''')

    def test_supervisor_waits_for_actual_owned_process(self):
        starter = r'''
param($Action)
$global:Child=Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 2') -WindowStyle Hidden -PassThru
$cloud='C:\Program Files (x86)\cloudflared\cloudflared.exe'
$global:Candidate=[pscustomobject]@{ProcessId=$global:Child.Id;ParentProcessId=$PID;Name='cloudflared.exe';ExecutablePath=$cloud;CreationDate=$global:Child.StartTime;CommandLine=('"'+$cloud+'" tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:8879')}
@{pid=$global:Child.Id;created_at=$global:Child.StartTime.ToUniversalTime().ToString('o');origin='http://127.0.0.1:8879';public_url='https://fixture.trycloudflare.com';temporary_hostname=$true} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot '../.runtime/tunnel/process.json') -Encoding UTF8
'''
        self.run_fixture(r'''
$global:Candidate=$null; $global:Child=$null
function Get-CimInstance { param($ClassName,$Filter) return $global:Candidate }
$timer=[Diagnostics.Stopwatch]::StartNew()
& (Join-Path $Fixture 'scripts/run_local_tunnel_task.ps1')
$timer.Stop()
if ($timer.ElapsedMilliseconds -lt 1800 -or !$global:Child.HasExited) { throw 'Supervisor did not wait for its actual process' }
$log=Get-Content -LiteralPath (Join-Path $Fixture '.runtime/tunnel/supervisor.log') -Raw
if ($log -notmatch 'Supervising owned PID' -or $log -notmatch 'Owned tunnel exited') { throw 'Supervisor lifecycle was not recorded' }
if ($LASTEXITCODE -ne 1) { throw 'Exited tunnel was falsely reported as a successful running task' }
Write-Output 'FIXTURE_OK'
''', starter=starter)


if __name__ == '__main__':
    unittest.main()
