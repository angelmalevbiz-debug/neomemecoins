"""Replay identity fixture REPLAY_IDENTITY_V1: the offline replay must book the
same trade the live engine booked from the same recorded rows.

The rows are synthetic but follow the recorded observation schema and the live
timing the research measured on 2026-10-07: a three-step preflight whose first
buy quote is ~4.65 s old at the recorded row, a cached risk pass ~47 s old, a
candidate snapshot older than the 8 s commit limit that the live commit
refreshed from the scanner feed, and a signal-stage observation without quote
evidence recorded just before the quote sequence. No network, no profit claim.
"""
import argparse
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import engine_execution as execution
import engine_exit_policy as exit_policy
import promoted_entry_guard as promoted_guard
from main_replay import MainReplay, parse_exit_variant

FIXTURE_VERSION = 'REPLAY_IDENTITY_V1'
FIXTURES = Path(__file__).resolve().parent/'fixtures'
ROWS_PATH = FIXTURES/'replay_identity_v1.jsonl'
EXPECTED_PATH = FIXTURES/'replay_identity_v1.expected.json'
MINT, POOL = 'A'*44, 'B'*44
USDC = execution.USDC
T0 = 1_791_410_000_000
NOTIONAL_RAW = 200_000_000
SOL_USD = 116.0
RENT_LAMPORTS = 1_650_000
NETWORK_FEE_USD = .03
RENT_USD = RENT_LAMPORTS/1e9*SOL_USD
SAFETY_CHECKED_AT = T0-47_000          # cached risk pass, older than the old 30 s literal
CANDIDATE_UPDATED_AT = T0-8_300        # candidate snapshot older than MAX_SIGNAL_AGE_MS
FIRST_BUY_AT, SALE_AT, FINAL_AT = T0-4_650, T0-2_450, T0-255


def coin(updated_at, liquidity=200_000):
    return {'address': MINT, 'pairAddress': POOL, 'dexId': 'pumpswap', 'symbol': 'FIXTURE',
            'name': 'Replay identity fixture', 'priceUsd': .0213, 'priceNative': .000184,
            'liquidityUsd': liquidity, 'marketCap': 1_000_000, 'fdv': 1_000_000, 'ageMinutes': 60,
            'score': 95, 'riskScore': 10, 'priceChange': {'m5': 5, 'h1': 8},
            'txns': {'m5': {'buys': 10, 'sells': 3}}, 'volume': {'m5': 5000, 'h1': 50_000},
            'signals': [], 'pairCreatedAt': T0-3_600_000, 'updatedAt': updated_at}


def flow(decision_at, *, trades=28, wallets=20, buy_usd=900., sell_usd=600., latest_event_at=None):
    latest_event_at = decision_at-1_500 if latest_event_at is None else latest_event_at
    return {'quality': 'COMPLETE', 'decision_at': decision_at, 'fresh': True, 'seconds': 30,
            'latest_at': decision_at, 'trades': trades, 'buys': trades-4, 'sells': 4,
            'buy_usd': buy_usd, 'sell_usd': sell_usd, 'net_buy_usd': buy_usd-sell_usd,
            'buy_sell_usd_ratio': round(buy_usd/max(sell_usd, 1.), 2), 'ratio': round(buy_usd/max(sell_usd, 1.), 2),
            'unique_wallets': wallets, 'buyer_wallets': max(wallets-4, 1), 'seller_wallets': 4,
            'max_sell_usd': 120, 'max_buy_usd': 300, 'whale_buy_usd': 0, 'whale_sell_usd': 0,
            'coverage': {'address': MINT, 'pairAddress': POOL, 'status': 'COMPLETE',
                         'complete_since_ms': T0-600_000, 'last_poll_at': decision_at},
            'verified_flow': {'source': promoted_guard.FLOW_SOURCE, 'coverage_status': 'COMPLETE',
                              'window_ms': promoted_guard.FLOW_WINDOW_MS, 'address': MINT, 'pairAddress': POOL,
                              'window_at': decision_at, 'latest_event_at': latest_event_at,
                              'available_at': decision_at, 'trades': trades, 'unique_wallets': wallets,
                              'buy_usd': buy_usd, 'sell_usd': sell_usd}}


def safety(checked_at):
    return {'allowed': True, 'checked_at': checked_at, 'mint': MINT, 'pair': POOL,
            'evidence': {'version': 'FIXTURE', 'status': 'pass', 'mint': MINT, 'pair': POOL,
                         'checked_at': checked_at, 'reasons': [],
                         'metrics': {'decimals': 6, 'sol_usd': SOL_USD,
                                     'token_account_rent_lamports': RENT_LAMPORTS}}}


def price(reference_received_at):
    return {'status': 'pass', 'observed_price': .0213, 'reference_price': .0213, 'divergence_pct': 0.,
            'reference_received_at': reference_received_at, 'source': 'FIXTURE', 'mint': MINT, 'pair': POOL}


def raw_quote(inp, out, amount, output, floor, received_at, *, slot, impact):
    return {'inputMint': inp, 'outputMint': out, 'inAmount': str(amount), 'outAmount': str(output),
            'otherAmountThreshold': str(floor), 'swapMode': 'ExactIn', 'slippageBps': execution.SLIPPAGE_BPS,
            'priceImpactPct': impact, 'contextSlot': slot,
            'routePlan': [{'swapInfo': {'inputMint': inp, 'outputMint': out, 'ammKey': POOL}}],
            '_received_at': received_at, '_observed_at': received_at, '_simulated_fill_at': received_at+255}


def recorded_entry_evidence():
    """Run the actual production preparation on recorded-timing raw quotes."""
    def buy(output, received_at, slot):
        raw = raw_quote(USDC, MINT, NOTIONAL_RAW, output, output*99//100, received_at, slot=slot, impact='0.004')
        return {'input_usdc_raw': NOTIONAL_RAW, 'token_raw_expected': output,
                'token_raw_amount': output*(10000-execution.BUFFER_BPS)//10000,
                'token_raw_floor': int(raw['otherAmountThreshold']), 'price_impact_pct': .4,
                'slippage_bps': execution.SLIPPAGE_BPS, 'route': [], 'quoted_at': received_at,
                'context_slot': slot, 'assumed_buffer_bps': execution.BUFFER_BPS, 'raw_quote': raw,
                'simulated_fill_at': raw['_simulated_fill_at'], 'simulated_delay_ms': 255,
                'execution_model_version': 'QUOTE_LATENCY_BUFFER_V6', 'is_simulated_fill': True}
    first = buy(9_400_000_000, FIRST_BUY_AT, 454_349_373)
    final = buy(9_378_000_000, FINAL_AT, 454_349_389)
    sale_raw = raw_quote(MINT, USDC, first['token_raw_amount'], 198_900_000, 196_911_000, SALE_AT,
                         slot=454_349_381, impact='0.0035')
    sale = {'expected_usdc': 198.9*(1-execution.BUFFER_BPS/10000), 'provider_expected_usdc': 198.9,
            'floor_usdc': 196.911, 'price_impact_pct': .35, 'slippage_bps': execution.SLIPPAGE_BPS,
            'route': [], 'quoted_at': SALE_AT, 'context_slot': 454_349_381,
            'token_input_raw': first['token_raw_amount'], 'route_matches_entry_pool': True,
            'assumed_buffer_bps': execution.BUFFER_BPS, 'raw_quote': sale_raw,
            'simulated_fill_at': sale_raw['_simulated_fill_at'], 'simulated_delay_ms': 255,
            'execution_model_version': 'QUOTE_LATENCY_BUFFER_V6', 'is_simulated_fill': True}
    with patch.object(execution, 'entry_quote', side_effect=[copy.deepcopy(first), copy.deepcopy(final)]), \
            patch.object(execution, 'exit_quote', return_value=copy.deepcopy(sale)), \
            patch.object(execution, 'stamp', return_value=T0-1):
        prepared = execution.prepare_entry(MINT, POOL, NOTIONAL_RAW/1e6)
    assert prepared is not None, 'production preflight must accept the recorded timing'
    entry, preview = prepared
    return ({**entry, 'entry_network_fee_usd': NETWORK_FEE_USD, 'entry_account_reserve_usd': RENT_USD},
            {**preview, 'exit_network_fee_usd': NETWORK_FEE_USD})


def mark_evidence(at, token_raw, net_usd, impact_pct):
    gross = round(net_usd+NETWORK_FEE_USD, 8)
    quoted_at = at-260
    raw = raw_quote(MINT, USDC, token_raw, int(round(gross*1e6)), int(round(gross*1e6*.99)), quoted_at,
                    slot=454_349_400+(at-T0)//400, impact=str(impact_pct/100))
    raw['_simulated_fill_at'] = at-5
    return {'execution_source': 'JUPITER_QUOTE_EXPECTED_WITH_BUFFER_V5', 'market_price': .0213,
            'fill_price': gross/(token_raw/1e6), 'market_value_usd': gross, 'gross_proceeds_usd': gross,
            'dex_fee_usd': 0., 'network_fee_usd': NETWORK_FEE_USD, 'net_proceeds_usd': net_usd,
            'impact_pct': impact_pct, 'slippage_pct': execution.BUFFER_BPS/100, 'latency_pct': 0.,
            'quoted_at': quoted_at, 'token_input_raw': token_raw, 'from_cache': False,
            'route_matches_entry_pool': True, 'raw_quote': raw, 'simulated_fill_at': at-5,
            'execution_model_version': 'QUOTE_LATENCY_BUFFER_V6', 'is_simulated_fill': True}


def row(at, coin_row, flow_row, safety_row, price_row, *, reasons=(), evidence=None, label='', verified=(0, 0)):
    record = {'available_at': at, 'observed_at': at, 'coin': coin_row, 'flow': flow_row,
              'context': {'conviction': 80., 'mode': 'STRONG', 'max_hold_minutes': 60, 'target_pct': 10.,
                          'trail_arm_pct': 8., 'trail_pct': 4.},
              'safety': safety_row,
              'execution': {'mode': 'RECORDED_LIQUIDITY_MODEL', 'mint': MINT, 'pair': POOL, 'decimals': 6,
                            'entry_network_fee_usd': NETWORK_FEE_USD, 'entry_account_reserve_usd': RENT_USD,
                            'costs_known': True, 'sol_usd_reference': SOL_USD,
                            'buy_verified_at': verified[0], 'sell_verified_at': verified[1]},
              'quotes': [], 'rejection_reasons': list(reasons),
              'source': {'kind': 'MAIN_SHARED_READ_ONLY_OBSERVATION',
                         'execution_evidence': copy.deepcopy(evidence or {}),
                         'cached_route_evidence': {'buy': {}, 'sell': {}}, 'price_evidence': price_row},
              'recording_drops_total': 0, 'fixture_label': label}
    record['id'] = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return record


def build_fixture():
    entry, preview = recorded_entry_evidence()
    token_raw = int(entry['token_raw_amount'])
    rows = [
        # A cached risk pass that expired (61 s) is unknown risk: WAIT.
        row(T0-30_000, coin(T0-30_500), flow(T0-30_100), safety(T0-91_000), price(T0-31_000),
            reasons=['risk_check_pending'], label='stale_safety_wait'),
        # Confirmed flow whose latest event is older than the 12 s guard window: WAIT.
        row(T0-20_000, coin(T0-20_500), flow(T0-20_100, latest_event_at=T0-33_000), safety(SAFETY_CHECKED_AT),
            price(T0-21_000), reasons=['promoted_verified_flow_stale'], label='stale_flow_wait'),
        # Too few independent trades in the exact-pool window: WAIT.
        row(T0-15_000, coin(T0-15_500), flow(T0-15_100, trades=2, wallets=2), safety(SAFETY_CHECKED_AT),
            price(T0-16_000), reasons=['promoted_buy_pressure_unconfirmed'], label='weak_pressure_wait'),
        # Signal-stage observation recorded before the live quote sequence began:
        # no quote evidence, so the replay must WAIT without arming a cooldown.
        row(T0-4_700, coin(CANDIDATE_UPDATED_AT), flow(T0-5_800, latest_event_at=T0-7_300),
            safety(SAFETY_CHECKED_AT), price(T0-14_000), label='signal_stage_no_evidence'),
        # Scanner refresh of the exact pool during the quote sequence; the live
        # commit read this newer snapshot from STATE.feed.
        row(T0-3_000, coin(T0-3_000), flow(T0-3_100), safety(SAFETY_CHECKED_AT),
            price(T0-14_000), label='scanner_refresh'),
        # The recorded entry: candidate snapshot 8.3 s old, first preflight buy 4.65 s
        # old, preview sale 2.45 s old, final buy 255 ms old, risk pass 47 s old.
        row(T0, coin(CANDIDATE_UPDATED_AT), flow(T0-5_800, latest_event_at=T0-7_300), safety(SAFETY_CHECKED_AT),
            price(T0-14_000), evidence={'entry': entry, 'exit': preview}, label='entry'),
        row(T0+2_200, coin(T0+1_800), flow(T0+1_900), safety(SAFETY_CHECKED_AT), price(T0+1_000),
            evidence={'mark': mark_evidence(T0+2_200, token_raw, 203.0, .30)}, label='mark_1'),
        row(T0+15_000, coin(T0+14_600), flow(T0+14_700), safety(SAFETY_CHECKED_AT), price(T0+14_000),
            evidence={'mark': mark_evidence(T0+15_000, token_raw, 194.0, .55)}, label='mark_2'),
        # Exit impact at max(0.75%, entry impact 0.4% + 0.5%) = 0.9%: EXIT_IMPACT_EMERGENCY.
        row(T0+30_000, coin(T0+29_600), flow(T0+29_700), safety(SAFETY_CHECKED_AT), price(T0+29_000),
            evidence={'mark': mark_evidence(T0+30_000, token_raw, 197.5, .90)}, label='mark_3_live_exit'),
        # Same token inside the re-entry cooldown after the close: WAIT.
        row(T0+90_000, coin(T0+89_500), flow(T0+89_600), safety(T0+60_000), price(T0+89_000),
            reasons=['cooldown'], label='post_exit_cooldown_wait'),
    ]
    entry_cost = NETWORK_FEE_USD+RENT_USD
    expected = {
        'version': FIXTURE_VERSION,
        'live_ledger': {
            'closed_trades': [{'label': 'mark_3_live_exit', 'exit_reason': 'EXIT_IMPACT_EMERGENCY',
                               'opened_at': T0, 'closed_at': T0+30_000, 'notional_usd': 200.0,
                               'jupiter_token_raw_amount': token_raw, 'entry_cost_usd': round(entry_cost, 8),
                               'exit_net_proceeds_usd': 197.5, 'pnl_usd': round(197.5-200.0-entry_cost, 8)}],
            'open_positions': 0,
            'wait_rows': {r['id']: r['rejection_reasons'] for r in rows if r['rejection_reasons']},
        },
        'recorded_timing': {
            'preflight_buy_age_ms_at_row': T0-FIRST_BUY_AT, 'preview_sale_age_ms_at_row': T0-SALE_AT,
            'final_buy_age_ms_at_row': T0-FINAL_AT, 'safety_age_ms_at_row': T0-SAFETY_CHECKED_AT,
            'candidate_signal_age_ms_at_row': T0-CANDIDATE_UPDATED_AT, 'commit_feed_signal_age_ms': 3_000,
        },
        'exit_variants': {
            'stop_pct=3': {'closed_trades': 1, 'exit_reason': 'STOP_LOSS_NET_TARGET', 'closed_at': T0+15_000,
                           'pnl_usd': round(194.0-200.0-entry_cost, 8)},
            'take_profit_pct=1': {'closed_trades': 1, 'exit_reason': 'TAKE_PROFIT_10_NET', 'closed_at': T0+2_200,
                                  'pnl_usd': round(203.0-200.0-entry_cost, 8)},
            'max_hold_minutes=0.2': {'closed_trades': 1, 'exit_reason': 'MAX_HOLD_60', 'closed_at': T0+15_000,
                                     'pnl_usd': round(194.0-200.0-entry_cost, 8)},
            'disable_exit_impact_emergency=1': {'closed_trades': 0, 'open_positions': 1,
                                                'final_valuation_status': 'unavailable',
                                                'note': 'no recorded marks exist after the live exit; the held '
                                                        'position cannot be valued past it and stays unknown risk'},
        },
    }
    return rows, expected


def write_fixture():
    rows, expected = build_fixture()
    FIXTURES.mkdir(exist_ok=True)
    ROWS_PATH.write_text(''.join(json.dumps(r, sort_keys=True)+'\n' for r in rows), encoding='utf-8')
    EXPECTED_PATH.write_text(json.dumps(expected, indent=1, sort_keys=True)+'\n', encoding='utf-8')


def load_fixture():
    rows = [json.loads(line) for line in ROWS_PATH.read_text(encoding='utf-8').splitlines() if line.strip()]
    return rows, json.loads(EXPECTED_PATH.read_text(encoding='utf-8'))


class ReplayIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.expected = load_fixture()
        cls.by_label = {r['fixture_label']: r for r in cls.rows}

    def replay(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            with MainReplay(Path(tmp), **kwargs) as replay:
                result = replay.replay(copy.deepcopy(self.rows))
                decisions = list(replay.decisions)
        return result, decisions

    def test_committed_fixture_matches_builder(self):
        rows, expected = build_fixture()
        self.assertEqual(rows, self.rows)
        self.assertEqual(expected, self.expected)

    def test_fixture_reproduces_the_live_timing_the_old_adapter_rejected(self):
        timing = self.expected['recorded_timing']
        # The old adapter applied a 4 s literal to the first preflight buy quote
        # (live preflight takes ~4.6-4.7 s), a 30 s literal to the cached risk
        # pass (live accepts promoted_guard.SAFETY_MAX_AGE_MS), and read the stale
        # candidate snapshot at commit instead of the refreshed feed.
        self.assertGreater(timing['preflight_buy_age_ms_at_row'], execution.PREFLIGHT_PREVIEW_MAX_AGE_MS)
        self.assertGreater(timing['safety_age_ms_at_row'], 30_000)
        self.assertLessEqual(timing['safety_age_ms_at_row'], promoted_guard.SAFETY_MAX_AGE_MS)
        self.assertGreater(timing['candidate_signal_age_ms_at_row'], execution.MAX_SIGNAL_AGE_MS)
        self.assertLessEqual(timing['commit_feed_signal_age_ms'], execution.MAX_SIGNAL_AGE_MS)
        self.assertLessEqual(timing['final_buy_age_ms_at_row'], execution.FINAL_QUOTE_MAX_AGE_MS)

    def test_replay_books_the_live_trade_and_waits_where_the_engine_waited(self):
        result, decisions = self.replay()
        ledger = self.expected['live_ledger']
        self.assertEqual(result['report_kind'], 'BASELINE')
        self.assertTrue(result['exit_variant']['is_baseline'])
        self.assertEqual(result['invalid_records'], 0)
        self.assertEqual(result['stats']['closed_trades'], 1)
        self.assertEqual(len(result['positions']), ledger['open_positions'])
        trade, booked = result['history'][0], ledger['closed_trades'][0]
        self.assertEqual(trade['exit_reason'], booked['exit_reason'])
        self.assertEqual(trade['opened_at'], booked['opened_at'])
        self.assertEqual(trade['closed_at'], booked['closed_at'])
        self.assertEqual(trade['notional_usd'], booked['notional_usd'])
        self.assertEqual(trade['jupiter_token_raw_amount'], booked['jupiter_token_raw_amount'])
        self.assertAlmostEqual(trade['exit_net_proceeds_usd'], booked['exit_net_proceeds_usd'], places=6)
        self.assertAlmostEqual(trade['entry_network_fee_usd']+trade['entry_account_reserve_usd'],
                               booked['entry_cost_usd'], places=6)
        self.assertLess(abs(trade['pnl_usd']-booked['pnl_usd']), .01)
        self.assertEqual(trade['exit_policy_version'], exit_policy.VERSION)
        by_id = {d['id']: d for d in decisions}
        for row_id, reasons in ledger['wait_rows'].items():
            self.assertFalse(by_id[row_id]['opened'])
            self.assertEqual(set(by_id[row_id]['rejections']), set(reasons), by_id[row_id])
        summary = result['decision_summary']
        self.assertEqual(summary['recorded_rejection_rows'], len(ledger['wait_rows']))
        self.assertEqual(summary['recorded_rejection_rows_reproduced'], len(ledger['wait_rows']))
        self.assertEqual(summary['rows_opened'], 1)
        self.assertEqual(summary['rows_closed'], 1)
        self.assertTrue(by_id[self.by_label['entry']['id']]['opened'])
        self.assertTrue(by_id[self.by_label['mark_3_live_exit']['id']]['closed'])
        windows = result['freshness_windows']
        self.assertEqual(windows['windows']['safety_ms'], min(promoted_guard.SAFETY_MAX_AGE_MS, 60_000))
        self.assertEqual(windows['windows']['preview_sell_ms'],
                         execution.PREFLIGHT_PREVIEW_MAX_AGE_MS+execution.FINAL_QUOTE_MAX_AGE_MS)
        self.assertIsNone(windows['windows']['preflight_buy_ms'])
        self.assertNotIn('frozen', windows['basis']['preview_sell_ms'])

    def test_signal_stage_row_without_evidence_never_arms_quote_cooldown(self):
        result, decisions = self.replay()
        stage = next(d for d in decisions if d['id'] == self.by_label['signal_stage_no_evidence']['id'])
        self.assertFalse(stage['opened'])
        self.assertFalse(stage['entry_evidence'])
        self.assertGreaterEqual(result['decision_summary']['cooldown_arms_suppressed'], 1)
        self.assertEqual(result['stats']['closed_trades'], 1)
        # Without the suppression the engine's 15 s cooldown from the evidence-less
        # row would have covered the recorded entry 4.7 s later.
        with tempfile.TemporaryDirectory() as tmp:
            with MainReplay(Path(tmp)) as replay:
                with patch.object(replay, 'may_arm_quote_cooldown', return_value=True):
                    blocked = replay.replay(copy.deepcopy(self.rows))
                    entry = next(d for d in replay.decisions if d['id'] == self.by_label['entry']['id'])
        self.assertEqual(blocked['stats']['closed_trades'], 0)
        self.assertIn('quote_retry_cooldown', entry['rejections'])

    def test_recorded_commit_rejection_is_the_same_live_decision(self):
        # The live engine recorded a quote bundle, then rejected in its commit
        # section 15 ms later because the re-read exact-pool flow had lost buy
        # pressure; the rejection row is linked to the bundle by the verified
        # quote times. The replay must feed that commit-time flow to the engine.
        entry_row = self.by_label['entry']
        evidence = entry_row['source']['execution_evidence']
        verified = (int(evidence['entry']['quoted_at']), int(evidence['exit']['quoted_at']))
        rejection = row(T0+15, coin(CANDIDATE_UPDATED_AT), flow(T0+10, buy_usd=600., sell_usd=640.),
                        safety(SAFETY_CHECKED_AT), price(T0-14_000), reasons=['promoted_buy_pressure_unconfirmed'],
                        label='commit_rejection', verified=verified)
        rows = [r for r in self.rows if not r['fixture_label'].startswith('mark_')]
        rows.append(rejection)
        linked = MainReplay.link_commit_outcomes(sorted(rows, key=lambda r: r['available_at']))
        self.assertEqual([r['fixture_label'] for r in linked.values()], ['commit_rejection'])
        with tempfile.TemporaryDirectory() as tmp:
            with MainReplay(Path(tmp)) as replay:
                result = replay.replay(copy.deepcopy(rows))
                entry = next(d for d in replay.decisions if d['id'] == entry_row['id'])
        self.assertEqual(result['stats']['closed_trades'], 0)
        self.assertEqual(result['positions'], [])
        self.assertTrue(entry['commit_outcome_linked'])
        self.assertEqual(set(entry['rejections']), {'promoted_buy_pressure_unconfirmed'})
        self.assertEqual(result['decision_summary']['commit_outcome_rows_linked'], 1)
        # A later 'cooldown' row (what the engine records after a successful
        # commit) or an unrelated bundle never links.
        unrelated = row(T0+15, coin(CANDIDATE_UPDATED_AT), flow(T0+10, buy_usd=600., sell_usd=640.),
                        safety(SAFETY_CHECKED_AT), price(T0-14_000), reasons=['cooldown'], verified=verified)
        self.assertEqual(MainReplay.link_commit_outcomes(sorted(rows[:-1]+[unrelated], key=lambda r: r['available_at'])), {})
        stale_bundle = dict(rejection, execution=dict(rejection['execution'], buy_verified_at=verified[0]-1))
        self.assertEqual(MainReplay.link_commit_outcomes(sorted(rows[:-1]+[stale_bundle], key=lambda r: r['available_at'])), {})

    def test_candidate_snapshot_alone_is_stale_at_commit(self):
        # Dropping the scanner refresh row leaves only the 8.3 s old candidate
        # snapshot in the feed; the engine's own commit check must then WAIT.
        rows = [r for r in self.rows if r['fixture_label'] != 'scanner_refresh']
        with tempfile.TemporaryDirectory() as tmp:
            with MainReplay(Path(tmp)) as replay:
                result = replay.replay(copy.deepcopy(rows))
                entry = next(d for d in replay.decisions if d['id'] == self.by_label['entry']['id'])
        self.assertEqual(result['stats']['closed_trades'], 0)
        self.assertIn('stale_signal', entry['rejections'])

    def test_exit_variants_change_exits_only_and_are_labelled(self):
        baseline, _ = self.replay()
        for text, expectation in self.expected['exit_variants'].items():
            with self.subTest(variant=text):
                result, _ = self.replay(exit_variant=parse_exit_variant(text))
                self.assertEqual(result['report_kind'], 'EXIT_VARIANT')
                self.assertFalse(result['exit_variant']['is_baseline'])
                self.assertEqual(result['exit_variant']['rules'], parse_exit_variant(text))
                self.assertEqual(result['exit_variant']['baseline_exit_rules'], baseline['exit_variant']['effective_exit_rules'])
                self.assertEqual(result['stats']['closed_trades'], expectation['closed_trades'])
                if expectation['closed_trades']:
                    trade = result['history'][0]
                    self.assertEqual(trade['exit_reason'], expectation['exit_reason'])
                    self.assertEqual(trade['closed_at'], expectation['closed_at'])
                    self.assertLess(abs(trade['pnl_usd']-expectation['pnl_usd']), .01)
                    # Entry identity and planned risk keep the baseline rules.
                    self.assertEqual(trade['jupiter_token_raw_amount'], baseline['history'][0]['jupiter_token_raw_amount'])
                    self.assertEqual(trade['planned_risk_usd'], baseline['history'][0]['planned_risk_usd'])
                    self.assertEqual(trade['planned_stop_net_pct'], baseline['history'][0]['planned_stop_net_pct'])
                else:
                    self.assertEqual(len(result['positions']), expectation['open_positions'])
                    position = result['positions'][0]
                    self.assertEqual(position['jupiter_token_raw_amount'], baseline['history'][0]['jupiter_token_raw_amount'])
                    # Held past the live exit: no later recorded mark exists, so the
                    # engine reports unknown valuation instead of a modeled value.
                    self.assertEqual(position['valuation_status'], expectation['final_valuation_status'])
                    self.assertEqual(position['exit_state'], 'VALUATION_UNAVAILABLE')
                    self.assertEqual(result['stats']['unavailable_liquidation_positions'], 1)
        # The baseline run is unaffected by any variant run.
        again, _ = self.replay()
        self.assertEqual(again['history'][0]['pnl_usd'], baseline['history'][0]['pnl_usd'])
        self.assertEqual(again['history'][0]['exit_reason'], 'EXIT_IMPACT_EMERGENCY')

    def test_exit_variant_rules_are_validated(self):
        for bad in ['stop_pct=0', 'stop_pct=-5', 'take_profit_pct=abc', 'max_hold_minutes=nan',
                    'unknown=1', 'stop_pct', 'disable_exit_impact_emergency=maybe', '']:
            with self.subTest(text=bad):
                with self.assertRaises(ValueError):
                    parse_exit_variant(bad)
        self.assertEqual(parse_exit_variant('stop_pct=8, disable_exit_impact_emergency=true'),
                         {'stop_pct': 8., 'disable_exit_impact_emergency': True})

    def test_exit_policy_default_hold_limits_unchanged(self):
        self.assertEqual(exit_policy.FIXED_MAX_HOLD_MINUTES, 60)
        self.assertEqual(exit_policy.ABSOLUTE_MAX_HOLD_MINUTES, 120)
        self.assertIsNone(exit_policy.exit_reason({}, {}, net_pct=0, peak_net_pct=0, hold_minutes=59.9))
        self.assertEqual(exit_policy.exit_reason({}, {}, net_pct=0, peak_net_pct=0, hold_minutes=60), 'MAX_HOLD_60')
        self.assertEqual(exit_policy.exit_reason({}, {}, net_pct=0, peak_net_pct=0, hold_minutes=30, max_hold_minutes=30), 'MAX_HOLD_60')
        self.assertIsNone(exit_policy.exit_reason({}, {'conviction': 80}, net_pct=0, peak_net_pct=0, hold_minutes=119, policy='adaptive'))
        self.assertEqual(exit_policy.exit_reason({}, {'conviction': 80}, net_pct=0, peak_net_pct=0, hold_minutes=120, policy='adaptive'), 'ABSOLUTE_MAX_HOLD')
        self.assertEqual(execution.FINAL_QUOTE_MAX_AGE_MS, 750)
        self.assertEqual(execution.PREFLIGHT_PREVIEW_MAX_AGE_MS, 4_000)
        self.assertEqual(execution.PREFLIGHT_MAX_SLOT_GAP, 25)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='regenerate the committed fixture files')
    args, remaining = parser.parse_known_args()
    if args.write:
        write_fixture()
        print(f'wrote {ROWS_PATH} and {EXPECTED_PATH}')
    else:
        unittest.main(argv=['unittest']+remaining)
