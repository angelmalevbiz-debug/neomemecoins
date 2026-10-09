param(
    [Parameter(Mandatory=$true)][string]$RuntimeRepository,
    [switch]$Apply
)
$ErrorActionPreference='Stop'
$sourceRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$targetRoot=[IO.Path]::GetFullPath($RuntimeRepository)
if ($sourceRoot -eq $targetRoot) { throw 'Source and runtime must be different checkouts.' }
$files=@('backend/funded_active_paper.py','backend/lab_capacity_test.py','backend/strategy_lab.py','backend/live_tape.py','backend/funded_market_candidates.py',
    'backend/entry_defense.py','backend/tape_pool_scheduler.py','backend/lab_dashboard_projection.py',
    'scripts/start_local_paper.ps1','scripts/build_funded_heat_seed.py',
    'src/lib/labStrategyView.ts','docs/FUNDED_ACTIVE_PAPER.md','strategy-lock.json')
$liveLock=Get-Content -LiteralPath (Join-Path $targetRoot 'strategy-lock.json') -Raw | ConvertFrom-Json
$incomingLock=Get-Content -LiteralPath (Join-Path $sourceRoot 'strategy-lock.json') -Raw | ConvertFrom-Json
$digest=[Security.Cryptography.SHA256]::Create()
function TextHash([string]$Path) {
    $content=[IO.File]::ReadAllText($Path).Replace("`r`n","`n")
    return [BitConverter]::ToString($digest.ComputeHash([Text.Encoding]::UTF8.GetBytes($content))).Replace('-','').ToLowerInvariant()
}
foreach($relative in $files) {
    $source=Join-Path $sourceRoot $relative
    $target=Join-Path $targetRoot $relative
    if (!(Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing release file: $relative" }
    $oldHash=$liveLock.support_files_sha256.$relative
    if ($oldHash -and (Test-Path -LiteralPath $target) -and (TextHash $target) -ne $oldHash) {
        throw "Runtime has unrelated edits in $relative; nothing deployed."
    }
    if ($incomingLock.support_files_sha256.$relative -and
        (TextHash $source) -ne $incomingLock.support_files_sha256.$relative) { throw "Incoming lock mismatch: $relative" }
    if (!$oldHash -and $relative -ne 'strategy-lock.json' -and (Test-Path -LiteralPath $target) -and
        (TextHash $target) -ne (TextHash $source)) { throw "Unrecognized runtime file: $relative" }
}
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$scratch=Join-Path ([IO.Path]::GetTempPath()) "neo-funded-active-$stamp"
New-Item -ItemType Directory -Path $scratch | Out-Null
foreach($folder in @('backend','scripts','docs')) {
    Copy-Item -LiteralPath (Join-Path $targetRoot $folder) -Destination $scratch -Recurse
}
New-Item -ItemType Directory -Path (Join-Path $scratch 'src') | Out-Null
Copy-Item -LiteralPath (Join-Path $targetRoot 'src/lib') -Destination (Join-Path $scratch 'src') -Recurse
foreach($relative in $files) {
    $destination=Join-Path $scratch $relative
    New-Item -ItemType Directory -Path (Split-Path $destination) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot $relative) -Destination $destination
}
Push-Location $scratch
try {
    & node scripts/verify-strategy-lock.mjs
    if ($LASTEXITCODE -ne 0) { throw 'Scratch strategy lock check failed.' }
    & (Join-Path $targetRoot '.venv/Scripts/python.exe') -c "import os,runpy,sys,tempfile; setup=runpy.run_path('scripts/run_python_checks.py'); temp=tempfile.TemporaryDirectory(prefix='neo-import-'); os.environ.update(setup['isolated_environment'](temp.name)); os.environ['NEO_LAB_FUNDED_ACTIVE_ENABLED']='1'; sys.path.insert(0,'backend'); import market_monitor,strategy_lab,live_tape,user_gateway; assert strategy_lab.active_paper.enabled(); print('PAPER active policy imports OK; no service started')"
    if ($LASTEXITCODE -ne 0) { throw 'Scratch service imports failed.' }
} finally { Pop-Location }
if (!$Apply) { Write-Output "Dry run passed. Scratch: $scratch"; exit 0 }
$launcher=Join-Path $targetRoot 'scripts/start_local_paper.ps1'
$status=@(& $launcher -Action Status)
if ($status.Count -ne 4 -or @($status | Where-Object {!$_.running}).Count) { throw 'Required owned services are not healthy; no Stop attempted.' }
$main=Invoke-RestMethod -Uri 'http://127.0.0.1:8878/state' -TimeoutSec 10
if (@($main.positions).Count) { throw 'Main PAPER has an open position; deploy refused.' }
$registry=Get-Content -LiteralPath (Join-Path $targetRoot '.runtime/accounts/user_accounts.json') -Raw | ConvertFrom-Json
foreach($account in $registry.accounts.PSObject.Properties) {
    $personal=Invoke-RestMethod -Uri "http://127.0.0.1:$($account.Value.engine_port)/state" -TimeoutSec 5
    if (@($personal.positions).Count) { throw 'A personal PAPER engine has an open position; deploy refused.' }
}
# Only the established launcher stops its identity-checked PAPER cohort. No PID kill.
& (Join-Path $targetRoot '.venv/Scripts/python.exe') (Join-Path $sourceRoot 'scripts/build_funded_heat_seed.py') --journal (Join-Path $targetRoot '.runtime/accounts/training/observations.jsonl') --out (Join-Path $targetRoot '.runtime/accounts/funded_heat_seed.json')
if ($LASTEXITCODE -ne 0) { Write-Warning 'No fresh history seed; normal fail-closed warm-up will apply.' }
& $launcher -Action Stop
if ($LASTEXITCODE -ne 0) { throw 'Graceful stop failed; no files overwritten.' }
$backup=Join-Path $targetRoot ".runtime/release-backup-funded-active-$stamp"
New-Item -ItemType Directory -Path (Join-Path $backup 'files') | Out-Null
$manifest=@()
foreach($relative in $files) {
    $target=Join-Path $targetRoot $relative
    $exists=Test-Path -LiteralPath $target
    if ($exists) {
        $saved=Join-Path (Join-Path $backup 'files') $relative
        New-Item -ItemType Directory -Path (Split-Path $saved) -Force | Out-Null
        Copy-Item -LiteralPath $target -Destination $saved
    }
    $manifest += [pscustomobject]@{path=$relative;existed=$exists;old_sha256=if($exists){TextHash $target}else{$null};new_sha256=TextHash (Join-Path $sourceRoot $relative)}
}
$ledgers=Join-Path $backup 'ledgers'
New-Item -ItemType Directory -Path $ledgers | Out-Null
Get-ChildItem -LiteralPath (Join-Path $targetRoot '.runtime/accounts') -File -Filter '*.json' |
    Copy-Item -Destination $ledgers
foreach($folder in @('users','strategy_lab_hf')) {
    $live=Join-Path $targetRoot ".runtime/accounts/$folder"
    if (Test-Path -LiteralPath $live) {
        # Personal journals are tens of GB too. Back up ledgers/config, never
        # duplicate append-only observation streams or old reset archives.
        foreach($file in (Get-ChildItem -LiteralPath $live -Recurse -File)) {
            $inside=$file.FullName.Substring($live.Length).TrimStart([char[]]@('\','/'))
            if ($inside -match '(^|[\\/])archive([\\/]|$)' -or $file.Name -eq 'observations.jsonl') { continue }
            if ($file.Extension -ne '.json' -and !($folder -eq 'strategy_lab_hf' -and $file.Extension -eq '.jsonl')) { continue }
            $saved=Join-Path (Join-Path $ledgers $folder) $inside
            New-Item -ItemType Directory -Path (Split-Path $saved) -Force | Out-Null
            Copy-Item -LiteralPath $file.FullName -Destination $saved
        }
    }
}
[IO.File]::WriteAllText((Join-Path $backup 'manifest.json'),($manifest | ConvertTo-Json -Depth 4))
foreach($relative in $files) {
    $destination=Join-Path $targetRoot $relative
    New-Item -ItemType Directory -Path (Split-Path $destination) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot $relative) -Destination $destination
}
& node (Join-Path $targetRoot 'scripts/verify-strategy-lock.mjs')
if ($LASTEXITCODE -ne 0) { throw "Post-sync lock failed; services remain stopped. Backup: $backup" }
& $launcher -Action Start
if ($LASTEXITCODE -ne 0) { throw "Start failed. Ledgers were not reset; backup: $backup" }
$after=Invoke-RestMethod -Uri 'http://127.0.0.1:8878/state' -TimeoutSec 10
[pscustomobject]@{deployed=$true;backup=$backup;scratch=$scratch;ledger_reset=$false;
    live_enabled=$false;policy_expected='PAPER_CAPACITY_TEST_V1_FIXED_100';requires_fresh_snapshot_verification=$true} | ConvertTo-Json
$digest.Dispose()
