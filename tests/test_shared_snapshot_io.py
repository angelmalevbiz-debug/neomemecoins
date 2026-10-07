"""Real atomic snapshot replacement with Windows read handles still open."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import live_tape as tape
import market_monitor as monitor
import shared_snapshot_io as snapshots
import strategy_lab as lab


class SharedSnapshotTests(unittest.TestCase):
    def test_open_reader_keeps_old_complete_snapshot_after_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'live_tape.json'
            old = {'updated_at': 100, 'events': [{'symbol': 'Старо'}] * 1000}
            new = {'updated_at': 101, 'events': [{'symbol': 'Ново'}] * 1000}
            target.write_text(json.dumps(old, ensure_ascii=False), encoding='utf-8')
            # Keep two native handles open, as simultaneous main/Lab readers do.
            # Neither read ahead before replacement, so this checks the handle's
            # file identity rather than a previously buffered copy of the data.
            with snapshots.open_shared_text(target) as main_reader, \
                    snapshots.open_shared_text(target) as lab_reader, \
                    patch.object(tape, 'OUT', target), \
                    patch.object(tape.time, 'sleep') as retry:
                tape.atomic_write(new)
                retry.assert_not_called()
                self.assertEqual(json.loads(main_reader.read()), old)
                self.assertEqual(json.loads(lab_reader.read()), old)
                self.assertEqual(json.loads(snapshots.read_shared_text(target)), new)
                self.assertFalse(list(target.parent.glob('*.replace-backup.*.bak')))
            self.assertTrue(main_reader.closed)
            self.assertTrue(lab_reader.closed)
            self.assertFalse(list(target.parent.glob('*.tmp')))

    def test_first_publication_creates_new_snapshot_without_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'live_tape.json'
            observation = {'updated_at': 101, 'events': []}
            with patch.object(tape, 'OUT', target):
                tape.atomic_write(observation)
            self.assertEqual(json.loads(snapshots.read_shared_text(target)), observation)
            self.assertFalse(list(target.parent.glob('*.tmp')))
            self.assertFalse(list(target.parent.glob('*.replace-backup.*.bak')))

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing semantics')
    def test_standard_open_reproduces_the_replacement_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'live_tape.json'
            replacement = Path(directory) / 'new.json'
            target.write_text('{"old":true}', encoding='utf-8')
            replacement.write_text('{"new":true}', encoding='utf-8')
            with target.open('r', encoding='utf-8') as reader:
                with self.assertRaises(PermissionError):
                    os.replace(replacement, target)
                self.assertEqual(json.loads(reader.read()), {'old': True})
            os.replace(replacement, target)
            self.assertEqual(json.loads(snapshots.read_shared_text(target)), {'new': True})

    def test_missing_file_and_invalid_text_errors_propagate(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'live_tape.json'
            with self.assertRaises(FileNotFoundError):
                snapshots.read_shared_text(target)
            target.write_bytes(b'\xff\xfe')
            with self.assertRaises(UnicodeDecodeError):
                snapshots.read_shared_text(target)
            # Decoding failure closes the descriptor; the next read succeeds.
            replacement = target.with_suffix('.replacement')
            replacement.write_bytes(b'{"updated_at":9}')
            os.replace(replacement, target)
            self.assertEqual(snapshots.read_shared_text(target), '{"updated_at":9}')

    @unittest.skipUnless(os.name == 'nt', 'Windows descriptor ownership')
    def test_text_wrapper_failure_closes_transferred_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'snapshot.json'
            target.write_text('{}', encoding='utf-8')
            fd = snapshots._windows_read_fd(str(target))
            with patch.object(snapshots, '_windows_read_fd', return_value=fd):
                with self.assertRaises(LookupError):
                    with snapshots.open_shared_text(target, encoding='invalid-codec-never-exists'):
                        self.fail('Invalid codec opened a text wrapper')
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_consumers_keep_unavailable_and_invalid_json_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'live_tape.json'
            with patch.object(monitor, 'LIVE_TAPE_PATH', target):
                self.assertEqual(monitor.read_live_tape(), {'status': 'offline', 'events': []})
                for content in ('not json', '[]', 'null'):
                    target.write_text(content, encoding='utf-8')
                    self.assertEqual(monitor.read_live_tape(), {'status': 'offline', 'events': []})
                target.write_text('{"updated_at":7,"status":"degraded","events":[]}', encoding='utf-8')
                self.assertEqual(monitor.read_live_tape()['updated_at'], 7)
            target.write_text('not json', encoding='utf-8')
            self.assertEqual(lab.load_json(target, {'unavailable': True}), {'unavailable': True})
            permission_patch = (patch.object(snapshots, '_windows_read_fd', side_effect=PermissionError('denied'))
                                if os.name == 'nt' else patch.object(Path, 'open', side_effect=PermissionError('denied')))
            with permission_patch:
                with self.assertRaises(PermissionError):
                    snapshots.read_shared_text(target)

    def test_persistent_projection_failure_raises_preserves_old_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'live_tape.json'
            previous = b'{"updated_at":1,"events":[]}'
            target.write_bytes(previous)
            failure = PermissionError(13, 'denied', str(target))
            with patch.object(tape, 'OUT', target), \
                    patch.object(tape, 'replace_shared_snapshot', side_effect=failure) as replace, \
                    patch.object(tape.time, 'sleep'):
                with self.assertRaises(PermissionError) as raised:
                    tape.atomic_write({'updated_at': 2, 'events': []})
            self.assertIs(raised.exception, failure)
            self.assertEqual(replace.call_count, tape.ATOMIC_REPLACE_ATTEMPTS)
            self.assertEqual(target.read_bytes(), previous)
            self.assertFalse(list(target.parent.glob('*.tmp')))

    @unittest.skipUnless(os.name == 'nt', 'Windows replacement error semantics')
    def test_acl_fault_is_not_hidden_by_movefile_fallback(self):
        import ctypes
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'live_tape.json'
            source = Path(directory) / 'new.tmp'
            target.write_bytes(b'{"updated_at":1}')
            source.write_bytes(b'{"updated_at":2}')
            failure = ctypes.WinError(5)
            with patch.object(snapshots, '_windows_replace', side_effect=failure), \
                    patch.object(snapshots.os, 'replace') as fallback:
                with self.assertRaises(PermissionError):
                    snapshots.replace_shared_snapshot(source, target)
                fallback.assert_not_called()
            self.assertEqual(target.read_bytes(), b'{"updated_at":1}')

    @unittest.skipUnless(os.name == 'nt', 'Windows partial replacement failures')
    def test_partial_replacement_failure_preserves_original_or_backup(self):
        import ctypes
        for code in (1176, 1177):
            with self.subTest(winerror=code), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'live_tape.json'
                previous = b'{"updated_at":1,"events":[{"symbol":"OLD"}]}'
                target.write_bytes(previous)
                backups = []

                def partial_failure(source, destination, backup):
                    self.assertEqual(source.parent, backup.parent)
                    self.assertNotEqual(backup, source)
                    self.assertNotEqual(backup, destination)
                    backups.append(backup)
                    if code == 1177:
                        # Documented failure: old file now exists at backup name.
                        os.replace(destination, backup)
                    raise ctypes.WinError(code)

                with patch.object(tape, 'OUT', target), \
                        patch.object(snapshots, '_windows_replace', side_effect=partial_failure):
                    with self.assertRaises(OSError) as raised:
                        tape.atomic_write({'updated_at': 2, 'events': []})
                self.assertEqual(raised.exception.winerror, code)
                self.assertEqual(len(backups), 1)
                preserved = target if code == 1176 else backups[0]
                self.assertEqual(preserved.read_bytes(), previous)
                self.assertFalse(list(target.parent.glob('*.tmp')))

    def test_invalid_projection_payload_keeps_prior_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'live_tape.json'
            previous = b'{"updated_at":1,"events":[]}'
            target.write_bytes(previous)
            with patch.object(tape, 'OUT', target):
                with self.assertRaises(ValueError):
                    tape.atomic_write({'updated_at': 2, 'price': float('nan')})
            self.assertEqual(target.read_bytes(), previous)
            self.assertFalse(list(target.parent.glob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
