"""Only temporary journals; malformed rows never become observations or proof."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import training_worker


class JournalIntegrityTests(unittest.TestCase):
    def test_incomplete_tail_waits_and_then_ingests_once_after_append(self):
        engine = Mock()
        engine.state = {'invalid_observations': 0, 'recording_drops_total': 0}
        engine.has_seen.return_value = False
        row = {'id': 'real-row', 'available_at': 1}
        payload = json.dumps(row).encode()
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'observations.jsonl'
            source.write_bytes(payload)
            self.assertEqual(training_worker.consume_batch(engine, source, 0, 0), (0, False))
            engine.ingest.assert_not_called()
            self.assertEqual(engine.state['invalid_observations'], 0)
            with source.open('ab') as handle:
                handle.write(b'\n')
            offset, changed = training_worker.consume_batch(engine, source, 0, 0)
            self.assertTrue(changed)
            self.assertEqual(offset, len(payload)+1)
            engine.ingest.assert_called_once_with(row, persist=False)
            self.assertEqual(training_worker.consume_batch(engine, source, offset, 0), (offset, False))

    def test_invalid_encoding_object_and_timestamp_are_durable_gaps_not_rows(self):
        engine = Mock()
        engine.state = {'invalid_observations': 0, 'recording_drops_total': 2}
        engine.has_seen.return_value = False
        payload = b'\xff\nnull\n{"available_at":"bad"}\n'
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'observations.jsonl'
            source.write_bytes(payload)
            self.assertEqual(training_worker.consume_batch(engine, source, 0, 0), (len(payload), True))
            engine.ingest.assert_not_called()
            self.assertEqual(source.read_bytes(), payload)
            self.assertEqual(engine.state['invalid_observations'], 3)
            self.assertEqual(engine.state['journal_malformed_rows'], 3)
            self.assertEqual(engine.state['recording_drops_total'], 5)


if __name__ == '__main__':
    unittest.main()
