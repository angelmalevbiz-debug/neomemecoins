"""Only tiny temporary recordings; no real account or fabricated market fill."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import observation_journal as journal
import training_worker
from paper_state_reset import archive_files, restore_archive


class ObservationPartsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'observations.jsonl'

    def row(self, index):
        return json.dumps({'id': str(index), 'available_at': index}).encode() + b'\n'

    def write(self, index):
        journal.append(self.source, self.row(index), part_bytes=80)

    def test_legacy_prefix_is_verbatim_and_cursor_continues_across_rotation(self):
        old = self.row(1) + self.row(2)
        self.source.write_bytes(old)
        self.write(3)
        self.assertEqual(self.source.read_bytes(), old)
        self.assertEqual(journal.total_size(self.source), len(old)+len(self.row(3)))
        with journal.Reader(self.source) as handle:
            handle.seek(len(old))
            self.assertEqual(handle.readline(), self.row(3))
            self.assertEqual(handle.tell(), journal.total_size(self.source))
        self.write(4)
        self.write(5)
        with journal.Reader(self.source) as handle:
            self.assertEqual(handle.read(), b''.join(self.row(i) for i in range(1, 6)))

    def test_worker_resumes_existing_global_checkpoint_and_ingests_once(self):
        self.write(1)
        self.write(2)
        old_offset = journal.total_size(self.source)
        self.write(3)
        self.write(4)
        engine = Mock()
        engine.state = {'invalid_observations': 0, 'recording_drops_total': 9}
        engine.has_seen.return_value = False
        offset, changed = training_worker.consume_batch(engine, self.source, old_offset, 0)
        self.assertTrue(changed)
        self.assertEqual(offset, journal.total_size(self.source))
        self.assertEqual([c.args[0]['id'] for c in engine.ingest.call_args_list], ['3','4'])
        self.assertEqual(engine.state['recording_drops_total'], 9)
        self.assertEqual(training_worker.consume_batch(engine, self.source, offset, 0), (offset, False))

    def test_reader_seek_read_and_iteration_span_parts_with_utf8_and_crlf(self):
        old = '{"name":"лев"}\r\n'.encode()
        self.source.write_bytes(old)
        journal.append(self.source, b'{"next":true}\n', part_bytes=len(old))
        with journal.Reader(self.source) as handle:
            self.assertEqual(list(handle), [old, b'{"next":true}\n'])
            handle.seek(-7, 2)
            self.assertEqual(handle.read(7), b':true}\n')
            handle.seek(2)
            self.assertEqual(handle.read(3), old[2:5])
            self.assertEqual(handle.seek(2, 1), 7)

    def test_manifest_failure_preserves_prefix_and_empty_orphan_is_reusable(self):
        self.source.write_bytes(self.row(1)+self.row(2))
        before = self.source.read_bytes()
        with patch('observation_journal.atomic_json', side_effect=OSError('replace blocked')):
            with self.assertRaises(OSError): self.write(3)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(journal.manifest_path(self.source).exists())
        self.assertEqual((self.source.parent/journal.part_name(self.source, 1)).stat().st_size, 0)
        self.write(3)
        with journal.Reader(self.source) as handle:
            self.assertEqual(handle.read(), before+self.row(3))

    def test_nonempty_orphan_is_never_overwritten_or_adopted(self):
        self.source.write_bytes(self.row(1)+self.row(2))
        orphan = self.source.parent/journal.part_name(self.source, 1)
        orphan.write_bytes(b'uncommitted\n')
        with self.assertRaises(ValueError): self.write(3)
        self.assertEqual(orphan.read_bytes(), b'uncommitted\n')
        self.assertFalse(journal.manifest_path(self.source).exists())

    def test_invalid_missing_reordered_changed_and_unsafe_manifest_fail_closed(self):
        for corruption in ('version','missing','changed','order','traversal','tail'):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as folder:
                source = Path(folder)/'observations.jsonl'
                source.write_bytes(self.row(1)+self.row(2))
                journal.append(source, self.row(3), part_bytes=80)
                manifest = journal.manifest_path(source)
                original = json.loads(manifest.read_text())
                if corruption == 'version': original['version'] = 'UNKNOWN'
                if corruption == 'missing': (source.parent/journal.part_name(source, 1)).unlink()
                if corruption == 'changed': source.write_bytes(source.read_bytes()+b'changed\n')
                if corruption == 'order': original['parts'].reverse()
                if corruption == 'traversal': original['parts'][0]['name'] = '../observations.jsonl'
                if corruption == 'tail': original['parts'][-1]['bytes'] = 1
                manifest.write_text(json.dumps(original))
                before = source.read_bytes()
                with self.assertRaises((ValueError, OSError)): self.write_to(source, 4)
                self.assertEqual(source.read_bytes(), before)
                self.assertEqual(json.loads(manifest.read_text()), original)

    def write_to(self, source, index):
        journal.append(source, self.row(index), part_bytes=80)

    def test_incomplete_tail_never_skipped_joined_or_truncated(self):
        self.source.write_bytes(b'{"partial":')
        with self.assertRaises(ValueError): self.write(3)
        self.assertEqual(self.source.read_bytes(), b'{"partial":')
        self.assertFalse(journal.manifest_path(self.source).exists())

    def test_unreadable_manifest_is_not_replaced_with_a_new_recording(self):
        self.source.write_bytes(self.row(1)+self.row(2))
        before = self.source.read_bytes()
        original = Path.stat
        manifest = journal.manifest_path(self.source)
        def stat(path, *args, **kwargs):
            if path == manifest: raise PermissionError('sharing violation')
            return original(path,*args,**kwargs)
        with patch.object(Path,'stat',stat):
            with self.assertRaises(PermissionError): self.write(3)
        self.assertEqual(self.source.read_bytes(),before)
        self.assertFalse(manifest.exists())

    def test_partial_active_tail_waits_at_original_cursor(self):
        self.source.write_bytes(self.row(1)+self.row(2))
        self.write(3)
        tail = journal.layout(self.source)[-1][0]
        offset = journal.total_size(self.source)
        with tail.open('ab') as handle: handle.write(self.row(4)[:-1])
        engine = Mock()
        engine.state = {'invalid_observations':0, 'recording_drops_total':0}
        self.assertEqual(training_worker.consume_batch(engine,self.source,offset,0),(offset,False))
        engine.ingest.assert_not_called()

    def test_segmented_checkpoint_cannot_silently_rewind(self):
        self.source.write_bytes(self.row(1)+self.row(2))
        self.write(3)
        with self.assertRaises(ValueError):
            training_worker.consume_batch(Mock(), self.source, journal.total_size(self.source)+1, 0)

    def test_parts_archived_moved_and_restored_together(self):
        self.source.write_bytes(self.row(1)+self.row(2))
        self.write(3)
        expected = journal.files(self.source)
        archive = archive_files(self.source.parent, ['observations.jsonl'], move_names={'observations.jsonl'})
        self.assertEqual(set(json.loads((archive/'manifest.json').read_text())['files']),set(expected))
        self.assertFalse(self.source.exists())
        self.assertFalse(journal.manifest_path(self.source).exists())
        restore_archive(archive, self.source.parent)
        with journal.Reader(self.source) as handle:
            self.assertEqual(handle.read(),self.row(1)+self.row(2)+self.row(3))

    def test_batch_bounds_are_explicit_and_nothing_is_pruned(self):
        with self.assertRaises(FileNotFoundError): journal.Reader(self.source)
        with self.assertRaises(ValueError): journal.append(self.source,b'not-newline')
        with self.assertRaises(ValueError): journal.append(self.source,self.row(1),part_bytes=1)
        self.assertFalse(self.source.exists())
        for i in range(20): self.write(i)
        with journal.Reader(self.source) as handle:
            self.assertEqual(len(list(handle)),20)
        self.assertTrue(all(size <= 80 for _,_,size in journal.layout(self.source)))

    def test_copy_archive_freezes_manifest_even_if_recorder_rotates(self):
        self.source.write_bytes(self.row(1)+self.row(2))
        self.write(3)
        import paper_state_reset
        original_copy = paper_state_reset._copy_file_checked
        def copying(source, target):
            result = original_copy(source,target)
            if source.name == 'observations.jsonl':
                self.write(4)
                self.write(5)  # Rotate AFTER the archive's part list was captured.
            return result
        with patch('paper_state_reset._copy_file_checked',side_effect=copying):
            archive = archive_files(self.source.parent,['observations.jsonl'])
        with journal.Reader(archive/'observations.jsonl') as handle:
            rows = [json.loads(line)['id'] for line in handle]
        self.assertEqual(rows,['1','2','3','4'])
        self.assertEqual(len(journal.layout(self.source)),3)


if __name__ == '__main__':
    unittest.main()
