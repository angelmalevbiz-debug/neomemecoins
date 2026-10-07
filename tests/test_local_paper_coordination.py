"""Real Windows mutex regression, with unique names and no real workers."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows lifecycle coordination')
class LifecycleCoordination(unittest.TestCase):
    def test_manual_mutation_and_watchdog_probe_share_reentrant_abandonable_mutex(self):
        operation = 'Local\\NeoPaperTestOperation-' + uuid.uuid4().hex
        lifetime = 'Local\\NeoPaperTestWatchdog-' + uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / 'scripts'
            scripts.mkdir()
            services = root / '.runtime/accounts/services'
            services.mkdir(parents=True)
            starter = scripts / 'start_local_paper.ps1'
            starter.write_text((ROOT / 'scripts/start_local_paper.ps1').read_text(encoding='utf-8').replace(
                'Local\\NeoLocalPaperOperation', operation), encoding='utf-8')
            watchdog = scripts / 'local_paper_watchdog.ps1'
            source = (ROOT / 'scripts/local_paper_watchdog.ps1').read_text(encoding='utf-8').replace(
                'Local\\NeoLocalPaperOperation', operation).replace('Local\\NeoLocalPaperWatchdog', lifetime)
            # Only mark entry into the real probe function. All runtime paths
            # resolve inside this fixture; no Python environment is installed.
            source = source.replace('function Get-Status {',
                                    "function Get-Status {\n    [IO.File]::WriteAllText((Join-Path $services 'probe-read'), 'read')")
            watchdog.write_text(source, encoding='utf-8')
            holder = root / 'holder.ps1'
            holder.write_text(r'''
param([string]$Name, [string]$Ready, [int]$HoldMilliseconds, [switch]$Abandon)
$m = [Threading.Mutex]::new($false, $Name)
$m.WaitOne() | Out-Null
[IO.File]::WriteAllText($Ready, 'ready')
if ($Abandon) { [Environment]::Exit(0) }
[Threading.Thread]::Sleep($HoldMilliseconds)
$m.ReleaseMutex()
$m.Dispose()
''', encoding='utf-8')
            controller = root / 'controller.ps1'
            controller.write_text(r'''
param([string]$Starter, [string]$Watchdog, [string]$Holder, [string]$Operation, [string]$Fixture)
$ErrorActionPreference = 'Stop'
$sentinel = [Threading.Mutex]::new($false, $Operation)
function Expect-InstallFailure {
    $expected = $false
    try { & $Starter -Action Start | Out-Null }
    catch { $expected = $_.Exception.Message -like 'Install the isolated .venv dependencies*' }
    if (!$expected) { throw 'Lifecycle gate did not reach the isolated missing-environment check' }
}
function Wait-Ready([string]$Path) {
    $limit = [DateTime]::UtcNow.AddSeconds(10)
    while (!(Test-Path -LiteralPath $Path)) {
        if ([DateTime]::UtcNow -gt $limit) { throw 'Fixture holder did not become ready' }
        [Threading.Thread]::Sleep(25)
    }
}
function Launch-Holder([string]$Ready, [switch]$Abandon) {
    $arguments = @('-NoProfile', '-NonInteractive', '-File', '"'+$Holder+'"',
                   '-Name', $Operation, '-Ready', '"'+$Ready+'"', '-HoldMilliseconds', '3000')
    if ($Abandon) { $arguments += '-Abandon' }
    return Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WindowStyle Hidden -PassThru
}
try {
    # Real nested ownership: the watchdog's outer lock and the starter's inner
    # acquisition execute on the same thread without a deadlock.
    if (!$sentinel.WaitOne(0)) { throw 'Isolated fixture mutex is unexpectedly busy' }
    try { Expect-InstallFailure }
    finally { $sentinel.ReleaseMutex() }
    if (!$sentinel.WaitOne(0)) { throw 'Failure did not release the starter operation lock' }
    $sentinel.ReleaseMutex()
    # A successful Stop takes the script's exit-0 path. Finally must still
    # release the lock, while retaining the explicit watchdog pause marker.
    [IO.File]::WriteAllText((Join-Path $Fixture '.runtime/accounts/services/processes.json'), '{"processes":[]}')
    & $Starter -Action Stop | Out-Null
    if (!$sentinel.WaitOne(0)) { throw 'Successful Stop exit leaked the operation lock' }
    $sentinel.ReleaseMutex()
    if (!(Test-Path -LiteralPath (Join-Path $Fixture '.runtime/accounts/services/watchdog.pause'))) {
        throw 'Explicit Stop did not retain its watchdog pause'
    }
    Remove-Item -LiteralPath (Join-Path $Fixture '.runtime/accounts/services/watchdog.pause')

    $ready = Join-Path $Fixture 'contended.ready'
    $owner = Launch-Holder $ready
    Wait-Ready $ready
    function Start-Sleep { param($Seconds, $Milliseconds) throw 'TEST_LOOP_COMPLETE' }
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $ended = $false
    try { & $Watchdog -CheckIntervalSeconds 1 }
    catch { $ended = $_.Exception.Message -eq 'TEST_LOOP_COMPLETE' }
    if (!$ended) { throw 'Watchdog fixture did not complete its bounded iteration' }
    if (Test-Path -LiteralPath (Join-Path $Fixture '.runtime/accounts/services/probe-read')) {
        throw 'Watchdog probed a manifest while another operation owned the lifecycle lock'
    }
    Expect-InstallFailure
    $timer.Stop()
    if ($timer.ElapsedMilliseconds -lt 2000) { throw 'Manual mutation did not wait for the independent holder' }
    $owner.WaitForExit()
    if (!$sentinel.WaitOne(0)) { throw 'Contended failure leaked the operation lock' }
    $sentinel.ReleaseMutex()

    # Keep a handle alive while the independent owner exits without releasing;
    # otherwise Windows would remove the named object before the test waiter.
    $abandonedReady = Join-Path $Fixture 'abandoned.ready'
    $abandoned = Launch-Holder $abandonedReady -Abandon
    Wait-Ready $abandonedReady
    $abandoned.WaitForExit()
    Expect-InstallFailure
    if (!$sentinel.WaitOne(0)) { throw 'Abandoned ownership was not recovered and released' }
    $sentinel.ReleaseMutex()
    Write-Output 'COORDINATION_TEST_OK'
} finally {
    $sentinel.Dispose()
}
''', encoding='utf-8')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(controller),
                                     str(starter), str(watchdog), str(holder), operation, str(root)],
                                    capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('COORDINATION_TEST_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
