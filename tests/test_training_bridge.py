"""Shared recorder/PROCESS learner integration with temporary PAPER state only."""
import copy
import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import training_bridge
from paper_training import USDC, PaperTrainingEngine
from training_bridge import TrainingBridge

MINT = "So11111111111111111111111111111111111111112"
PAIR = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def inputs(at, price=1):
    coin = {"address": MINT, "pairAddress": PAIR, "priceUsd": price, "liquidityUsd": 1_000_000,
            "updatedAt": at, "score": 95, "dexId": "raydium", "symbol": "TEST"}
    flow = {"quality": "COMPLETE", "fresh": True, "latest_at": at, "trades": 10,
            "unique_wallets": 4, "buy_usd": 100, "sell_usd": 20, "buy_sell_usd_ratio": 5}
    safety = {"status": "pass", "mint": MINT, "pair": PAIR, "checked_at": at, "metrics": {"decimals": 6}}
    price_proof = {"status": "pass", "mint": MINT, "pair": PAIR, "reference_received_at": at,
                   "observed_price": price, "reference_price": price}
    buy_raw = {"inputMint": USDC, "outputMint": MINT,
               "routePlan": [{"swapInfo": {"ammKey": PAIR, "inputMint": USDC, "outputMint": MINT}}]}
    sell_raw = {"inputMint": MINT, "outputMint": USDC,
                "routePlan": [{"swapInfo": {"ammKey": PAIR, "inputMint": MINT, "outputMint": USDC}}]}
    # Correctness fixture includes an explicit positive modeled account cost;
    # this synthetic small fee is not financial evidence. Actual new-account
    # tests below use recorded lamports and a fresh quote-asset USD reference.
    quotes = {"entry": {"quoted_at": at, "raw_quote": buy_raw,
                        "entry_network_fee_usd": .03, "entry_account_reserve_usd": .00165},
              "exit": {"quoted_at": at, "raw_quote": sell_raw, "network_fee_usd": .03}}
    return coin, flow, safety, price_proof, quotes


class TrainingBridgeProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.bridge = None
        self.addCleanup(self.stop)

    def start(self):
        self.bridge = TrainingBridge(self.root)
        return self.bridge

    def stop(self):
        if self.bridge:
            bridge = self.bridge
            bridge.close()
            self.assertFalse(bridge.thread.is_alive())
            if bridge.process is not None:
                self.assertIsNotNone(bridge.process.poll())
            self.bridge = None

    def wait_for(self, predicate, timeout=8):
        end = time.monotonic() + timeout
        last = {}
        while time.monotonic() < end:
            path = self.root / "training_snapshot.json"
            if path.exists():
                try:
                    last = json.loads(path.read_text(encoding="utf-8"))
                    if predicate(last) and self.bridge and self.bridge.process is not None:
                        return last
                except (OSError, ValueError):
                    pass
            if self.bridge and self.bridge.process and self.bridge.process.poll() is not None:
                error = (self.root/"worker_error.log").read_text(encoding="utf-8", errors="replace")
                self.fail("training worker unexpectedly exited: %s %s" % (self.bridge.process.returncode, error))
            time.sleep(.02)
        self.fail("condition timed out; last snapshot %s" % {k:v for k,v in last.items() if k != "books"})

    @staticmethod
    def control(snapshot):
        return next(b for b in snapshot["books"] if b["id"] == "CONTROL")

    def observe(self, at, price=1, proof=True):
        coin, flow, safety, validation, quotes = inputs(at, price)
        kwargs = {"safety": safety, "validation": validation, "quotes": quotes,
                  "context": {"conviction": 80, "available_at": at}} if proof else {}
        self.assertTrue(self.bridge.observe(coin, flow, now=at, **kwargs))

    def test_process_needs_proof_then_fills_on_new_market_after_latency(self):
        self.start()
        at = int(time.time()*1000)
        self.observe(at, proof=False)
        snap = self.wait_for(lambda s:s["unique_observations"] == 1)
        journal_size = (self.root/"observations.jsonl").stat().st_size
        checkpoint = json.loads((self.root/"training.json").read_text(encoding="utf-8"))["recording_start_offset"]
        self.assertEqual(checkpoint, journal_size)
        self.assertEqual(self.control(snap)["open_positions"], 0)
        self.assertEqual(self.control(snap)["pending_orders"], 0)
        self.observe(at+1000)
        snap = self.wait_for(lambda s:s["unique_observations"] == 2)
        self.assertEqual(self.control(snap)["pending_orders"], 1)
        self.observe(at+2000, price=2)
        snap = self.wait_for(lambda s:s["unique_observations"] == 3)
        self.assertEqual(self.control(snap)["open_positions"], 1)
        self.assertEqual(self.control(snap)["positions"][0]["entry"]["market_price"], 2)
        self.assertEqual(snap["unique_market_episodes"], 1)

    def test_quote_probe_status_is_persisted_across_bridge_restart(self):
        first = self.start()
        first.note_quote_probe('ROUTE_EVIDENCE_RECORDED', attempted=True,
                               success=True, at=1_800_000_000_000)
        self.assertEqual(first.quote_probe['attempts'], 1)
        self.assertEqual(first.quote_probe['successes'], 1)
        self.stop()
        second = self.start()
        self.assertEqual(second.quote_probe['status'], 'ROUTE_EVIDENCE_RECORDED')
        self.assertEqual(second.quote_probe['last_attempt_at'], 1_800_000_000_000)
        self.assertEqual(second.quote_probe['successes'], 1)

    def test_process_restart_recovers_pending_and_durable_completed_results(self):
        self.start()
        at = int(time.time()*1000)
        self.observe(at)
        first = self.wait_for(lambda s:s["unique_observations"] == 1)
        self.assertEqual(self.control(first)["pending_orders"], 1)
        first_pid = self.bridge.process.pid
        self.stop(); self.start()
        restored = self.wait_for(lambda s:s["unique_observations"] == 1)
        self.assertNotEqual(self.bridge.process.pid, first_pid)
        self.assertEqual(self.control(restored)["pending_orders"], 1)
        self.observe(at+1000)
        self.observe(at+2000, price=.8)
        self.observe(at+3000, price=.7)
        finished = self.wait_for(lambda s:self.control(s)["completed_trades"] == 1)
        self.assertLess(self.control(finished)["net_pnl_usd"], -3)
        self.stop(); self.start()
        reopened = self.wait_for(lambda s:self.control(s)["completed_trades"] == 1)
        self.assertEqual(reopened["simulation_count"], finished["simulation_count"])
        self.assertEqual(reopened["unique_observations"], finished["unique_observations"])
        self.assertEqual(self.control(reopened)["net_pnl_usd"], self.control(finished)["net_pnl_usd"])

    def test_corrupt_complete_journal_row_is_retained_reported_and_not_replayed_on_restart(self):
        journal = self.root/'observations.jsonl'
        corrupt = b'incomplete-old-write\n'
        journal.write_bytes(corrupt)
        self.start()
        self.observe(int(time.time()*1000))
        snapshot = self.wait_for(lambda s:s['unique_observations'] == 1)
        self.assertEqual(snapshot['invalid_observations'], 1)
        self.assertEqual(snapshot['recording_drops_total'], 1)
        self.assertTrue(snapshot['journal_integrity']['promotion_blocked_by_gap'])
        self.assertEqual(snapshot['journal_integrity']['last_bad_offset'], 0)
        self.assertTrue(journal.read_bytes().startswith(corrupt))
        self.stop(); self.start()
        reopened = self.wait_for(lambda s:s['unique_observations'] == 1)
        self.assertEqual(reopened['invalid_observations'], 1)
        self.assertEqual(reopened['journal_integrity']['malformed_rows'], 1)

    def test_exited_learning_worker_is_restarted_and_resumes_from_journal(self):
        self.start()
        initial = self.wait_for(lambda s:s["unique_observations"] == 0)
        first_pid = self.bridge.process.pid
        self.assertEqual(initial['worker_pid'], first_pid)
        self.bridge.process.terminate()
        self.bridge.process.wait(timeout=5)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            process = self.bridge.process
            if process is not None and process.pid != first_pid and process.poll() is None:
                break
            time.sleep(.02)
        else:
            self.fail("learning bridge did not restart its failed worker")

        at = int(time.time()*1000)
        self.observe(at)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                snapshot = json.loads((self.root/"training_snapshot.json").read_text(encoding="utf-8"))
                if (snapshot.get("unique_observations") == 1 and
                        self.bridge.process.poll() is None and self.bridge.error is None):
                    self.assertEqual(snapshot['worker_pid'], self.bridge.process.pid)
                    self.assertNotEqual(snapshot['worker_pid'], first_pid)
                    self.stop()
                    # Windows cannot rename a process-held file. Shutdown must
                    # finish the actual learner, not merely its venv launcher.
                    lock = self.root/'training.process.lock'
                    moved = self.root/'training.closed.lock'
                    lock.rename(moved)
                    moved.rename(lock)
                    return
            except (OSError, ValueError):
                pass
            time.sleep(.02)
        self.fail("restarted worker did not resume and process durable journal")

    def test_shutdown_sentinel_deferred_behind_batch_is_not_lost(self):
        bridge = TrainingBridge.__new__(TrainingBridge)
        bridge.root = self.root
        bridge.config = None
        bridge.stop_event = threading.Event()
        bridge.queue = queue.Queue()
        bridge._deferred = training_bridge._NO_DEFERRED_ITEM
        bridge._persist_drop_count = Mock(return_value=True)
        bridge._ensure_worker = Mock()
        bridge._write_batch = Mock()
        bridge._record_persisted = Mock()
        bridge.read_snapshot = Mock()
        bridge.processed = 0
        bridge.queue.put({'id':'one'})
        bridge.queue.put({'id':'two'})
        bridge.queue.put(None)
        thread = threading.Thread(target=bridge.run)
        thread.start()
        thread.join(timeout=1)
        if thread.is_alive():
            bridge.stop_event.set()
            thread.join(timeout=1)
            self.fail('Shutdown sentinel was lost after a recorder batch')
        bridge._write_batch.assert_called_once_with([{'id':'one'},{'id':'two'}])
        self.assertEqual(bridge.queue.unfinished_tasks, 0)

    def test_bounded_shutdown_persists_gap_for_queue_rows_left_after_slow_batch(self):
        self.start()
        self.wait_for(lambda s:s["unique_observations"] == 0)
        entered = threading.Event()
        original = self.bridge._write_batch

        def slow_write(rows):
            entered.set()
            time.sleep(3.2)
            original(rows)

        with patch.object(self.bridge, "_write_batch", side_effect=slow_write):
            self.assertTrue(self.bridge.submit({"id":"slow-batch", "available_at":1}))
            self.assertTrue(entered.wait(timeout=2))
            for index in range(5):
                self.assertTrue(self.bridge.submit({"id":f"queued-{index}", "available_at":index+2}))
            self.bridge.close()

        saved = json.loads((self.root/"recorder_status.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(saved["dropped_total"], 5)
        self.assertTrue(self.bridge.recording_drop_gap)
        self.assertIn("missing observations invalidate evidence coverage", self.bridge.error)
        self.assertEqual(self.bridge.queue.qsize(), 0)
        self.assertEqual(self.bridge.queue.unfinished_tasks, 0)

    def test_close_waits_for_worker_creation_and_stops_the_published_process(self):
        entered, release = threading.Event(), threading.Event()
        original = TrainingBridge._start_worker
        def delayed_start(bridge, config_path):
            entered.set()
            if not release.wait(timeout=3):
                raise RuntimeError('Test did not release worker creation')
            original(bridge, config_path)
        with patch.object(TrainingBridge, '_start_worker', delayed_start):
            bridge = self.start()
            self.assertTrue(entered.wait(timeout=2))
            errors = []
            def close():
                try:bridge.close()
                except Exception as exc:errors.append(exc)
            closer = threading.Thread(target=close)
            closer.start()
            try:
                release.set()
                closer.join(timeout=8)
                self.assertFalse(closer.is_alive())
                self.assertEqual(errors, [])
                self.assertFalse(bridge.thread.is_alive())
                self.assertIsNotNone(bridge.process)
                self.assertIsNotNone(bridge.process.poll())
            finally:
                release.set()
                closer.join(timeout=8)

    def test_durable_reset_boundary_excludes_future_dated_old_journal_after_restart(self):
        self.start()
        # A reset must exclude the earlier journal prefix even if imported
        # availability timestamps happen to be later than the wall clock.
        at = int(time.time()*1000)+3600000
        self.observe(at); self.observe(at+1000)
        self.wait_for(lambda s:s["unique_observations"] == 2)
        self.assertTrue(self.bridge.submit({"_reset": True}))
        clean = self.wait_for(lambda s:s["unique_observations"] == 0)
        self.assertEqual(clean["simulation_count"], 0)
        self.assertEqual(self.bridge.recording_drop_baseline, self.bridge.dropped)
        self.assertFalse(self.bridge.recording_drop_gap)
        saved = json.loads((self.root/"training.json").read_text(encoding="utf-8"))
        self.assertGreater(saved["recording_start_offset"], 0)
        self.stop(); self.start()
        clean_again = self.wait_for(lambda s:s["unique_observations"] == 0)
        self.assertEqual(self.control(clean_again)["cash"], 500)
        self.observe(at+2000)
        new = self.wait_for(lambda s:s["unique_observations"] == 1)
        self.assertEqual(new["simulation_count"], 0)
        self.assertTrue(list((self.root/"archive").glob("*/manifest.json")))

    def test_worker_keeps_partial_final_journal_line_until_complete(self):
        self.start()
        self.wait_for(lambda s:s["unique_observations"] == 0)
        at = int(time.time()*1000)
        coin, flow, safety, validation, quotes = inputs(at)
        with patch.object(self.bridge, "submit", side_effect=lambda x:json.dumps(x)):
            encoded = self.bridge.observe(coin, flow, safety=safety, validation=validation, quotes=quotes, now=at)
        source = self.root/"observations.jsonl"
        source.write_text(encoded, encoding="utf-8")
        time.sleep(.15)
        self.assertEqual(json.loads((self.root/"training_snapshot.json").read_text())["unique_observations"], 0)
        with source.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        snap = self.wait_for(lambda s:s["unique_observations"] == 1)
        self.assertEqual(self.control(snap)["pending_orders"], 1)

    def test_queue_drop_visible_and_submit_never_waits_for_learner(self):
        bridge = TrainingBridge.__new__(TrainingBridge)
        bridge.queue = queue.Queue(maxsize=1)
        bridge.lock = threading.Lock(); bridge.pending_ids = set(); bridge.recent_ids = training_bridge.OrderedDict()
        bridge.coalesced = 0
        bridge.closed = False; bridge.dropped = 0; bridge.error = None
        bridge.recording_drop_baseline = 0
        bridge.processed = 0; bridge.latest = {"paper_only": True}
        self.assertTrue(bridge.submit({"id": "one"}))
        started = time.monotonic()
        self.assertFalse(bridge.submit({"id": "two"}))
        self.assertLess(time.monotonic()-started, .1)
        with patch.object(training_bridge, "_BRIDGE", bridge):
            self.assertEqual(training_bridge.snapshot()["recorder"]["dropped"], 1)
            self.assertEqual(training_bridge.snapshot()["recorder"]["dropped_total"], 1)
        self.assertIn("queue full", bridge.error.lower())
        bridge.closed = True
        self.assertFalse(bridge.submit({"id": "three"}))

    def test_recorder_snapshot_separates_current_training_gap_from_archived_drops(self):
        bridge = TrainingBridge.__new__(TrainingBridge)
        bridge.queue = queue.Queue(maxsize=1)
        bridge.lock = threading.Lock(); bridge.pending_ids = set(); bridge.recent_ids = training_bridge.OrderedDict()
        bridge.coalesced = 0; bridge.closed = False; bridge.dropped = 12; bridge.error = None
        bridge.processed = 0; bridge.latest = {"paper_only": True}; bridge.recording_drop_baseline = 9
        with patch.object(training_bridge, "_BRIDGE", bridge):
            recorder = training_bridge.snapshot()["recorder"]
        self.assertEqual(recorder["dropped"], 3)
        self.assertEqual(recorder["dropped_baseline"], 9)
        self.assertEqual(recorder["dropped_total"], 12)

    def test_duplicate_observation_ids_are_coalesced_without_queue_pressure(self):
        bridge = TrainingBridge.__new__(TrainingBridge)
        bridge.queue = queue.Queue(maxsize=1)
        bridge.lock = threading.Lock(); bridge.pending_ids = set(); bridge.recent_ids = training_bridge.OrderedDict()
        bridge.coalesced = 0; bridge.closed = False; bridge.dropped = 0; bridge.error = None
        self.assertTrue(bridge.submit({"id": "same-evidence"}))
        self.assertTrue(bridge.submit({"id": "same-evidence"}))
        self.assertEqual(bridge.queue.qsize(), 1)
        self.assertEqual(bridge.coalesced, 1)
        self.assertEqual(bridge.dropped, 0)
        self.assertIsNone(bridge.error)
        self.assertEqual(bridge.queue.get_nowait(), {"id": "same-evidence"})
        bridge.queue.task_done()
        bridge._record_persisted("same-evidence")
        self.assertTrue(bridge.submit({"id": "same-evidence"}))
        self.assertEqual(bridge.queue.qsize(), 0)
        self.assertEqual(bridge.coalesced, 2)

    def test_repeated_snapshots_coalesce_but_new_flow_and_quotes_are_recorded(self):
        bridge = TrainingBridge.__new__(TrainingBridge)
        bridge.cache = {}
        bridge.lock = threading.Lock()
        bridge.last_observation_samples = {}
        bridge.coalesced = 0
        bridge.dropped = 0
        bridge.error = None
        bridge.closed = False
        recorded = []

        def capture(row):
            recorded.append(row)
            return True

        coin, flow, safety, validation, quotes = inputs(10_000)
        with patch.object(bridge, "submit", side_effect=capture):
            self.assertTrue(bridge.observe(coin, flow, safety=safety,
                                           validation=validation, now=10_000))

            repeated_coin = copy.deepcopy(coin)
            repeated_coin["updatedAt"] = 10_500
            repeated_safety = copy.deepcopy(safety)
            repeated_safety["checked_at"] = 10_500
            repeated_validation = copy.deepcopy(validation)
            repeated_validation["reference_received_at"] = 10_500
            self.assertTrue(bridge.observe(repeated_coin, flow, safety=repeated_safety,
                                           validation=repeated_validation, now=10_500))
            self.assertEqual(len(recorded), 1)
            self.assertEqual(bridge.coalesced, 1)

            new_flow = copy.deepcopy(flow)
            new_flow.update(latest_at=10_600, trades=11, buy_usd=110)
            self.assertTrue(bridge.observe(repeated_coin, new_flow, safety=repeated_safety,
                                           validation=repeated_validation, now=10_600))
            self.assertEqual(len(recorded), 2)

            new_quotes = copy.deepcopy(quotes)
            new_quotes["entry"]["quoted_at"] = 10_700
            new_quotes["exit"]["quoted_at"] = 10_700
            self.assertTrue(bridge.observe(repeated_coin, new_flow, safety=repeated_safety,
                                           validation=repeated_validation,
                                           quotes=new_quotes, now=10_700))
            self.assertEqual(len(recorded), 3)

            self.assertTrue(bridge.observe(repeated_coin, new_flow, safety=repeated_safety,
                                           validation=repeated_validation,
                                           quotes=new_quotes, now=14_000))
            self.assertEqual(len(recorded), 4)

    def test_recorder_batches_rows_into_one_durable_journal_sync(self):
        bridge = TrainingBridge.__new__(TrainingBridge)
        bridge.root = self.root
        rows = [{"id": "one", "available_at": 1}, {"id": "two", "available_at": 2}]
        with patch("training_bridge.os.fsync") as sync:
            bridge._write_batch(rows)
        self.assertEqual(sync.call_count, 1)
        saved = [json.loads(line) for line in (self.root / "observations.jsonl").read_text().splitlines()]
        self.assertEqual(saved, rows)

    def test_queue_drop_counter_is_durable_and_keeps_learning_epoch_degraded(self):
        bridge = TrainingBridge.__new__(TrainingBridge)
        bridge.root = self.root
        bridge.dropped = 7
        bridge._persisted_dropped = 0
        bridge.error = None
        self.assertTrue(bridge._persist_drop_count())
        status = json.loads((self.root / "recorder_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["dropped_total"], 7)

        self.start()
        self.assertEqual(self.bridge.dropped, 7)
        self.assertIn("recorded gaps", self.bridge.error)

    def test_legacy_checkpoint_drop_count_migrates_to_durable_sidecar(self):
        engine = PaperTrainingEngine(self.root / "training.json")
        engine.state["recording_drops_total"] = 9
        engine.save()

        self.start()
        deadline = time.monotonic() + 2
        while not (self.root / "recorder_status.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        saved = json.loads((self.root / "recorder_status.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["dropped_total"], 9)
        self.assertEqual(self.bridge.dropped, 9)
        self.assertIn("recorded gaps", self.bridge.error)

    def test_recent_duplicate_ids_are_restored_from_durable_training_state(self):
        engine = PaperTrainingEngine(self.root / "training.json")
        engine.state["seen_ids"] = ["persisted-evidence"]
        engine.save()
        self.start()
        self.assertTrue(self.bridge.submit({"id": "persisted-evidence"}))
        self.assertEqual(self.bridge.queue.qsize(), 0)
        self.assertEqual(self.bridge.coalesced, 1)
        self.assertEqual(self.bridge.dropped, 0)

    def test_submit_stays_nonblocking_when_child_process_is_not_progressing(self):
        self.start()
        self.wait_for(lambda s:s["unique_observations"] == 0)
        # A writer may still work while the learner is blocked or expensive;
        # submitting does not inspect/join/wait for the learner subprocess.
        with patch.object(self.bridge.process, "wait", side_effect=AssertionError("wait on submit")):
            at = int(time.time()*1000)
            start = time.monotonic()
            self.observe(at)
            self.assertLess(time.monotonic()-start, .1)
        self.wait_for(lambda s:s["unique_observations"] == 1)

    def test_cached_unknown_future_and_wrong_identity_proof_does_not_become_fresh(self):
        bridge = TrainingBridge.__new__(TrainingBridge)
        bridge.cache = {}; bridge.lock = threading.Lock(); bridge.dropped = 0; bridge.error = None
        captured = []
        bridge.submit = lambda x:captured.append(x) or True
        at = int(time.time()*1000)
        coin, flow, safety, price, quotes = inputs(at)
        quotes["entry"]["quoted_at"] = at+1000
        bridge.observe(coin, flow, safety=safety, validation=price, quotes=quotes, now=at)
        self.assertFalse(captured[-1]["execution"]["buy_route"])
        quotes = inputs(at)[4]
        quotes["entry"]["raw_quote"]["outputMint"] = "A"*32
        bridge.observe(coin, flow, quotes=quotes, now=at+1000)
        self.assertFalse(captured[-1]["execution"]["buy_route"])
        bridge.observe(coin, flow, quotes=inputs(at)[4], now=at+1000)
        self.assertTrue(captured[-1]["execution"]["buy_route"])
        bridge.observe(coin, flow, validation=price, now=at+31000)
        self.assertFalse(captured[-1]["execution"]["price_verified"])
        # Re-reading the same old provider pass did not replace its source age.
        self.assertEqual(bridge.cache[(MINT,PAIR)]["price_checked_at"], at)
        price["mint"] = "B"*32; price["reference_received_at"] = at+31000
        bridge.observe(coin, flow, validation=price, now=at+31000)
        self.assertFalse(captured[-1]["execution"]["price_verified"])
        self.assertTrue(all(x["flow"]["latest_at"] == at for x in captured))

    def test_different_proof_events_in_same_millisecond_have_different_ids(self):
        bridge = TrainingBridge.__new__(TrainingBridge)
        bridge.cache = {}; bridge.lock = threading.Lock(); bridge.dropped = 0; bridge.error = None
        captured = []
        bridge.submit = lambda x:captured.append(x) or True
        at = int(time.time()*1000)
        coin, flow, safety, price, quotes = inputs(at)
        bridge.observe(coin, flow, now=at)
        bridge.observe(coin, flow, safety=safety, validation=price, quotes=quotes, now=at)
        self.assertNotEqual(captured[0]["id"], captured[1]["id"])

    def test_recording_errors_return_false_and_do_not_interrupt_primary_exit(self):
        bridge = TrainingBridge.__new__(TrainingBridge)
        bridge.cache = {}; bridge.lock = threading.Lock(); bridge.dropped = 0; bridge.error = None
        bridge.submit = lambda x:True
        at = int(time.time()*1000)
        coin, flow, *_ = inputs(at)
        flow["buy_usd"] = float("nan")
        self.assertFalse(bridge.observe(coin, flow, now=at))
        self.assertEqual(bridge.dropped, 1)
        self.assertIn("Observation refused", bridge.error)


class RouteEvidenceAndCosts(unittest.TestCase):
    def setUp(self):
        self.bridge = TrainingBridge.__new__(TrainingBridge)
        self.bridge.cache = {}
        self.bridge.lock = threading.Lock()
        self.bridge.dropped = 0
        self.bridge.error = None
        self.rows = []
        self.bridge.submit = lambda row:self.rows.append(row) or True
        self.at = 1_000_000
        self.coin,self.flow,self.safety,self.price,self.quotes = inputs(self.at)

    def observe(self,quotes=None,at=None,**kwargs):
        self.assertTrue(self.bridge.observe(self.coin,self.flow,safety=self.safety,validation=self.price,
            quotes=self.quotes if quotes is None else quotes,now=self.at if at is None else at,**kwargs))
        return self.rows[-1]

    def test_real_main_pacing_2350ms_preflight_sale_seeds_model(self):
        self.quotes['exit']['quoted_at']=self.at-2350
        row=self.observe()
        self.assertTrue(row['execution']['buy_route'])
        self.assertTrue(row['execution']['sell_route'])
        self.assertEqual(row['execution']['buy_verified_at'],self.at)
        self.assertEqual(row['execution']['sell_verified_at'],self.at-2350)
        self.assertEqual(row['quotes'],[])
        self.assertFalse(row['execution']['route_evidence_is_execution_quote'])
        with tempfile.TemporaryDirectory() as root:
            engine=PaperTrainingEngine(Path(root)/'state.json')
            simulated=engine._model(row,'buy',20_000_000)
        self.assertIsNotNone(simulated)
        self.assertEqual(simulated['market_price'],1)
        self.assertEqual(simulated['quoted_at'],self.at)

    def test_independent_receipts_do_not_require_one_batch(self):
        self.observe(quotes={'exit':self.quotes['exit']})
        row=self.observe(quotes={'entry':self.quotes['entry']},at=self.at+2350)
        self.assertTrue(row['execution']['buy_route'])
        self.assertTrue(row['execution']['sell_route'])
        self.assertEqual(row['execution']['sell_verified_at'],self.at)

    def test_mark_refresh_never_renews_old_buy_proof(self):
        self.observe()
        mark=copy.deepcopy(self.quotes['exit']);mark['quoted_at']=self.at+31000
        row=self.observe(quotes={'mark':mark},at=self.at+31000)
        self.assertFalse(row['execution']['buy_route'])
        self.assertTrue(row['execution']['sell_route'])
        self.assertEqual(row['execution']['buy_verified_at'],self.at)
        self.assertEqual(row['execution']['sell_verified_at'],self.at+31000)

    def test_stale_future_wrong_mint_and_wrong_pool_refuse_route_evidence(self):
        cases=[]
        for timestamp in (self.at-30001,self.at+1):
            q=copy.deepcopy(self.quotes);q['entry']['quoted_at']=timestamp;cases.append(q)
        q=copy.deepcopy(self.quotes);q['entry']['raw_quote']['outputMint']='A'*44;cases.append(q)
        q=copy.deepcopy(self.quotes);q['entry']['raw_quote']['routePlan'][0]['swapInfo']['ammKey']='B'*44;cases.append(q)
        for q in cases:
            with self.subTest(quote=q):
                row=self.observe(quotes=q)
                self.assertFalse(row['execution']['buy_route'])
        q=copy.deepcopy(self.quotes);q['exit']['raw_quote']['routePlan'][0]['swapInfo']['ammKey']='B'*44
        row=self.observe(quotes=q)
        self.assertFalse(row['execution']['sell_route'])
        self.assertTrue(row['execution']['buy_route'])

    def test_invalid_new_quote_clears_earlier_route_and_explicit_failure_wins(self):
        self.observe()
        q=copy.deepcopy(self.quotes['entry']);q['quoted_at']=self.at+10
        row=self.observe(quotes={'entry':q})
        self.assertFalse(row['execution']['buy_route'])
        self.observe()
        row=self.observe(quotes={},reasons=['exit_quote'])
        self.assertFalse(row['execution']['sell_route'])

    def test_capture_network_budget_mark_fee_decimals_and_account_rent(self):
        self.coin.update(priceNative=1/350,quoteTokenAddress=training_bridge.SOL)
        self.safety['metrics']['token_account_rent_lamports']=1_650_000
        self.quotes['entry'].pop('entry_account_reserve_usd')
        self.quotes['exit']['network_fee_usd']=.09
        with patch.dict('os.environ',{'NEO_EXEC_NETWORK_FEE_SOL':'0.0001'}): row=self.observe()
        execution=row['execution']
        self.assertAlmostEqual(execution['entry_network_fee_usd'],.035)
        self.assertAlmostEqual(execution['exit_network_fee_usd'],.09)
        self.assertAlmostEqual(execution['entry_account_reserve_usd'],.5775)
        self.assertEqual(execution['decimals'],6)
        self.assertTrue(execution['costs_known'])
        self.assertEqual(execution['sol_reference_at'],self.at)
        self.assertEqual(row['source']['cached_route_evidence']['sell']['network_fee_usd'],.09)

    def test_missing_cost_evidence_is_unknown_not_free(self):
        self.quotes['entry'].pop('entry_network_fee_usd')
        self.quotes['entry'].pop('entry_account_reserve_usd')
        self.quotes['exit'].pop('network_fee_usd')
        row=self.observe()
        self.assertFalse(row['execution']['costs_known'])
        self.assertIsNone(row['execution']['network_fee_usd'])
        self.assertIsNone(row['execution']['entry_account_reserve_usd'])
        with tempfile.TemporaryDirectory() as root:
            engine=PaperTrainingEngine(Path(root)/'state.json')
            self.assertIsNone(engine._model(row,'buy',20_000_000))

    def test_complete_recorded_fee_legs_replace_pump_guess_with_explicit_estimate(self):
        self.coin['dexId']='pumpswap'
        raw=self.quotes['entry']['raw_quote']
        raw['inAmount']='20000000'
        raw['routePlan'][0]['swapInfo'].update(feeMint=USDC,feeAmount='60000')
        sale_raw=self.quotes['exit']['raw_quote']
        sale_raw['inAmount']='20000000'
        sale_raw['routePlan'][0]['swapInfo'].update(feeMint=USDC,feeAmount='60000')
        row=self.observe()
        self.assertEqual(row['execution']['dex_fee_bps'],30)
        self.assertEqual(row['execution']['fee_basis'],'RECORDED_ROUTE_FEE_ESTIMATE_V1')
        self.assertEqual(row['execution']['fee_observations'][0]['legs'][0]['raw_amount'],60000)
        raw['routePlan'][0]['swapInfo'].pop('feeAmount')
        sale_raw['routePlan'][0]['swapInfo'].pop('feeAmount')
        row=self.observe()
        self.assertEqual(row['execution']['dex_fee_bps'],125)
        self.assertIn('NOT_GUARANTEED_BOUND',row['execution']['fee_basis'])

    def test_zero_fee_on_one_side_never_makes_unknown_opposite_side_free(self):
        self.coin['dexId']='pumpswap'
        raw=self.quotes['entry']['raw_quote']
        raw['inAmount']='20000000'
        raw['routePlan'][0]['swapInfo'].update(feeMint=USDC,feeAmount='0')
        row=self.observe()
        self.assertEqual(row['execution']['entry_dex_fee_bps'],0)
        self.assertEqual(row['execution']['exit_dex_fee_bps'],125)
        self.assertEqual(row['execution']['dex_fee_bps'],125)
        self.assertIn('UNKNOWN_SIDE_ASSUMPTION',row['execution']['fee_basis'])

    def test_independent_books_do_not_inherit_main_free_existing_account(self):
        self.coin.update(priceNative=1/350,quoteTokenAddress=training_bridge.SOL)
        self.safety['metrics']['token_account_rent_lamports']=1_650_000
        self.quotes['entry']['entry_account_reserve_usd']=0
        row=self.observe()
        self.assertAlmostEqual(row['execution']['entry_account_reserve_usd'],.5775)
        self.assertEqual(row['execution']['observed_main_entry_account_reserve_usd'],0)
        self.assertIn('NO_INVENTORY_OR_RECOVERY',row['execution']['account_reserve_basis'])
        self.coin.pop('priceNative')
        self.safety['metrics'].pop('token_account_rent_lamports')
        row=self.observe()
        self.assertIsNone(row['execution']['entry_account_reserve_usd'])
        self.assertFalse(row['execution']['costs_known'])


if __name__ == "__main__":
    unittest.main()
