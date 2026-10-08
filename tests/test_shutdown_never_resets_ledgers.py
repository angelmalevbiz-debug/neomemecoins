"""A shutdown must never write a default account over a ledger it did not load.

Pre-existing hazard found in the pre-deploy review: the service runner always saved
STATE on exit, so a failed or interrupted load (or a second engine refused by the
ledger lock) replaced the real ledger with an empty $1,000 account. The Strategy Lab
had the same hazard, and also rebuilt 34 empty books from an unreadable file.
Offline only; every path is a temporary directory.
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

_TEMP = tempfile.TemporaryDirectory(prefix='neo-shutdown-')
os.environ['NEO_MARKET_STATE_PATH'] = str(Path(_TEMP.name) / 'state.json')
os.environ['NEO_MARKET_AUDIT_PATH'] = str(Path(_TEMP.name) / 'audit.jsonl')
os.environ['NEO_LIVE_TAPE_PATH'] = str(Path(_TEMP.name) / 'tape.json')
os.environ.pop('NEO_SIGNAL_STRATEGY', None)
import market_monitor as m
import strategy_lab as lab

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('local_paper_service_shutdown', ROOT / 'scripts/local_paper_service.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def service_module(main_error, loaded):
    module = MagicMock()
    module.main.side_effect = main_error
    module.STATE.loaded = loaded
    module.LOADED = loaded
    return module


class RunnerShutdown(unittest.TestCase):
    def run_service(self, service, module, name):
        marker = Mock()
        marker.exists.return_value = False
        with patch.object(runner, 'validate_environment', return_value=marker), \
             patch.dict(runner.sys.modules, {name: module}), \
             patch.object(runner, 'publish_worker_identity'):
            try:
                runner.run(service)
            except RuntimeError:
                pass

    def test_failed_engine_load_does_not_save_a_default_account(self):
        module = service_module(RuntimeError('Account state is unreadable; refusing automatic reset'), False)
        self.run_service('main', module, 'market_monitor')
        module.STATE.save.assert_not_called()
        module.training_bridge.stop.assert_called_once()

    def test_refused_second_engine_does_not_touch_the_ledger(self):
        module = service_module(RuntimeError('Another PAPER engine already writes state.json'), False)
        self.run_service('main', module, 'market_monitor')
        module.STATE.save.assert_not_called()

    def test_loaded_engine_still_flushes_on_stop(self):
        module = service_module(KeyboardInterrupt(), True)
        self.run_service('main', module, 'market_monitor')
        module.STATE.save.assert_called_once()

    def test_lab_persists_only_after_its_ledger_was_loaded(self):
        unloaded = service_module(KeyboardInterrupt(), False)
        self.run_service('lab', unloaded, 'strategy_lab')
        unloaded.persist.assert_not_called()
        loaded = service_module(KeyboardInterrupt(), True)
        self.run_service('lab', loaded, 'strategy_lab')
        loaded.persist.assert_called_once_with('stopped')

    def test_tape_flushes_its_ticker_registry_on_stop(self):
        # DEFENSIVE_ENTRY_LAYER_V1: the seat screen's ticker sightings of the last
        # (at most 5-minute) save interval survive a clean tape restart.
        without_recorder = service_module(KeyboardInterrupt(), True)
        without_recorder._RECORDER = None
        self.run_service('tape', without_recorder, 'live_tape')
        without_recorder.flush_scheduler_registry.assert_called_once_with()
        recording = service_module(KeyboardInterrupt(), True)
        recorder = recording._RECORDER
        self.run_service('tape', recording, 'live_tape')
        recording.flush_scheduler_registry.assert_called_once_with()
        recorder.db.commit.assert_called_once_with()
        recorder.close.assert_called_once_with()


class LoadFlags(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='neo-load-flag-')
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'state.json'
        p = patch.object(m, 'STATE_PATH', self.state)
        p.start()
        self.addCleanup(p.stop)

    def test_engine_marks_loaded_only_after_a_successful_read(self):
        self.assertFalse(m.State(load_state=False).loaded)
        self.assertTrue(m.State().loaded)  # no file yet: a legitimate new account
        self.state.write_text(json.dumps({'history': [{'id': 'keep', 'pnl_usd': -1}], 'positions': [],
                                          'demo_balance_usd': 999.0, 'trade_seq': 1}), encoding='utf-8')
        self.assertTrue(m.State().loaded)
        self.state.write_text('{broken', encoding='utf-8')
        fresh = m.State(load_state=False)
        with self.assertRaisesRegex(RuntimeError, 'refusing automatic reset'):
            fresh.load()
        self.assertFalse(fresh.loaded)
        self.assertEqual(self.state.read_text(encoding='utf-8'), '{broken')

    def test_unreadable_lab_ledger_is_refused_not_rebuilt(self):
        path = Path(self.temp.name) / 'strategy_lab.json'
        path.write_text('{broken', encoding='utf-8')
        with patch.object(lab, 'STATE_PATH', path), \
             patch.object(lab, 'RESET_FLAG_PATH', Path(self.temp.name) / 'absent.reset'):
            with self.assertRaisesRegex(RuntimeError, 'refusing automatic reset'):
                lab.load_state()
        self.assertEqual(path.read_text(encoding='utf-8'), '{broken')

    def test_missing_lab_ledger_still_starts_fresh_books(self):
        with patch.object(lab, 'STATE_PATH', Path(self.temp.name) / 'absent.json'), \
             patch.object(lab, 'RESET_FLAG_PATH', Path(self.temp.name) / 'absent.reset'):
            state = lab.load_state()
        self.assertEqual(set(state['books']), {s['id'] for s in lab.STRATEGIES})


if __name__ == '__main__':
    unittest.main()
