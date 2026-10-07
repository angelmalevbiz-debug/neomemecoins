"""Explicit offline PAPER reset with a verified, recoverable archive.

Never imports a trading engine, starts a service, or touches a live wallet.
Raw market recordings remain available; session trade/account state starts anew.
"""
import hashlib
import json
import math
import os
import time
import uuid
from pathlib import Path

from engine_runtime import atomic_json


ARCHIVE_CHUNK_BYTES = 1024 * 1024


def _hash_file(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open('rb') as handle:
        while True:
            chunk = handle.read(ARCHIVE_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _copy_file_checked(source, target):
    digest = hashlib.sha256()
    size = 0
    with Path(source).open('rb') as src, Path(target).open('xb') as dst:
        while True:
            chunk = src.read(ARCHIVE_CHUNK_BYTES)
            if not chunk:
                break
            dst.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    return digest.hexdigest(), size


def archive_files(root, names, *, move_names=()):
    root = Path(root).resolve()
    destination = root / 'archive' / f'reset-{time.time_ns()}-{uuid.uuid4().hex[:8]}'
    destination.mkdir(parents=True, mode=0o700)
    files = {}
    move_names = set(move_names)
    for name in names:
        source = root / name
        if source.resolve().parent != root or not source.is_file():
            continue
        target = destination / source.name
        if source.name in move_names:
            digest, size = _hash_file(source)
            os.replace(source, target)
            if target.stat().st_size != size:
                raise OSError('archive size mismatch; reset refused')
        else:
            digest, size = _copy_file_checked(source, target)
            os.chmod(target, 0o600)
            archived_digest, archived_size = _hash_file(target)
            if archived_digest != digest or archived_size != size:
                raise OSError('archive checksum mismatch; reset refused')
        os.chmod(target, 0o600)
        files[source.name] = {'sha256': digest, 'bytes': size}
    atomic_json(destination / 'manifest.json', {
        'schema_version': 1, 'mode': 'PAPER', 'archived_at': int(time.time()*1000),
        'reason': 'User explicitly requested history and capital reset', 'files': files,
    })
    return destination


def reset_offline(root, *, starting_balance=1000.0, training_balance=500.0):
    root = Path(root).resolve()
    if not math.isfinite(starting_balance) or starting_balance <= 0:
        raise ValueError('positive finite PAPER capital required')
    if not math.isfinite(training_balance) or training_balance <= 0:
        raise ValueError('positive finite training capital required')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A caller must stop the writer before this explicit offline operation.
    archive = archive_files(root, ['state.json', 'audit.jsonl', 'training.json',
                                   'training_snapshot.json', 'training_events.jsonl'])
    stamp = int(time.time()*1000)
    sid = f'PAPER-RESET-{stamp}-{uuid.uuid4().hex[:8]}'
    fresh = {
        'schema_version': 2, 'execution_mode': 'PAPER', 'positions': [], 'history': [],
        'events': [], 'price_history': {}, 'trade_seq': 0, 'pending_audit': [],
        'demo_starting_balance_usd': starting_balance, 'demo_balance_usd': starting_balance,
        'demo_started_at': stamp, 'demo_session_id': sid,
        'risk_day_key': time.strftime('%Y-%m-%d', time.gmtime()),
        'risk_day_start_balance_usd': starting_balance,
        'reset_archive': str(archive),
    }
    # Imported only for the explicit training reset, after the archive succeeds.
    from paper_training import PaperTrainingEngine
    training_root = root / 'training'
    training_archive = archive_files(
        training_root,
        ['training.json', 'training_snapshot.json', 'training_events.jsonl', 'observations.jsonl',
         'recorder_status.json', 'quote_probe_status.json'],
        move_names={'observations.jsonl'},
    )
    prepared = training_root / f'.reset-training-{uuid.uuid4().hex}.json'
    engine = PaperTrainingEngine(prepared)
    engine.reset(initial_cash=training_balance)
    engine.state['recording_start_at'] = stamp
    journal = training_root / 'observations.jsonl'
    journal.touch(exist_ok=True)
    engine.state['recording_start_offset'] = journal.stat().st_size if journal.exists() else 0
    engine.save()
    # All serialization/initialization succeeds before the account is changed.
    atomic_json(root / 'state.json', fresh)
    os.replace(prepared, training_root / 'training.json')
    engine.path = training_root / 'training.json'
    atomic_json(training_root / 'training_snapshot.json', engine.snapshot())
    atomic_json(training_root / 'recorder_status.json', {
        'version': 1, 'dropped_total': 0, 'updated_at': stamp})
    atomic_json(training_root / 'quote_probe_status.json', {
        'status': 'WAITING_FOR_QUALIFIED_FLOW', 'attempts': 0, 'successes': 0,
        'last_attempt_at': 0, 'last_updated_at': 0, 'reason': ''})
    (root / 'audit.jsonl').write_text(json.dumps({
        'schema_version': 2, 'id': sid, 'kind': 'RESET', 'ts': stamp,
        'starting_balance_usd': starting_balance, 'archive': str(archive),
    })+'\n', encoding='utf-8')
    # Evaluation journals contain old outcomes and must not survive a session reset.
    if (root / 'training_events.jsonl').exists():
        (root / 'training_events.jsonl').write_text('', encoding='utf-8')
    return {'mode': 'PAPER', 'session_id': sid, 'balance_usd': starting_balance,
            'history_count': 0, 'open_positions': 0, 'archive': str(archive),
            'training_archive': str(training_archive), 'training': engine.snapshot()}


def reset_training_offline(training_root, *, training_balance=500.0):
    """Archive and restart one PAPER learner without touching any trading account.

    Writers must be stopped. The worker lock is acquired here as a second
    guard, the old observation journal is checksum-archived and moved, and the
    new learner inherits the monotonic recorder-drop baseline. This permits a
    clean evidence epoch while keeping prior gaps visible in the archive.
    """
    training_root = Path(training_root).resolve()
    if training_root.name != 'training':
        raise ValueError('training-only reset requires a dedicated training directory')
    if not math.isfinite(training_balance) or training_balance <= 0:
        raise ValueError('positive finite training capital required')
    training_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (training_root / 'reset.request.json').exists():
        raise FileExistsError('a PAPER learner reset is already pending')

    from compat_file_lock import flock, LOCK_EX, LOCK_NB, LOCK_UN
    from paper_training import PaperTrainingEngine, number

    lock_path = training_root / 'training.process.lock'
    with lock_path.open('a+') as lock:
        flock(lock, LOCK_EX | LOCK_NB)
        try:
            old_path = training_root / 'training.json'
            old = json.loads(old_path.read_text(encoding='utf-8')) if old_path.exists() else {}
            if not isinstance(old, dict):
                raise ValueError('invalid PAPER training state; reset refused')
            baseline = max(0, int(number(old.get('recording_drops_baseline'))))
            epoch_drops = max(0, int(number(old.get('recording_drops_total'))))
            drops_total = baseline + epoch_drops
            status_path = training_root / 'recorder_status.json'
            if status_path.exists():
                recorder = json.loads(status_path.read_text(encoding='utf-8'))
                if not isinstance(recorder, dict):
                    raise ValueError('invalid PAPER recorder status; reset refused')
                drops_total = max(drops_total, int(number(recorder.get('dropped_total'))))

            stamp = int(time.time() * 1000)
            prepared = training_root / f'.reset-training-{uuid.uuid4().hex}.json'
            engine = PaperTrainingEngine(prepared)
            engine.reset(initial_cash=training_balance)
            engine.state['recording_drops_baseline'] = drops_total
            engine.state['recording_drops_total'] = 0
            engine.state['recording_start_at'] = stamp
            engine.state['recording_start_offset'] = 0
            engine.save()
            try:
                archive = archive_files(
                    training_root,
                    ['training.json', 'training_snapshot.json', 'training_events.jsonl',
                     'recorder_status.json', 'observations.jsonl'],
                    move_names={'observations.jsonl'},
                )
                (training_root / 'observations.jsonl').touch(exist_ok=True)
                os.replace(prepared, old_path)
                engine.path = old_path
                atomic_json(training_root / 'training_snapshot.json', engine.snapshot())
                atomic_json(status_path, {'version': 1, 'dropped_total': drops_total,
                                          'updated_at': stamp})
                events = training_root / 'training_events.jsonl'
                if events.exists():
                    events.write_text('', encoding='utf-8')
                return {'archive': str(archive), 'training': engine.snapshot(),
                        'recording_drops_baseline': drops_total}
            finally:
                if prepared.exists():
                    prepared.unlink()
        finally:
            flock(lock, LOCK_UN)


def restore_archive(archive, root):
    archive, root = Path(archive).resolve(), Path(root).resolve()
    manifest = json.loads((archive / 'manifest.json').read_text(encoding='utf-8'))
    verified = {}
    for name, metadata in manifest['files'].items():
        if Path(name).name != name:
            raise ValueError('unsafe manifest filename')
        source = archive / name
        digest, size = _hash_file(source)
        if digest != metadata['sha256'] or size != metadata['bytes']:
            raise ValueError('archive checksum mismatch; restore refused')
        verified[name] = source
    # Validate every file before overwriting anything, and preserve current state.
    preserved = archive_files(root, verified, move_names={'observations.jsonl'})
    for name, source in verified.items():
        temp = root / (name+'.restore.tmp')
        digest, size = _copy_file_checked(source, temp)
        if digest != manifest['files'][name]['sha256'] or size != manifest['files'][name]['bytes']:
            raise OSError('restore checksum mismatch; restore refused')
        os.replace(temp, root / name)
    return {'restored_files': sorted(verified), 'previous_state_archive': str(preserved)}


def reset_all_offline(root, *, paired_root=None):
    """Reset all known PAPER account formats after stopping their writers.

    Authoritative private registries are preserved; every overwritten JSON has
    a checksum archive. Unknown formats fail closed rather than losing history.
    """
    root = Path(root).resolve()
    paths = [root]
    users = root / 'users'
    if users.exists():
        paths.extend(p.parent for p in users.glob('*/state.json')
                     if p.resolve().is_relative_to(root))
    def valid_capital(book):
        if not isinstance(book, dict):
            raise ValueError('Unknown PAPER book schema; reset refused')
        capital = book.get('starting_balance', 500)
        if isinstance(capital, bool) or not math.isfinite(float(capital)) or float(capital) <= 0:
            raise ValueError('Invalid PAPER starting capital; reset refused')
    # Validate legacy layouts and their capital before changing any account.
    for name, field in [('user_accounts.json','accounts'),('strategy_lab.json','books'),
                        ('astra_6_brain.json','book')]:
        source = root / name
        if source.exists():
            data = json.loads(source.read_text(encoding='utf-8'))
            if not isinstance(data.get(field),dict):
                raise ValueError(f'Unknown {name} schema; reset refused')
            if field == 'accounts':
                if any(not isinstance(account, dict) for account in data[field].values()):
                    raise ValueError('Unknown private account schema; reset refused')
            else:
                for book in data[field].values() if field == 'books' else [data[field]]:
                    valid_capital(book)
    if paired_root is not None and (Path(paired_root)/'paired_state.json').exists():
        paired = json.loads((Path(paired_root)/'paired_state.json').read_text(encoding='utf-8'))
        if not isinstance(paired.get('groups'),dict):
            raise ValueError('Unknown paired schema; reset refused')
        import lab_paired_policy as policy
        if paired.get('version') != policy.VERSION or set(paired['groups']) != {g.id for g in policy.GROUPS}:
            raise ValueError('Unknown paired version/groups; reset refused')
        for group in paired['groups'].values():
            if not isinstance(group, dict) or not isinstance(group.get('books'), dict) or set(group['books']) != set(policy.ARMS):
                raise ValueError('Unknown paired arms; reset refused')
            for book in group['books'].values():
                valid_capital(book)
    archives, count = [], 0
    for account_root in paths:
        result = reset_offline(account_root)
        archives.extend([result['archive'],result['training_archive']])
        count += 1
    names = ['user_accounts.json','strategy_lab.json','strategy_lab_compact.json','astra_6_brain.json']
    archives.append(str(archive_files(root, names)))
    stamp = int(time.time()*1000)
    registry = root / 'user_accounts.json'
    if registry.exists():
        data = json.loads(registry.read_text(encoding='utf-8'))
        for account in data.get('accounts', {}).values():
            account.update(balance=1000.,history=[],positions=[],trade_seq=0,started_at=stamp,updated_at=stamp)
        atomic_json(registry, data)
    lab = root / 'strategy_lab.json'
    legacy_books = 0
    if lab.exists():
        data = json.loads(lab.read_text(encoding='utf-8'))
        if not isinstance(data.get('books'),dict):
            raise ValueError('Unknown Strategy Lab schema; archived but reset refused')
        for book in data['books'].values():
            capital = float(book.get('starting_balance',500))
            book.update(balance=capital,position=None,positions=[],history=[],trade_seq=0,
                        last_entry_by_address={},created_at=stamp,entry_diagnostics={})
            legacy_books += 1
        data.update(started_at=stamp,updated_at=stamp,status='reset',stats={})
        data.pop('astra',None)
        data.pop('paired',None)
        atomic_json(lab,data)
        from lab_dashboard_projection import compact_strategy_lab
        atomic_json(root/'strategy_lab_compact.json',compact_strategy_lab(data))
    astra = root / 'astra_6_brain.json'
    if astra.exists():
        data = json.loads(astra.read_text(encoding='utf-8'))
        if not isinstance(data.get('book'),dict):
            raise ValueError('Unknown Astra schema; archived but reset refused')
        book=data['book']
        capital=float(book.get('starting_balance',500))
        book.update(balance=capital,positions=[],position=None,history=[],trade_seq=0,known_mints=[],
                    created_at=stamp,day=time.strftime('%Y-%m-%d',time.gmtime()),day_start_balance=capital)
        data.update(updated_at=stamp,status='reset',events=[],stats={},diagnostics={},quote_requests=0)
        atomic_json(astra,data)
        legacy_books += 1
    if paired_root is not None:
        paired_root = Path(paired_root).resolve()
        source = paired_root/'paired_state.json'
        archives.append(str(archive_files(paired_root,['paired_state.json','snapshot.json','ledger.jsonl'])))
        if source.exists():
            data=json.loads(source.read_text(encoding='utf-8'))
            if not isinstance(data.get('groups'),dict):
                raise ValueError('Unknown paired schema; archived but reset refused')
            for group in data['groups'].values():
                group.update(sequence=0,episode=None,completed=[],last_closed_by_mint={},diagnostics={})
                for book in group['books'].values():
                    capital=float(book.get('starting_balance',500))
                    book.update(balance=capital,position=None,positions=[],history=[])
                    legacy_books += 1
                group['day_start_balances']={k:b['balance'] for k,b in group['books'].items()}
            data.update(started_at=stamp,updated_at=stamp,status='reset')
            atomic_json(source,data)
            from lab_paired_runner import ExperimentRunner
            runner=ExperimentRunner(paired_root,stamp)
            runner.persist(stamp)
            ledger = paired_root/'ledger.jsonl'
            if ledger.exists():
                ledger.write_text('',encoding='utf-8')
    return {'mode':'PAPER','main_and_personal_accounts_reset':count,
            'legacy_books_reset':legacy_books,'training_books_per_account':9,'archives':archives}
