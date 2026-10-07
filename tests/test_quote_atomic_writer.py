"""Shared quote observations remain atomic under Windows sharing violations."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import honest_quote_transport as transport
import live_tape as tape


class SharedAtomicWriterTests(unittest.TestCase):
    def test_transient_replace_lock_retries_without_changing_observation_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'reference.json'
            target.write_text('{"old": true}',encoding='utf-8')
            original_replace=Path.replace
            attempts=[]

            def replace(source,destination):
                attempts.append(source)
                if len(attempts)<3:
                    self.assertEqual(json.loads(target.read_text()),{'old':True})
                    raise PermissionError(13,'Windows sharing violation',str(destination))
                return original_replace(source,destination)

            observation={'_received_at':123,'usd_per_unit':150}
            with patch.object(Path,'replace',replace),patch.object(transport.time,'sleep') as delay:
                transport._write(target,observation)
            self.assertEqual(json.loads(target.read_text()),observation)
            self.assertEqual(len(attempts),3)
            self.assertEqual([call.args[0] for call in delay.call_args_list],[.025,.05])
            self.assertFalse(list(root.glob('*.tmp')))

    def test_exhausted_sharing_retry_raises_keeps_previous_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'reference.json'
            previous=b'{"observed_at":1,"usd_per_unit":100}'
            target.write_bytes(previous)
            error=PermissionError(13,'locked',str(target))
            with patch.object(Path,'replace',side_effect=error) as replace, \
                 patch.object(transport.time,'sleep') as delay:
                with self.assertRaises(PermissionError) as raised:
                    transport._write(target,{'observed_at':2,'usd_per_unit':150})
            self.assertIs(raised.exception,error)
            self.assertEqual(replace.call_count,transport.ATOMIC_REPLACE_ATTEMPTS)
            self.assertEqual(delay.call_count,transport.ATOMIC_REPLACE_ATTEMPTS-1)
            self.assertLessEqual(sum(call.args[0] for call in delay.call_args_list),1.6)
            self.assertEqual(target.read_bytes(),previous)
            self.assertFalse(list(root.glob('*.tmp')))

    def test_nonpermission_disk_error_is_not_retried_or_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'reference.json'
            target.write_bytes(b'{"old":true}')
            with patch.object(Path,'replace',side_effect=OSError(28,'disk full')) as replace, \
                 patch.object(transport.time,'sleep') as delay:
                with self.assertRaises(OSError):transport._write(target,{'new':True})
            self.assertEqual(replace.call_count,1)
            delay.assert_not_called()
            self.assertEqual(target.read_bytes(),b'{"old":true}')
            self.assertFalse(list(root.glob('*.tmp')))

    def test_concurrent_writers_use_distinct_complete_temp_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'reference.json'
            gate=threading.Barrier(2);lock=threading.Lock()
            observed_sources={};original_replace=Path.replace
            observations=[{'_received_at':index,'contents':[index]*1000} for index in (1,2)]

            def replace(source,destination):
                thread_id=threading.get_ident()
                with lock:
                    first=thread_id not in observed_sources
                    if first:observed_sources[thread_id]=(source,json.loads(source.read_text()))
                if first:gate.wait(timeout=5)
                return original_replace(source,destination)

            with patch.object(Path,'replace',replace),ThreadPoolExecutor(max_workers=2) as workers:
                futures=[workers.submit(transport._write,target,row) for row in observations]
                for future in futures:future.result(timeout=10)
            self.assertEqual(len({source for source,row in observed_sources.values()}),2)
            self.assertEqual(sorted(row['_received_at'] for source,row in observed_sources.values()),[1,2])
            self.assertIn(json.loads(target.read_text()),observations)
            self.assertFalse(list(root.glob('*.tmp')))

    def test_nonfinite_payload_preserves_existing_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'reference.json';target.write_text('{"old":true}')
            with self.assertRaises(ValueError):transport._write(target,{'usd_per_unit':float('nan')})
            self.assertEqual(target.read_text(),'{"old":true}')
            self.assertFalse(list(target.parent.glob('*.tmp')))


class RecorderFailureLoggingTests(unittest.TestCase):
    def test_filename_and_windows_code_are_logged_without_path_or_exception_message(self):
        error=PermissionError(13,'Bearer PRIVATE_TOKEN',r'C:\private_credentials\quote-reference.json')
        error.winerror=32
        summary=tape.failure_summary(error)
        self.assertEqual(summary,'PermissionError (file=quote-reference.json, winerror=32)')
        self.assertNotIn('PRIVATE_TOKEN',summary)
        self.assertNotIn('private_credentials',summary)

    def test_filename_control_characters_and_noninteger_error_codes_are_sanitized(self):
        error=PermissionError(13,'private',r'C:\private\quote'+'\nreference.json')
        error.winerror='private'
        summary=tape.failure_summary(error)
        self.assertEqual(summary,'PermissionError (file=quote_reference.json)')
        self.assertNotIn('\n',summary)
        self.assertEqual(tape.failure_summary(RuntimeError('secret response')),'RuntimeError')

    def test_replace_failure_reports_destination_basename_when_available(self):
        error=PermissionError(13,'private',r'C:\private\temporary.json')
        error.filename2=r'C:\private\live_tape.json'
        self.assertEqual(tape.failure_summary(error),'PermissionError (file=live_tape.json)')


if __name__=='__main__':unittest.main()
