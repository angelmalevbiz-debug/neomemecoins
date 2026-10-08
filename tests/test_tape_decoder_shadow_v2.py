"""Reversed Pump pools whose wrapped SOL cash account is closed in the swap.

Evidence: tests/fixtures/tape_real_bodies_reversed_wsol.json holds four public
getTransaction bodies fetched read-only on 2026-10-08 for signatures taken from
a scratch copy of the PAPER tape journal. Since PR #7 the reversed-orientation
branch required a pre/post token balance entry for the user's base (cash)
account; a wrapped SOL account created and closed inside the transaction has
none, so every such swap was journaled as BASE_DECIMALS_OR_MINT_MISSING and
pools such as SHITCOIN/TWEETCRAFT yielded zero decoded swaps.

The repaired path reads the exact base leg from the Pump event (equal to the
pool base vault delta) and the exact user quote leg (equal to the user's token
account delta). Its events carry a distinct decoder version and the
DECODER_PATH_UNVALIDATED quality flag, which the engine and the Lab already
treat as DEGRADED flow, until the owner lists the version as validated.
"""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import live_tape as tape
from test_tape_reversed_orientation import BASE_ACCOUNT, META, NOW, USER, classify, fixture, key
from test_tape_execution_repair import transaction

FIXTURE = Path(__file__).resolve().parent/'fixtures'/'tape_real_bodies_reversed_wsol.json'
BODIES = {row['name']: row for row in json.loads(FIXTURE.read_text(encoding='utf-8'))['bodies']}
V1, V2, FLAG = tape.DECODER_VERSION, tape.REVERSED_CASH_LEG_DECODER_VERSION, tape.UNVALIDATED_DECODER_FLAG


def real(name):
    row = BODIES[name]
    tx = copy.deepcopy(row['result'])
    block_ms = int(tx['blockTime'])*1000
    metadata = {'pair': row['pair'], 'address': row['address'], 'symbol': row['symbol'],
                'quote_usd_reference': 200.0, 'quote_reference_at': block_ms,
                'quote_reference_source': 'JUPITER_CONVERSION_QUOTE_REFERENCE'}
    return tx, metadata, block_ms


def classify_real(name, **changes):
    tx, metadata, block_ms = real(name)
    return tape.classify_transaction(tx, dict(metadata, **changes),
                                     observed_at=block_ms+500, ingested_at=block_ms+1000)


def balance_deltas(tx):
    keys, _ = tape._instruction_records(tx)
    pre, post = {}, {}
    for kind, target in (('preTokenBalances', pre), ('postTokenBalances', post)):
        for balance in tx['meta'].get(kind) or []:
            target[keys[balance['accountIndex']]] = int(balance['uiTokenAmount']['amount'])
    return {account: post.get(account, 0)-pre.get(account, 0)
            for account in set(pre) | set(post)}


def swap_accounts(tx):
    _, records = tape._instruction_records(tx)
    return [accounts for _, program, accounts, data in records
            if program == tape.PUMP_AMM and data[:8] in tape.SWAP_DISCRIMINATORS]


def close_account(tx, account, *, program='spl-token', kind='closeAccount'):
    tx['meta']['innerInstructions'][0]['instructions'].append(
        {'program': program, 'programId': 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
         'parsed': {'type': kind, 'info': {'account': account, 'destination': USER, 'owner': USER}}})


def without_cash_balance(**kwargs):
    tx = fixture(**kwargs)
    tx['meta']['preTokenBalances'].pop(0)
    return tx


class RealBodyDecoderTests(unittest.TestCase):
    def test_closed_wrapped_cash_account_decodes_exact_legs_on_the_shadow_path(self):
        tx, metadata, _ = real('tweetcraft_base_decimals')
        state, events, reason = classify_real('tweetcraft_base_decimals')
        self.assertEqual((state, reason), ('unclassified', FLAG))
        event, = events
        accounts, = swap_accounts(tx)
        deltas = balance_deltas(tx)
        self.assertNotIn(accounts[5], deltas)  # wrapped SOL account closed in-transaction
        self.assertEqual(event['decoder_version'], V2)
        self.assertFalse(event['decoder_validated'])
        self.assertEqual(event['quality_flags'], [FLAG])
        self.assertEqual(event['cash_leg_balance_metadata'], 'WRAPPED_ACCOUNT_CLOSED_IN_TRANSACTION')
        self.assertEqual(event['pool_orientation'], 'TRACKED_QUOTE')
        self.assertEqual((event['onchain_direction'], event['direction']), ('SELL', 'BUY'))
        self.assertEqual(event['address'], metadata['address'])
        self.assertEqual(event['pairAddress'], metadata['pair'])
        # Token leg equals the user's token account delta; cash leg equals the
        # pool base vault delta. Nothing is taken from the scanner price.
        self.assertEqual(int(event['token_raw_amount']), deltas[accounts[6]])
        self.assertEqual(int(event['quote_raw_amount']), deltas[accounts[7]])
        self.assertEqual((event['quote_asset'], event['quote_decimals']), (tape.WSOL, 9))
        self.assertEqual(event['token_decimals'], 6)
        self.assertEqual(event['quote_amount'], int(event['quote_raw_amount'])/10**9)
        self.assertEqual(event['usd_amount'], round(event['quote_amount']*200.0, 8))
        self.assertEqual(event['usd_valuation_source'], 'JUPITER_CONVERSION_QUOTE_REFERENCE')
        self.assertTrue(event['confirmed_swap'])
        # Fee evidence stays the separate strict reconciler's business; an
        # unvalidated event never carries authenticated fee provenance.
        self.assertNotIn('provenance_authenticated', event.get('fee_evidence') or {})

    def test_two_wallet_reversed_body_reconciles_both_swaps_against_the_vault(self):
        tx, _, _ = real('shitcoin_base_decimals')
        state, events, reason = classify_real('shitcoin_base_decimals')
        self.assertEqual((state, reason), ('unclassified', FLAG))
        self.assertEqual(len(events), 2)
        deltas = balance_deltas(tx)
        accounts = swap_accounts(tx)
        self.assertEqual(len({e['wallet'] for e in events}), 2)
        self.assertEqual([(e['onchain_direction'], e['direction']) for e in events],
                         [('SELL', 'BUY'), ('BUY', 'SELL')])
        for event, instruction in zip(events, accounts):
            self.assertEqual(event['decoder_version'], V2)
            self.assertEqual(event['wallet'], instruction[1])
            self.assertEqual(int(event['token_raw_amount']), abs(deltas[instruction[6]]))
        sold_in, bought_out = (int(e['quote_raw_amount']) for e in events)
        self.assertEqual(sold_in-bought_out, deltas[accounts[0][7]])
        self.assertEqual(len({e['event_index'] for e in events}), 2)

    def test_default_path_bodies_are_decoded_exactly_as_before_with_the_version_added(self):
        state, events, reason = classify_real('normal_processed')
        self.assertEqual((state, reason), ('processed', None))
        event, = events
        self.assertEqual((event['decoder_version'], event['decoder_validated']), (V1, True))
        self.assertEqual(event['quality_flags'], [])
        self.assertEqual(event['pool_orientation'], 'TRACKED_BASE')
        self.assertEqual(event['cash_leg_balance_metadata'], 'ABSENT')
        tx, _, _ = real('normal_processed')
        accounts, = swap_accounts(tx)
        self.assertEqual(int(event['token_raw_amount']), balance_deltas(tx)[accounts[7]])
        state, events, reason = classify_real('reversed_processed')
        self.assertEqual((state, reason), ('processed', None))
        event, = events
        self.assertEqual((event['decoder_version'], event['cash_leg_balance_metadata']), (V1, 'PRESENT'))
        journal = BODIES['reversed_processed']['journal_event']
        for field in ('direction', 'onchain_direction', 'pool_orientation', 'token_raw_amount',
                      'token_decimals', 'quote_asset', 'quote_raw_amount', 'quote_decimals',
                      'wallet', 'quality_flags'):
            self.assertEqual(event[field], journal[field], field)

    def test_listing_the_shadow_version_as_validated_makes_the_same_events_processed(self):
        with patch.object(tape, 'VALIDATED_DECODER_VERSIONS', frozenset({V1, V2})):
            state, events, reason = classify_real('tweetcraft_base_decimals')
        self.assertEqual((state, reason), ('processed', None))
        self.assertEqual(events[0]['quality_flags'], [])
        self.assertEqual((events[0]['decoder_version'], events[0]['decoder_validated']), (V2, True))

    def test_validated_versions_default_to_the_default_path_only(self):
        with patch.dict(os.environ, {'NEO_TAPE_VALIDATED_DECODER_VERSIONS': ''}):
            self.assertEqual(tape.validated_decoder_versions(), frozenset({V1}))
        with patch.dict(os.environ, {'NEO_TAPE_VALIDATED_DECODER_VERSIONS': f' {V2} ,'}):
            self.assertEqual(tape.validated_decoder_versions(), frozenset({V1, V2}))
        self.assertEqual(tape.STATUS['decoder']['default_version'], V1)
        self.assertEqual(tape.STATUS['decoder']['shadow_versions'], [V2])
        self.assertEqual(tape.STATUS['decoder']['unvalidated_flow_quality'], 'DEGRADED')


class SyntheticOrientationTests(unittest.TestCase):
    def test_missing_cash_balance_without_in_transaction_close_still_fails_closed(self):
        self.assertEqual(classify(without_cash_balance()), ('unclassified', [], 'BASE_DECIMALS_OR_MINT_MISSING'))
        for change in ({'account': key(30)}, {'program': 'spl-token-2022'}, {'kind': 'initializeAccount3'}):
            with self.subTest(change=change):
                tx = without_cash_balance()
                close_account(tx, change.get('account', BASE_ACCOUNT),
                              program=change.get('program', 'spl-token'),
                              kind=change.get('kind', 'closeAccount'))
                self.assertEqual(classify(tx), ('unclassified', [], 'BASE_DECIMALS_OR_MINT_MISSING'))

    def test_closed_wrapped_cash_account_takes_the_versioned_shadow_path(self):
        for onchain, token_side, cash in (('BUY', 'SELL', tape.WSOL), ('SELL', 'BUY', tape.WSOL),
                                          ('BUY', 'SELL', tape.USDC)):
            with self.subTest(onchain=onchain, cash=cash):
                tx = without_cash_balance(directions=(onchain,), cash=cash)
                close_account(tx, BASE_ACCOUNT)
                state, events, reason = classify(tx)
                self.assertEqual((state, reason), ('unclassified', FLAG))
                event, = events
                self.assertEqual(event['decoder_version'], V2)
                self.assertEqual(event['quality_flags'], [FLAG])
                self.assertEqual((event['direction'], event['onchain_direction']), (token_side, onchain))
                self.assertEqual((event['token_raw_amount'], event['token_decimals']), ('151000000', 6))
                self.assertEqual(event['quote_asset'], cash)
                self.assertEqual(event['quote_raw_amount'], '2000000' if cash == tape.USDC else '2000000000')
                self.assertEqual(event['quote_amount'], 2)
                self.assertEqual(event['usd_amount'], 2 if cash == tape.USDC else 200)

    def test_present_cash_balance_keeps_the_default_path_in_both_orientations(self):
        state, events, reason = classify(fixture())
        self.assertEqual((state, reason), ('processed', None))
        self.assertEqual((events[0]['decoder_version'], events[0]['cash_leg_balance_metadata']), (V1, 'PRESENT'))
        tx = fixture()
        close_account(tx, BASE_ACCOUNT)  # a close with balances present changes nothing
        self.assertEqual(classify(tx)[1][0]['decoder_version'], V1)
        tx = fixture()
        tx['meta']['preTokenBalances'][0]['uiTokenAmount']['decimals'] = 6
        self.assertEqual(classify(tx)[2], 'BASE_DECIMALS_OR_MINT_MISSING')
        normal = transaction(quote_mint=tape.WSOL)
        normal['meta']['preTokenBalances'].pop(1)
        state, events, _ = tape.classify_transaction(
            normal, dict(META, pair=normal['transaction']['message']['accountKeys'][0]['pubkey'],
                         address=normal['transaction']['message']['accountKeys'][3]['pubkey']),
            observed_at=NOW, ingested_at=NOW)
        self.assertEqual(state, 'processed')
        self.assertEqual((events[0]['decoder_version'], events[0]['cash_leg_balance_metadata']), (V1, 'ABSENT'))

    def test_other_defects_keep_their_existing_names_over_the_shadow_flag(self):
        tx = without_cash_balance()
        close_account(tx, BASE_ACCOUNT)
        for entry in tx['transaction']['message']['accountKeys']:
            entry['signer'] = False
        state, events, reason = classify(tx)
        self.assertEqual((state, reason), ('unclassified', 'SWAP_ACTOR_NOT_TRANSACTION_SIGNER'))
        self.assertEqual(sorted(events[0]['quality_flags']), sorted(['SWAP_ACTOR_NOT_TRANSACTION_SIGNER', FLAG]))
        tx = without_cash_balance()
        close_account(tx, BASE_ACCOUNT)
        state, events, reason = classify(tx, dict(META, quote_usd_reference=None))
        self.assertEqual((state, reason), ('unclassified', 'QUOTE_USD_UNKNOWN_OR_ESTIMATED_WITHOUT_ROUTE'))


class RecorderAndEngineConsumerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.tx, self.metadata, self.block_ms = real('tweetcraft_base_decimals')
        self.clock = [self.block_ms+1000]
        self.rec = tape.TapeRecorder(Path(self.directory.name)/'tape.sqlite',
                                     clock=lambda: self.clock[0], tx_budget=4)
        row = BODIES['tweetcraft_base_decimals']
        with self.rec.db:
            self.rec.db.execute('INSERT INTO pairs(pair,mint,complete_since,last_poll,metadata) VALUES(?,?,?,?,?)',
                                (row['pair'], row['address'], self.block_ms-60_000, self.clock[0], json.dumps(self.metadata)))
            self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,observed,metadata,state)
                VALUES(?,?,?,?,?,?,'pending')''', (row['signature'], row['pair'], row['slot'], self.block_ms,
                                                   self.block_ms+500, json.dumps(self.metadata)))
        self.row = row

    def tearDown(self):
        self.rec.close()
        self.directory.cleanup()

    def test_shadow_events_are_journaled_as_unclassified_and_the_pool_stays_degraded(self):
        self.rec.process(lambda calls: [{'result': copy.deepcopy(self.tx)} for _ in calls])
        state = self.rec.db.execute('SELECT state,reason FROM signatures').fetchone()
        self.assertEqual((state['state'], state['reason']), ('unclassified', FLAG))
        # Shadow-path events live in their own journal table only.
        self.assertEqual(self.rec.db.execute('SELECT count(*) FROM events').fetchone()[0], 0)
        stored = [json.loads(r['payload']) for r in self.rec.db.execute('SELECT payload FROM shadow_events')]
        self.assertEqual([(e['decoder_version'], e['quality_flags']) for e in stored], [(V2, [FLAG])])
        row = self.rec.db.execute('SELECT signature,pair,event_time,available,event_id FROM shadow_events').fetchone()
        self.assertEqual((row['signature'], row['pair'], row['event_id']),
                         (self.row['signature'], self.row['pair'], stored[0]['event_id']))
        self.assertEqual((row['event_time'], row['available']), (stored[0]['event_time'], stored[0]['available_at']))
        snapshot = self.rec.snapshot([self.metadata])
        coverage = snapshot['pair_coverage'][self.row['pair']]
        self.assertEqual((coverage['status'], coverage['reason']), ('DEGRADED', 'UNCLASSIFIED_TRANSACTIONS'))
        self.assertEqual(snapshot['events'], [])
        self.assertEqual((snapshot['events_total'], snapshot['shadow_events_window']), (0, 1))
        self.assertEqual(snapshot['decoder']['validated_versions'], [V1])
        self.assertEqual(self.rec.decode_yield(self.row['pair']),
                         {'bodies': 1, 'decoded_swaps': 0, 'usable_swaps': 0, 'shadow_swaps': 1,
                          'first_body_at': self.clock[0], 'last_body_at': self.clock[0],
                          'window_ms': self.rec.yield_window_ms, 'since': self.clock[0]-self.rec.yield_window_ms})

    def test_shadow_events_never_count_toward_the_projection_window_or_max_events(self):
        # A second, otherwise complete pool must not be marked
        # UI_WINDOW_TRUNCATED because of shadow-path volume elsewhere.
        clean = dict(self.metadata, pair='CLEAN-POOL', address='CLEAN-MINT')
        with self.rec.db:
            self.rec.db.execute('INSERT INTO pairs(pair,mint,complete_since,last_poll,metadata) VALUES(?,?,?,?,?)',
                                (clean['pair'], clean['address'], self.block_ms-60_000, self.clock[0], json.dumps(clean)))
            for index in range(2):
                self.rec.db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',
                    (f'valid-{index}', f'valid-{index}', clean['pair'], self.block_ms-index, self.block_ms,
                     json.dumps({'event_id': f'valid-{index}', 'pair': clean['pair']})))
        self.rec.process(lambda calls: [{'result': copy.deepcopy(self.tx)} for _ in calls])
        shadow = 50
        with self.rec.db:
            self.rec.db.executemany('INSERT INTO shadow_events VALUES(?,?,?,?,?,?)',
                ((f'shadow-{i}', f'shadow-{i}', self.row['pair'], self.block_ms, self.block_ms,
                  json.dumps({'event_id': f'shadow-{i}', 'quality_flags': [FLAG]})) for i in range(shadow)))
        with patch.object(tape, 'MAX_EVENTS', 2):
            snapshot = self.rec.snapshot([self.metadata, clean])
        self.assertFalse(snapshot['projection_truncated'])
        self.assertEqual([event['event_id'] for event in snapshot['events']], ['valid-0', 'valid-1'])
        self.assertEqual(snapshot['shadow_events_window'], shadow+1)
        self.assertEqual(snapshot['pair_coverage'][clean['pair']]['status'], 'COMPLETE')
        self.assertNotIn('UI_WINDOW_TRUNCATED',
                         [record['reason'] for record in snapshot['pair_coverage'].values()])
        # The truncation guard itself still works for validated events.
        with self.rec.db:
            self.rec.db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',
                ('valid-2', 'valid-2', clean['pair'], self.block_ms-2, self.block_ms, json.dumps({'event_id': 'valid-2'})))
        with patch.object(tape, 'MAX_EVENTS', 2):
            snapshot = self.rec.snapshot([self.metadata, clean])
        self.assertTrue(snapshot['projection_truncated'])
        self.assertEqual(snapshot['pair_coverage'][clean['pair']]['reason'], 'UI_WINDOW_TRUNCATED')

    def test_shadow_table_is_added_to_an_existing_journal_without_touching_its_rows(self):
        path = self.rec.path
        with self.rec.db:
            self.rec.db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',
                                ('kept', 'kept', self.row['pair'], self.block_ms, self.block_ms, '{"kept":true}'))
            self.rec.db.execute('DROP TABLE shadow_events')
        self.rec.close()
        self.rec = tape.TapeRecorder(path, clock=lambda: self.clock[0], tx_budget=4)
        tables = {row['name'] for row in self.rec.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('shadow_events', tables)
        indexes = {row['name'] for row in self.rec.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertTrue({'shadow_event_time_idx', 'shadow_event_pair_time_idx'} <= indexes)
        self.assertEqual([tuple(row) for row in self.rec.db.execute('SELECT event_id,payload FROM events')],
                         [('kept', '{"kept":true}')])
        self.assertEqual(self.rec.db.execute('SELECT count(*) FROM shadow_events').fetchone()[0], 0)

    def test_engine_live_flow_treats_shadow_events_as_degraded_until_validated(self):
        for name, filename in (('NEO_MARKET_STATE_PATH', 'state.json'), ('NEO_MARKET_AUDIT_PATH', 'audit.jsonl'),
                               ('NEO_LIVE_TAPE_PATH', 'tape.json')):
            os.environ.setdefault(name, str(Path(self.directory.name)/filename))
        import market_monitor as m
        _, events, _ = tape.classify_transaction(self.tx, self.metadata, observed_at=self.block_ms+500,
                                                 ingested_at=self.block_ms+1000)
        event = dict(events[0], event_id='sig:pool:0')
        coverage = {self.row['pair']: {'address': self.row['address'], 'pairAddress': self.row['pair'],
                                       'status': 'COMPLETE', 'complete_since_ms': self.block_ms-300_000,
                                       'last_poll_at': self.block_ms+1500}}
        projection = {'updated_at': self.block_ms+1500, 'events': [event], 'pair_coverage': coverage}
        with patch.object(m, 'read_live_tape', return_value=projection), \
             patch.object(m, 'now_ms', return_value=self.block_ms+2000):
            result = m.STATE.live_flow(self.row['address'], 30, self.row['pair'])
        self.assertEqual((result['quality'], result['trades'], result['buy_usd']), ('DEGRADED', 0, 0))
        self.assertFalse(result['fresh'])
        validated = dict(event, quality_flags=[], decoder_validated=True)
        projection['events'] = [validated]
        with patch.object(m, 'read_live_tape', return_value=projection), \
             patch.object(m, 'now_ms', return_value=self.block_ms+2000):
            result = m.STATE.live_flow(self.row['address'], 30, self.row['pair'])
        self.assertEqual((result['quality'], result['trades']), ('COMPLETE', 1))
        self.assertEqual(result['buy_usd'], round(validated['usd_amount'], 2))


if __name__ == '__main__':
    unittest.main()
