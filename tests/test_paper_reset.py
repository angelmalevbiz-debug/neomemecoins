import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from paper_state_reset import reset_offline,reset_all_offline,restore_archive


class PaperResetTests(unittest.TestCase):
    def test_reset_restorable_checked_archive_and_independent_training_capital(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            old={'demo_balance_usd':900,'history':[{'id':'loss','pnl_usd':-100}], 'positions':[{'id':'held'}]}
            raw=json.dumps(old).encode()
            (root/'state.json').write_bytes(raw)
            result=reset_offline(root)
            fresh=json.loads((root/'state.json').read_text(encoding='utf-8'))
            self.assertEqual(fresh['demo_balance_usd'],1000)
            self.assertEqual(fresh['history'],[])
            self.assertEqual(fresh['positions'],[])
            for book in result['training']['books']:
                self.assertEqual(book['starting_balance'],500)
                self.assertEqual(book['cash'],500)
                self.assertEqual(book['completed_trades'],0)
            archive=Path(result['archive'])
            manifest=json.loads((archive/'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['files']['state.json']['sha256'],hashlib.sha256(raw).hexdigest())
            restore_archive(archive,root)
            self.assertEqual((root/'state.json').read_bytes(),raw)

    def test_failed_archive_never_resets_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/'state.json';state.write_text('{"demo_balance_usd":800}')
            before=state.read_bytes()
            with patch('paper_state_reset.archive_files',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):reset_offline(root)
            self.assertEqual(state.read_bytes(),before)

    def test_reset_all_personal_registry_lab_and_astra_keep_accounts_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            user=root/'users'/'test';user.mkdir(parents=True)
            (user/'state.json').write_text('{"demo_balance_usd":700,"history":[1]}')
            (root/'user_accounts.json').write_text(json.dumps({'accounts':{'test':{'balance':700,'history':[1],'engine_port':18801}}}))
            (root/'strategy_lab.json').write_text(json.dumps({'books':{'SCALPER':{'starting_balance':100,'balance':80,'history':[1],'position':{'id':'x'}}}}))
            (root/'astra_6_brain.json').write_text(json.dumps({'book':{'starting_balance':500,'balance':400,'positions':[1],'history':[1]}}))
            result=reset_all_offline(root)
            self.assertEqual(result['main_and_personal_accounts_reset'],2)
            self.assertEqual(result['legacy_books_reset'],2)
            registry=json.loads((root/'user_accounts.json').read_text())['accounts']['test']
            self.assertEqual(registry['balance'],1000)
            self.assertEqual(registry['engine_port'],18801)
            lab=json.loads((root/'strategy_lab.json').read_text())['books']['SCALPER']
            self.assertEqual(lab['balance'],100)
            self.assertIsNone(lab['position'])
            self.assertFalse(lab['history'])

    def test_unknown_legacy_schema_refuses_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state.json';state.write_text('{"demo_balance_usd":800}')
            (root/'astra_6_brain.json').write_text('{"alien":true}')
            before=state.read_bytes()
            with self.assertRaises(ValueError):reset_all_offline(root)
            self.assertEqual(state.read_bytes(),before)

    def test_invalid_legacy_capital_refuses_before_resetting_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state.json';state.write_text('{"demo_balance_usd":800}')
            (root/'strategy_lab.json').write_text('{"books":{"bad":{"starting_balance":-1}}}')
            before=state.read_bytes()
            with self.assertRaises(ValueError):reset_all_offline(root)
            self.assertEqual(state.read_bytes(),before)

    def test_offline_reset_preserves_market_journal_but_skips_entire_old_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); training=root/'training';training.mkdir()
            # Imported/future availability must not resurrect old session results.
            journal=training/'observations.jsonl'
            old=b'{"id":"old-future","available_at":9999999999999}\n'
            journal.write_bytes(old)
            result=reset_offline(root)
            state=json.loads((training/'training.json').read_text(encoding='utf-8'))
            self.assertEqual(state['recording_start_offset'],len(old))
            self.assertEqual(journal.read_bytes(),old)
            self.assertEqual((Path(result['training_archive'])/'observations.jsonl').read_bytes(),old)


if __name__=='__main__':unittest.main()
