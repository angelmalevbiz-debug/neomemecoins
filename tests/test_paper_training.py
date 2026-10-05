"""Synthetic correctness fixtures ONLY; they do not establish trading edge."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from paper_training import AsyncTrainingRecorder, BASELINE, MODEL, PaperTrainingEngine, normalize_quote

NOW = 1_800_000_000_000
MINT = "So11111111111111111111111111111111111111112"
PAIR = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def row(at, price=1.0, score=90.0, mint=MINT, sell=True, identifier=None):
    return {"id": identifier or "%s:%s:%s:%s" % (at, mint, price, score),
            "available_at": at, "observed_at": at, "source": "synthetic correctness fixture",
            "coin": {"address": mint, "pairAddress": PAIR, "priceUsd": price,
                     "liquidityUsd": 1_000_000.0, "updatedAt": at, "score": score, "symbol": "TEST"},
            "flow": {"fresh": True, "latest_at": at, "trades": 10,
                     "unique_wallets": 5, "buy_usd": 100, "sell_usd": 20, "ratio": 5},
            "safety": {"allowed": True, "mint": mint, "pair": PAIR, "checked_at": at},
            "execution": {"mode": MODEL, "mint": mint, "pair": PAIR,
                          "decimals": 6, "dex_fee_bps": 30,
                          "network_fee_usd": .01, "buy_route": True, "sell_route": sell,
                          "verified_at": at, "price_verified": True}}


def episode(at, mint, score=90, exit_price=1.15):
    # Decision -> next observed entry after latency -> exit decision -> next
    # observed liquidation. There is no fill at a chosen stop/target price.
    return [row(at, score=score, mint=mint), row(at + 1000, score=score, mint=mint),
            row(at + 2000, price=exit_price, score=score, mint=mint),
            row(at + 3000, price=exit_price, score=score, mint=mint)]


def book(engine, name="CONTROL"):
    return engine.state["books"][name]


class PaperTrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "training.json"

    def engine(self, **config):
        # $10 fixtures keep simple arithmetic; production default is $20 with
        # explicit real account-reserve costs from the shared recording bridge.
        return PaperTrainingEngine(self.path, {"notional": 10, **config})

    def test_next_observation_and_latency_no_favourable_stale_fill(self):
        e = self.engine()
        e.ingest(row(NOW))
        self.assertEqual(book(e)["positions"], {})
        e.ingest(row(NOW + 200, price=.50))
        self.assertEqual(book(e)["positions"], {})
        e.ingest(row(NOW + 1000, price=2.0))
        p = book(e)["positions"][MINT]
        self.assertEqual(p["entry"]["market_price"], 2.0)
        self.assertLess(p["token_raw"], 5_000_000)
        self.assertEqual(p["decision_at"], NOW)
        self.assertEqual(p["opened_at"], NOW + 1000)

    def test_entry_rent_and_side_specific_network_costs_count_once(self):
        e = self.engine(notional=20)
        for at, price in ((NOW,1),(NOW+1000,1),(NOW+2000,1.2),(NOW+3000,1.2)):
            x=row(at,price=price)
            x['execution'].update(entry_network_fee_usd=.03,exit_network_fee_usd=.07,
                                  entry_account_reserve_usd=.165)
            e.ingest(x)
            if at==NOW+1000:
                self.assertAlmostEqual(book(e)['cash'],500-20-.03-.165)
                self.assertAlmostEqual(book(e)['positions'][MINT]['committed_usd'],20.195)
        trade=book(e)['trades'][0]
        self.assertAlmostEqual(trade['entry']['network_fee_usd'],.03)
        self.assertAlmostEqual(trade['entry']['entry_account_reserve_usd'],.165)
        self.assertAlmostEqual(trade['exit']['network_fee_usd'],.07)
        self.assertAlmostEqual(trade['exit']['entry_account_reserve_usd'],0)
        self.assertAlmostEqual(book(e)['cash']-500,trade['pnl_usd'])
        self.assertAlmostEqual(trade['pnl_usd'],trade['exit']['output_raw']/1e6-.07-20.195)

    def test_known_high_roundtrip_fees_are_rejected_before_any_trade_attempt(self):
        e=self.engine(notional=20)
        for at in (NOW,NOW+1000):
            x=row(at)
            x['execution'].update(entry_network_fee_usd=.03,exit_network_fee_usd=.03,
                                  entry_account_reserve_usd=1)
            e.ingest(x)
        self.assertFalse(book(e)['pending'])
        self.assertFalse(book(e)['positions'])
        self.assertFalse(book(e)['failed'])
        self.assertEqual(book(e)['cash'],500)
        self.assertIn('roundtrip_cost',book(e)['rejected'][-1]['reasons'])

    def test_different_observed_entry_and_exit_dex_fees_are_not_interchanged(self):
        e=self.engine(notional=20)
        for at,price in ((NOW,1),(NOW+1000,1),(NOW+2000,1.2),(NOW+3000,1.2)):
            x=row(at,price=price)
            x['execution'].update(entry_dex_fee_bps=0,exit_dex_fee_bps=50,
                                  dex_fee_bps=125)
            e.ingest(x)
        trade=book(e)['trades'][0]
        self.assertEqual(trade['entry']['dex_fee_bps'],0)
        self.assertEqual(trade['exit']['dex_fee_bps'],50)
        self.assertGreater(trade['exit']['dex_fee_input'],0)
        self.assertEqual(trade['entry']['dex_fee_input'],0)

    def test_future_features_and_stale_safety_fail_closed(self):
        e = self.engine()
        x = row(NOW)
        x["coin"]["updatedAt"] = NOW + 1000
        x["flow"]["latest_at"] = NOW + 1000
        e.ingest(x)
        self.assertFalse(book(e)["pending"])
        self.assertIn("stale_or_future_market", book(e)["rejected"][-1]["reasons"])
        x = row(NOW + 1000)
        x["safety"]["checked_at"] = NOW - 60000
        e.ingest(x)
        self.assertFalse(book(e)["pending"])
        x = row(NOW + 2000)
        x["observed_at"] = NOW + 2001
        self.assertFalse(e.ingest(x))

    def test_malformed_nonfinite_recording_is_rejected_without_corrupting_state(self):
        e = self.engine()
        x = row(NOW)
        x["flow"]["buy_usd"] = float("nan")
        self.assertFalse(e.ingest(x))
        self.assertFalse(e.ingest([1, 2, 3]))
        restored = PaperTrainingEngine(self.path)
        self.assertEqual(restored.state["invalid_observations"], 2)
        self.assertEqual(restored.state["seen_ids"], [])

    def test_resubmitted_old_market_snapshot_cannot_fill_latency_order(self):
        e = self.engine()
        e.ingest(row(NOW))
        delayed = row(NOW + 1000, price=.5)
        delayed["coin"]["updatedAt"] = NOW
        e.ingest(delayed)
        self.assertFalse(book(e)["positions"])
        e.ingest(row(NOW + 2000, price=2))
        self.assertEqual(book(e)["positions"][MINT]["entry"]["market_price"], 2)

    def test_disappeared_token_pending_order_expires_on_shared_clock(self):
        e = self.engine(order_timeout_ms=1000)
        e.ingest(row(NOW))
        e.ingest(row(NOW+2000, mint="A"*32))
        self.assertNotIn(MINT, book(e)["pending"])
        self.assertEqual(book(e)["failed"][0]["reason"], "no_matching_market_observation_after_latency")
        self.assertEqual(book(e)["failed"][0]["side"], "buy")
        self.assertLess(book(e)["cash"], 500)

    def test_disappeared_open_token_becomes_conservative_loss_pending_exit(self):
        e = self.engine(order_timeout_ms=1000)
        e.ingest(row(NOW)); e.ingest(row(NOW+1000))
        e.ingest(row(NOW+30000, mint="A"*32))
        self.assertEqual(book(e)["positions"][MINT]["mark_usd"], 0)
        self.assertEqual(book(e)["pending"][MINT]["side"], "sell")
        e.ingest(row(NOW+32000, mint="A"*32))
        self.assertEqual(book(e)["failed"][0]["side"], "sell")
        self.assertEqual(len(book(e)["positions"]), 2)  # independent second token can fill
        self.assertEqual(len(book(e)["trades"]), 0)

    def test_replay_order_uses_availability_not_source_time(self):
        e = self.engine()
        first, second = row(NOW), row(NOW + 1000, price=2)
        second["observed_at"] = NOW - 1000  # late-arriving fact
        e.replay([second, first])
        p = book(e)["positions"][MINT]
        self.assertEqual(p["entry_observation_id"], second["id"])
        self.assertEqual(p["entry"]["market_price"], 2)
        self.assertFalse(e.ingest(row(NOW - 1000)))

    def test_separate_books_multiple_parallel_positions_full_loss_limits(self):
        e = self.engine()
        for index, mint in enumerate((MINT, "A" * 32, "B" * 32)):
            e.ingest(row(NOW + index * 2000, mint=mint))
            e.ingest(row(NOW + index * 2000 + 1000, mint=mint))
        self.assertEqual(len(book(e)["positions"]), 3)
        self.assertAlmostEqual(book(e)["cash"], 500 - 3 * 10.01)
        self.assertAlmostEqual(book(e, "STRICT")["cash"], 500 - 3 * 10.01)
        book(e)["cash"] = 1
        self.assertGreater(book(e, "STRICT")["cash"], 400)
        snap = e.snapshot()
        self.assertEqual(len(snap["books"]), 9)
        self.assertNotIn("aggregate_return", snap)
        self.assertEqual(snap["simulation_count"], 24)
        self.assertEqual(snap["unique_market_episodes"], 3)

    def test_gapped_stop_uses_delayed_adverse_fill_and_retains_costs(self):
        e = self.engine()
        e.ingest(row(NOW)); e.ingest(row(NOW + 1000))
        e.ingest(row(NOW + 2000, price=.90))
        self.assertEqual(len(book(e)["trades"]), 0)
        e.ingest(row(NOW + 3000, price=.60))
        t = book(e)["trades"][0]
        self.assertLess(t["pnl_pct"], -40)
        self.assertEqual(t["exit"]["market_price"], .60)
        self.assertEqual(t["exit_reason"], "STOP_NET")
        self.assertGreater(t["exit_analysis"]["stop_gap_pct"], 30)
        self.assertAlmostEqual(book(e)["cash"] - 500, t["pnl_usd"])
        self.assertEqual(book(e)["positions"], {})

    def test_unknown_sell_route_retains_open_loser_failed_retries_and_restart(self):
        e = self.engine(order_timeout_ms=1000)
        e.ingest(row(NOW)); e.ingest(row(NOW + 1000))
        e.ingest(row(NOW + 2000, sell=False))
        e.ingest(row(NOW + 4000, sell=False))
        self.assertEqual(len(book(e)["positions"]), 1)
        self.assertEqual(len(book(e)["trades"]), 0)
        self.assertEqual(book(e)["positions"][MINT]["mark_usd"], 0)
        self.assertEqual(len(book(e)["failed"]), 1)
        self.assertAlmostEqual(e.stats(book(e))["net_pnl_usd"], -10.03)
        self.assertEqual(e.stats(book(e))["wins"], 0)
        restored = PaperTrainingEngine(self.path)
        self.assertEqual(restored.snapshot(), e.snapshot())
        restored.ingest(row(NOW + 5000, price=.80))
        self.assertEqual(len(book(restored)["trades"]), 1)
        self.assertLess(book(restored)["trades"][0]["pnl_usd"], -2)
        self.assertAlmostEqual(book(restored)["cash"] - 500, book(restored)["trades"][0]["pnl_usd"])

    def test_replayed_duplicates_do_not_become_independent_evidence(self):
        e = self.engine()
        rows = episode(NOW, MINT)
        e.replay(rows)
        count = e.snapshot()["simulation_count"]
        e.replay(rows)
        self.assertEqual(e.snapshot()["simulation_count"], count)
        self.assertEqual(e.snapshot()["unique_market_episodes"], 1)
        # Distinct decision IDs within the same market episode are still one cluster.
        e.ingest(row(NOW + 5000, identifier="different"))
        self.assertEqual(e.snapshot()["unique_market_episodes"], 1)

    def test_rejected_signal_path_is_not_trade_or_executed_profit(self):
        e = self.engine()
        e.ingest(row(NOW, score=40)); e.ingest(row(NOW + 1000, score=40, price=2))
        s = e.stats(book(e))
        self.assertEqual(s["completed_trades"], 0)
        self.assertEqual(s["net_pnl_usd"], 0)
        self.assertEqual(s["rejected_signals"], 2)
        rejected = book(e)["rejected"][0]
        self.assertEqual(rejected["outcome"], "not_executed")
        self.assertEqual(rejected["subsequent_market_return_pct"], 100)
        self.assertIn("not_executable", rejected["missed_opportunity"])

    def test_restart_pending_and_open_exit_steps_equivalent(self):
        for boundary in (1, 2, 3):
            with self.subTest(boundary=boundary):
                left = PaperTrainingEngine(Path(self.tmp.name) / ("left%d.json" % boundary))
                right_path = Path(self.tmp.name) / ("right%d.json" % boundary)
                right = PaperTrainingEngine(right_path)
                rows = episode(NOW, MINT, exit_price=.8)
                left.replay(rows)
                right.replay(rows[:boundary])
                right = PaperTrainingEngine(right_path)
                right.replay(rows[boundary:])
                self.assertEqual(left.snapshot(), right.snapshot())

    def learning_engine(self):
        return self.engine(min_train_episodes=2, train_every_episodes=1,
                           min_validation_episodes=2, min_validation_trades=2,
                           min_validation_days=1,
                           min_improvement_usd=.5, embargo_ms=1000,
                           require_positive_ci=False, rollback_min_episodes=2,
                           rollback_underperformance_usd=.5)

    def train(self, e):
        rows = []
        for index, (score, price) in enumerate(((75, .8), (90, 1.15), (90, 1.15))):
            rows.extend(episode(NOW + index * 10000, chr(65 + index) * 32, score, price))
        e.replay(rows)
        return NOW + 23000

    def validate(self, e, start):
        for index, (score, price) in enumerate(((75, .8), (90, 1.15), (90, 1.15))):
            e.replay(episode(start + 10000 + index * 10000, chr(68 + index) * 32, score, price))

    def test_training_freezes_then_future_validation_changes_next_decision(self):
        e = self.learning_engine()
        cutoff = self.train(e)
        self.assertEqual(e.state["active_version"], "v0-control")
        self.assertEqual(e.state["training"]["candidate"], "STRICT")
        self.assertEqual(book(e, "LEARNER")["params"], BASELINE)
        self.assertEqual(book(e, "VALIDATE_CANDIDATE")["trades"], [])
        # Already-used token episode cannot enter held-out validation.
        e.ingest(row(cutoff + 2000, mint="C" * 32))
        self.assertFalse(book(e, "VALIDATE_CANDIDATE")["pending"])
        # Recreate from saved frozen candidate before proceeding.
        e = PaperTrainingEngine(self.path)
        self.validate(e, cutoff)
        self.assertNotEqual(e.state["active_version"], "v0-control")
        self.assertEqual(book(e, "LEARNER")["params"]["min_score"], 80)
        self.assertEqual(book(e)["params"], BASELINE)
        last = e.state["last_training"]
        self.assertEqual(last["status"], "approved_isolated_paper")
        self.assertGreater(last["validation_start"], last["train_cutoff"])
        after = cutoff + 60000
        e.ingest(row(after, score=75, mint="G" * 32))
        self.assertIn("G" * 32, book(e)["pending"])
        self.assertNotIn("G" * 32, book(e, "LEARNER")["pending"])
        restored = PaperTrainingEngine(self.path)
        self.assertEqual(restored.state["active_version"], e.state["active_version"])
        self.assertEqual(book(restored, "LEARNER")["params"]["min_score"], 80)

    def test_future_validation_failure_does_not_promote_train_winner(self):
        e = self.learning_engine()
        cutoff = self.train(e)
        # High-score winners train; high-score losers validate. Positive train
        # performance cannot leak into acceptance checks on future outcomes.
        for index in range(2):
            e.replay(episode(cutoff + 10000 + index * 10000, chr(68 + index) * 32, 90, .8))
        self.assertEqual(e.state["active_version"], "v0-control")
        self.assertIn("nonpositive_net_result", e.state["last_training"]["rejected_checks"])

    def test_incomplete_recording_coverage_blocks_automatic_promotion(self):
        e = self.learning_engine()
        cutoff = self.train(e)
        gap = row(cutoff+5000, score=40, mint="Z"*32)
        gap["recording_drops_total"] = 1
        e.ingest(gap)
        self.validate(e, cutoff)
        self.assertEqual(e.state["active_version"], "v0-control")
        self.assertIn("incomplete_recording_coverage", e.state["last_training"]["rejected_checks"])
        self.assertEqual(e.snapshot()["recording_drops_total"], 1)

    def test_automatic_rollback_requires_new_episode_evidence(self):
        e = self.learning_engine()
        cutoff = self.train(e)
        self.validate(e, cutoff)
        self.assertNotEqual(e.state["active_version"], "v0-control")
        # Strict misses opportunities the frozen baseline takes, on NEW data.
        for index in range(2):
            e.replay(episode(cutoff + 60000 + index * 10000, chr(71 + index) * 32, 75, 1.15))
        self.assertEqual(e.state["active_version"], "v0-control")
        self.assertEqual(book(e, "LEARNER")["params"], BASELINE)
        self.assertEqual(e.state["versions"][-1]["status"], "automatic_rollback")

    def test_main_live_mode_and_corrupt_control_never_load(self):
        with self.assertRaisesRegex(ValueError, "PAPER"):
            self.engine(mode="LIVE")
        e = self.engine()
        saved = copy.deepcopy(e.state)
        saved["books"]["CONTROL"]["params"]["min_score"] = 40
        self.path.write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "frozen control"):
            PaperTrainingEngine(self.path)

    def test_cannot_restart_with_unapproved_parameter_mutation(self):
        e = self.engine()
        e.state["books"]["LEARNER"]["params"] = {**BASELINE, "min_score": 1}
        e.save()
        with self.assertRaisesRegex(ValueError, "approved version"):
            PaperTrainingEngine(self.path)

    def test_gold_adaptive_has_real_context_exit_and_different_outcome(self):
        e = self.engine()
        for at in (NOW, NOW + 1000):
            x = row(at)
            x["context"] = {"conviction": 80, "available_at": at}
            e.ingest(x)
        self.assertIn(MINT, book(e, "GOLD_ADAPTIVE")["positions"])
        for at in (NOW + 2000, NOW + 3000):
            x = row(at, price=.99)
            x["context"] = {"conviction": 20, "available_at": at}
            e.ingest(x)
        self.assertEqual(book(e, "GOLD_ADAPTIVE")["trades"][0]["exit_reason"], "CONVICTION_EXIT")
        self.assertTrue(book(e)["positions"])
        self.assertEqual(book(e)["trades"], [])

    def test_future_adaptive_context_cannot_affect_entry_or_exit(self):
        e = self.engine()
        x = row(NOW)
        x["context"] = {"conviction": 80, "available_at": NOW + 5000}
        e.ingest(x)
        self.assertFalse(book(e, "GOLD_ADAPTIVE")["pending"])
        self.assertTrue(book(e)["pending"])

    def test_conservative_zero_mark_enforces_daily_loss_and_entries_stop(self):
        e = self.engine(daily_loss_fraction=.01)
        e.ingest(row(NOW)); e.ingest(row(NOW + 1000))
        e.ingest(row(NOW + 2000, sell=False))
        e.ingest(row(NOW + 3000, mint="A" * 32))
        self.assertNotIn("A" * 32, book(e)["pending"])
        self.assertEqual(book(e)["halt_reason"], "daily_loss_including_unliquidatable_positions")
        self.assertGreater(e.stats(book(e))["max_drawdown_pct"], 2)

    def test_all_pending_books_keep_capital_limits_at_fill(self):
        e = self.engine(max_positions=20, max_exposure_fraction=.05)
        for i in range(4):
            e.ingest(row(NOW + i * 100, mint=chr(65+i)*32))
        self.assertEqual(len(book(e)["pending"]), 1)
        e.ingest(row(NOW + 1000, mint="A"*32))
        exposure = sum(p["committed_usd"] for p in book(e)["positions"].values())
        self.assertLessEqual(exposure, 25)
        self.assertGreaterEqual(book(e)["cash"], 0)

    def test_prefix_decisions_identical_regardless_future_market_path(self):
        left = PaperTrainingEngine(Path(self.tmp.name) / "prefix_left.json")
        right = PaperTrainingEngine(Path(self.tmp.name) / "prefix_right.json")
        prefix = [row(NOW), row(NOW + 1000)]
        for x in prefix:
            left.ingest(x); right.ingest(x)
        before = copy.deepcopy(left.state["books"])
        self.assertEqual(before, right.state["books"])
        left.replay([row(NOW + 2000, price=2), row(NOW + 3000, price=2)])
        right.replay([row(NOW + 2000, price=.2), row(NOW + 3000, price=.2)])
        self.assertEqual(left.state["books"]["CONTROL"]["trades"][0]["decision_features"],
                         right.state["books"]["CONTROL"]["trades"][0]["decision_features"])
        self.assertGreater(left.state["books"]["CONTROL"]["trades"][0]["pnl_usd"], 0)
        self.assertLess(right.state["books"]["CONTROL"]["trades"][0]["pnl_usd"], 0)

    def test_explicit_reset_only_resets_this_training_state(self):
        unrelated = Path(self.tmp.name) / "main.json"
        unrelated.write_text('{"cash":123,"history":[1]}')
        e = self.engine()
        e.replay(episode(NOW, MINT))
        snap = e.reset(initial_cash=500)
        self.assertEqual(snap["simulation_count"], 0)
        self.assertEqual(snap["unique_observations"], 0)
        self.assertEqual(book(e)["cash"], 500)
        self.assertEqual(json.loads(unrelated.read_text())["cash"], 123)

    def test_async_recorder_freezes_observations_and_flushes(self):
        path = Path(self.tmp.name) / "observations.jsonl"
        recorder = AsyncTrainingRecorder(path)
        x = row(NOW)
        self.assertTrue(recorder.submit(x))
        x["coin"]["priceUsd"] = 999
        self.assertTrue(recorder.close())
        self.assertEqual(json.loads(path.read_text())["coin"]["priceUsd"], 1)
        self.assertEqual(recorder.snapshot()["written"], 1)
        self.assertFalse(recorder.submit(x))

    def test_quote_mode_requires_exact_quantity_and_fresh_next_quote(self):
        e = self.engine()
        def quote(at, side, amount, output, floor=None):
            return {"side": side, "mint": MINT, "pair": PAIR, "input_raw": amount,
                    "output_raw": output, "floor_raw": floor or output,
                    "quoted_at": at, "available_at": at, "route": True,
                    "price_impact_pct": .01, "network_fee_usd": .01,
                    "entry_account_reserve_usd": 0.0,
                    "cost_basis": "EXPLICIT_SYNTHETIC_PAPER_COSTS",
                    "fee_included": True, "decimals": 6}
        x = row(NOW); x.pop("execution")
        x["quotes"] = [quote(NOW, "buy", 10000000, 10000000),
                       quote(NOW, "sell", 10000000, 9990000)]
        e.ingest(x)
        y = copy.deepcopy(x); y.update(id="next", available_at=NOW + 1000, observed_at=NOW + 1000)
        y["coin"]["updatedAt"] = NOW + 1000
        # Copying a prior cheap quote does not fill the delayed buy.
        e.ingest(y)
        self.assertFalse(book(e)["positions"])
        z = row(NOW + 2000); z.pop("execution")
        z["quotes"] = [quote(NOW + 2000, "buy", 10000000, 5000000),
                       quote(NOW + 2000, "sell", 5000001, 9980000)]
        e.ingest(z)
        self.assertFalse(book(e)["positions"])
        self.assertEqual(book(e)["failed"][-1]["reason"], "no_sell_preflight")

    def test_normalize_native_jupiter_quote_preserves_integer_units(self):
        native = {"raw_quote": {"inAmount": "10000000", "outAmount": "1234567",
                  "otherAmountThreshold": "1200000", "_received_at": NOW,
                  "inputMint": "USDC", "outputMint": MINT, "swapMode": "ExactIn",
                  "routePlan": [{"swapInfo": {"ammKey": PAIR}}], "priceImpactPct": ".001"}}
        result = normalize_quote(native, side="buy", mint=MINT, pair=PAIR, decimals=6)
        self.assertEqual(result["input_raw"], "10000000")
        self.assertEqual(result["floor_raw"], "1200000")
        self.assertEqual(result["quoted_at"], NOW)
        self.assertAlmostEqual(result["price_impact_pct"], .1)
        self.assertIsNone(result["network_fee_usd"])
        self.assertIsNone(result["entry_account_reserve_usd"])
        self.assertEqual(result["network_fee_basis"], "UNKNOWN")

    def test_imported_quotes_require_explicit_network_fee_and_entry_reserve(self):
        e=self.engine()
        x=row(NOW);x.pop('execution')
        buy={'side':'buy','mint':MINT,'pair':PAIR,'input_raw':10000000,
             'output_raw':10000000,'floor_raw':10000000,'quoted_at':NOW,
             'available_at':NOW,'route':True,'price_impact_pct':.01,
             'fee_included':True,'decimals':6}
        x['quotes']=[buy]
        self.assertIsNone(e._execution(x,'buy',10000000))
        buy['network_fee_usd']=0.0
        self.assertIsNone(e._execution(x,'buy',10000000))
        buy['entry_account_reserve_usd']=0.0
        self.assertIsNotNone(e._execution(x,'buy',10000000))
        buy['network_fee_usd']=None
        self.assertIsNone(e._execution(x,'buy',10000000))

    def test_cli_import_replay_compare_preserves_real_recording_provenance(self):
        # CLI correctness on labelled fixtures; no financial claim is made.
        script = Path(__file__).resolve().parents[1] / "scripts" / "paper_training.py"
        source = Path(self.tmp.name) / "source.jsonl"
        dataset = Path(self.tmp.name) / "dataset.jsonl"
        target = Path(self.tmp.name) / "cli_state.json"
        snap = Path(self.tmp.name) / "cli_snapshot.json"
        config = Path(self.tmp.name) / "cli_config.json"
        config.write_text('{"notional":10}', encoding="utf-8")
        rows = episode(NOW, MINT)
        source.write_text("".join(json.dumps(x)+"\n" for x in reversed(rows)), encoding="utf-8")
        imported = subprocess.run([sys.executable, str(script), "import", "--input", str(source),
                                   "--output", str(dataset)], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(imported.stdout)["observations"], 4)
        replayed = subprocess.run([sys.executable, str(script), "replay", "--input", str(dataset),
                                   "--state", str(target), "--snapshot", str(snap), "--config", str(config)],
                                  capture_output=True, text=True, check=True)
        summary = json.loads(replayed.stdout)
        self.assertEqual(summary["unique_observations"], 4)
        self.assertEqual(summary["unique_market_episodes"], 1)
        self.assertEqual(summary["books"][0]["completed_trades"], 1)
        self.assertEqual(summary["books"][0]["recent_trades"][0]["decision_features"]["coin"]["priceUsd"], 1)
        compared = subprocess.run([sys.executable, str(script), "compare", "--state", str(target)],
                                  capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(compared.stdout)["control_comparison"], summary["control_comparison"])
        self.assertEqual(json.loads(snap.read_text())["dataset"]["sha256"], summary["dataset"]["sha256"])


if __name__ == "__main__":
    unittest.main()
