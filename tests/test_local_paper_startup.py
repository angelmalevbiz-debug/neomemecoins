import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('local_paper_service', ROOT / 'scripts/local_paper_service.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
TOKEN = '0123456789abcdef0123456789abcdef'


class WorkerIdentity(unittest.TestCase):
    def test_identity_reports_worker_pid_and_stays_in_service_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'stop.request'
            path = marker.with_name(f'startup-main-{TOKEN}.json')
            identity = runner.startup_identity_path('main', TOKEN, str(path), marker)
            with patch.object(runner.os, 'getpid', return_value=8123):
                runner.publish_worker_identity(identity, 'main', TOKEN)
            self.assertEqual(json.loads(path.read_text()),
                             {'service': 'main', 'run_token': TOKEN, 'pid': 8123})
            self.assertFalse(path.with_name(path.name + '.8123.tmp').exists())
            for bad_path in (path.parent.parent / path.name, path.with_name('startup-tape-' + TOKEN + '.json')):
                with self.assertRaises(ValueError):
                    runner.startup_identity_path('main', TOKEN, str(bad_path), marker)
            for token, ready in (('', str(path)), (TOKEN, ''), ('invalid', str(path))):
                with self.assertRaises(ValueError):
                    runner.startup_identity_path('main', token, ready, marker)

    def test_late_import_honors_launcher_stop_without_starting_worker(self):
        marker = Mock()
        marker.exists.side_effect = [False, True]
        module = Mock()
        with patch.object(runner, 'validate_environment', return_value=marker), \
                patch.dict(runner.sys.modules, {'market_monitor': module}), \
                patch.object(runner, 'publish_worker_identity') as publish:
            runner.run('main')
        module.main.assert_not_called()
        publish.assert_not_called()

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows ownership checks')
    def test_powershell_uses_worker_handshake_and_rejects_pid_reuse_or_wrong_token(self):
        # Load only the two startup helpers and mock CIM/listener observations;
        # this never starts or stops a real PAPER service.
        script = r'''
param([string]$SourceScript, [string]$ReadyFile)
$ErrorActionPreference = 'Stop'
$ast = [Management.Automation.Language.Parser]::ParseFile($SourceScript, [ref]$null, [ref]$null)
$defs = $ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -in @('Get-OwnedProcess', 'Wait-WorkerRecord')}, $true)
foreach ($definition in $defs) { Invoke-Expression $definition.Extent.Text }
$runner = 'C:\owned\scripts\local_paper_service.py'
$MainPort = 8878
$GatewayPort = 8879
$token = '0123456789abcdef0123456789abcdef'
$created = [DateTime]::UtcNow
$worker = [pscustomobject]@{ProcessId=8123; CreationDate=$created;
    CommandLine="python `"$runner`" --service main --run-token $token"}
function Get-CimInstance { param($ClassName, $Filter, $ErrorAction)
    if ($Filter -eq 'ProcessId=8123') { return $worker }
    return $null
}
function Get-NetTCPConnection { param($State, $LocalPort, $ErrorAction)
    return [pscustomobject]@{OwningProcess=8123}
}
$record = Wait-WorkerRecord 'main' $token $ReadyFile 'stdout' 'stderr'
if ($record.pid -ne 8123 -or $record.run_token -ne $token) { throw 'Launcher PID used instead of worker identity' }
$record.created_at = $created.AddSeconds(-10).ToString('o')
if ($null -ne (Get-OwnedProcess $record)) { throw 'Reused PID accepted' }
$record.created_at = $created.ToString('o')
$record.run_token = 'ffffffffffffffffffffffffffffffff'
if ($null -ne (Get-OwnedProcess $record)) { throw 'Other startup token accepted' }
$badIdentityRejected = $false
try { Wait-WorkerRecord 'main' 'ffffffffffffffffffffffffffffffff' $ReadyFile 'stdout' 'stderr' | Out-Null }
catch { $badIdentityRejected = $_.Exception.Message -like 'Invalid startup identity*' }
if (!$badIdentityRejected) { throw 'Mismatched readiness identity accepted' }
Write-Output 'OWNERSHIP_TEST_OK'
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            fixture = path / 'ownership.ps1'
            fixture.write_text(script, encoding='utf-8')
            ready = path / 'ready.json'
            ready.write_text(json.dumps({'service': 'main', 'run_token': TOKEN, 'pid': 8123}))
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(fixture),
                                     str(ROOT / 'scripts/start_local_paper.ps1'), str(ready)],
                                    capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('OWNERSHIP_TEST_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
