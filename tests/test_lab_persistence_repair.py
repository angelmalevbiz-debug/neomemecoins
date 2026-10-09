"""Regression coverage for Windows sharing locks and durable entry recovery."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import strategy_lab as lab


class StopLoop(BaseException):
    pass


class LabPersistenceRepairTests(unittest.TestCase):
    def test_transient_windows_lock_retries_without_replacing_old_state_early(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'state.json'
            target.write_text('{"old":true}',encoding='utf-8')
            replace=Path.replace
            calls=[]

            def locked_once(source,destination):
                calls.append(source)
                if len(calls)==1:
                    self.assertEqual(json.loads(target.read_text()),{'old':True})
                    raise PermissionError('sharing lock')
                return replace(source,destination)

            with patch.object(Path,'replace',locked_once),patch.object(lab.time,'sleep') as sleep:
                lab.atomic_write_path(target,{'new':True})
            self.assertEqual(json.loads(target.read_text()),{'new':True})
            self.assertEqual(len(calls),2)
            sleep.assert_called_once()

    def test_persistent_lock_preserves_prior_state_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'state.json'
            target.write_text('{"old":true}',encoding='utf-8')
            with patch.object(Path,'replace',side_effect=PermissionError('locked')) as replace,patch.object(lab.time,'sleep'):
                with self.assertRaises(PermissionError):
                    lab.atomic_write_path(target,{'new':True})
            self.assertEqual(replace.call_count,8)
            self.assertEqual(json.loads(target.read_text()),{'old':True})

    def test_error_reporting_failure_does_not_kill_loop_or_admit_entries_before_recovery(self):
        state={'activity_version':lab.activity.POLICY_VERSION}
        writes=[PermissionError('online'),PermissionError('degraded'),
                PermissionError('retry'),PermissionError('degraded'),None,None]
        # main() also builds the LAB_HIGH_FREQUENCY_V1 container, which loads and checkpoints
        # HF_ROOT (NEO_STRATEGY_LAB_HF_DIR, else next to the Lab ledger): never a real HF root here.
        with patch.object(lab,'STATE',{}),patch.object(lab,'load_state',return_value=state),\
             patch.object(lab,'build_high_frequency',return_value=None) as build_hf,patch.object(lab,'HF',None),\
             patch.object(lab,'flow_map',return_value={}),patch.object(lab,'update_positions'),\
             patch.object(lab,'maybe_open') as open_entry,patch.object(lab,'persist',side_effect=writes) as persist,\
             patch.object(lab,'ENTRY_REFRESH_SECONDS',0),\
             patch.object(lab.SESSION,'get') as get,patch.object(lab.time,'sleep',side_effect=[None,None,StopLoop]),\
             patch.object(lab,'print',create=True):
            get.return_value.json.return_value={'feed':[]}
            with self.assertRaises(StopLoop):
                lab.main()
            self.assertEqual(open_entry.call_count,2)
            self.assertEqual(persist.call_count,6)
            self.assertEqual(persist.call_args_list[4].args,('degraded','Recovering a failed state write'))
            build_hf.assert_called_once_with()


if __name__=='__main__':
    unittest.main()
