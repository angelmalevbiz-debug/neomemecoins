"""Offline regressions for confirmed Pump pools whose base is the cash leg.

The instruction prefix, account counts and CPI event types reproduce confirmed
TWEETCRAFT transaction 3QLEEsByAbwpMvmF6DkY6w3FHC5NGEbvUMpbFNhjAQpR6Hr6mdxYZtZVQFpsVBBr1CT76kPe5pMmkrQvvZawZcMQ
(slot 454312666). Wallets/accounts are synthetic and amounts are deterministic;
the fixture retains only the fields the strict decoder uses, with no RPC calls.
"""
import copy
import unittest

import live_tape as tape


NOW = 1_800_000_000_000
POOL = '54TyDv21vFxf4vhYaUb2M82T3tVrsQVQ4615GfXaKEui'
MINT = 'BZe6RWbM8AU86Luky7d8sAEQcz4ge1wADoYYgryPYua'


def key(n):
    return tape._b58encode(bytes([n])*32)


USER, BASE_ACCOUNT, QUOTE_ACCOUNT = key(1), key(2), key(3)
META = {'pair': POOL, 'address': MINT, 'symbol': 'TWEETCRAFT',
        'quote_usd_reference': 100, 'quote_reference_at': NOW,
        'quote_reference_source': 'JUPITER_CONVERSION_QUOTE_REFERENCE'}


def fixture(*, directions=('BUY',), cash=tape.WSOL, exact_quote=False):
    cash_decimals = 6 if cash == tape.USDC else 9
    accounts = [POOL, USER, key(4), cash, MINT, BASE_ACCOUNT, QUOTE_ACCOUNT,
                key(5), key(6)]
    extras = [key(n) for n in range(7, 23)]
    keys = accounts + extras
    instructions, events = [], []
    for direction in directions:
        discriminator = next(k for k, v in tape.SWAP_DISCRIMINATORS.items() if v == direction)
        if exact_quote and direction == 'BUY':
            discriminator = bytes([198, 46, 21, 82, 180, 217, 232, 112])
        event_disc = next(k for k, v in tape.EVENT_DISCRIMINATORS.items() if v == direction)
        # Event offset 16 is base WSOL/USDC; offset 112 is the actual user quote
        # token leg. Deliberately different quantities expose a swapped-leg bug.
        values = [NOW//1000, 2*10**cash_decimals] + [0]*11 + [151_000_000]
        payload = event_disc + b''.join(v.to_bytes(8, 'little') for v in values)
        payload += b''.join(tape._b58decode(x) for x in
                            (POOL, USER, BASE_ACCOUNT, QUOTE_ACCOUNT))
        count = 25 if direction == 'BUY' else 23
        instructions.append({'programId': tape.PUMP_AMM, 'accounts': keys[:count],
                             'data': tape._b58encode(discriminator + bytes(16))})
        events.append({'programId': tape.PUMP_AMM, 'accounts': [],
                       'data': tape._b58encode(tape.ANCHOR_EVENT_CPI + payload)})
    return {'slot': 454_312_666, 'blockTime': NOW//1000,
            'transaction': {'message': {
                'accountKeys': [{'pubkey': x, 'signer': x == USER} for x in keys],
                'instructions': instructions}},
            'meta': {'err': None, 'logMessages': [],
                     'innerInstructions': [{'index': 0, 'instructions': events}],
                     'preTokenBalances': [
                         {'accountIndex': 5, 'mint': cash,
                          'uiTokenAmount': {'amount': '0', 'decimals': cash_decimals}},
                         {'accountIndex': 6, 'mint': MINT,
                          'uiTokenAmount': {'amount': '0', 'decimals': 6}}],
                     'postTokenBalances': []}}


def classify(tx, metadata=None, *, available=NOW):
    return tape.classify_transaction(tx, META if metadata is None else metadata,
                                     observed_at=NOW, ingested_at=available)


def change_event(tx, offset, replacement, index=0):
    event = tx['meta']['innerInstructions'][0]['instructions'][index]
    raw = bytearray(tape._b58decode(event['data']))
    # The self-CPI discriminator precedes the event discriminator and fields.
    raw[8+offset:8+offset+len(replacement)] = replacement
    event['data'] = tape._b58encode(bytes(raw))


class ReversedOrientationTests(unittest.TestCase):
    def test_known_instruction_sides_invert_and_use_exact_cash_and_token_legs(self):
        for onchain, token_side, exact in [('BUY', 'SELL', False),
                                          ('BUY', 'SELL', True),
                                          ('SELL', 'BUY', False)]:
            with self.subTest(onchain=onchain, exact_quote=exact):
                state, events, reason = classify(fixture(directions=(onchain,), exact_quote=exact))
                self.assertEqual((state, reason), ('processed', None))
                event, = events
                self.assertEqual(event['direction'], token_side)
                self.assertEqual(event['note'], token_side)
                self.assertEqual(event['onchain_direction'], onchain)
                self.assertEqual(event['address'], MINT)
                self.assertEqual(event['pairAddress'], POOL)
                self.assertEqual(event['token_raw_amount'], '151000000')
                self.assertEqual(event['token_decimals'], 6)
                self.assertEqual(event['token_amount'], 151)
                self.assertEqual(event['quote_asset'], tape.WSOL)
                self.assertEqual(event['quote_raw_amount'], '2000000000')
                self.assertEqual(event['quote_decimals'], 9)
                self.assertEqual(event['quote_amount'], 2)
                self.assertEqual(event['usd_amount'], 200)
                self.assertEqual(event['pool_orientation'], 'TRACKED_QUOTE')
                self.assertEqual(event['onchain_base_mint'], tape.WSOL)
                self.assertEqual(event['onchain_quote_mint'], MINT)
                self.assertEqual(event['quality_flags'], [])
                self.assertTrue(event['confirmed_swap'])

    def test_exact_usdc_base_is_valued_as_cash_without_scanner_price(self):
        state, events, reason = classify(fixture(cash=tape.USDC), dict(META, price=99999))
        self.assertEqual((state, reason), ('processed', None))
        self.assertEqual(events[0]['usd_amount'], 2)
        self.assertEqual(events[0]['token_amount'], 151)
        self.assertFalse(events[0]['usd_valuation_estimated'])
        self.assertEqual(events[0]['usd_valuation_source'], 'ACTUAL_USDC_QUOTE_LEG')

    def test_both_cpi_swap_events_are_kept_with_opposite_token_directions(self):
        state, events, _ = classify(fixture(directions=('SELL', 'BUY')))
        self.assertEqual(state, 'processed')
        self.assertEqual([e['direction'] for e in events], ['BUY', 'SELL'])
        self.assertEqual(len({e['event_index'] for e in events}), 2)

    def test_missing_wrong_or_inconsistent_token_and_cash_metadata_fail_closed(self):
        changes = [lambda b: b.pop(1), lambda b: b.pop(0),
                   lambda b: b[1].update(mint=key(30)),
                   lambda b: b[0].update(mint=key(30)),
                   lambda b: b[0]['uiTokenAmount'].update(decimals=6),
                   lambda b: b[1]['uiTokenAmount'].update(decimals=19)]
        for change in changes:
            with self.subTest(change=change):
                tx = fixture()
                change(tx['meta']['preTokenBalances'])
                state, events, _ = classify(tx)
                self.assertEqual(state, 'unclassified')
                self.assertEqual(events, [])
        tx = fixture()
        tx['meta']['postTokenBalances'] = copy.deepcopy(tx['meta']['preTokenBalances'])
        tx['meta']['postTokenBalances'][1]['mint'] = key(30)
        self.assertEqual(classify(tx)[2], 'TOKEN_METADATA_INCONSISTENT')

    def test_exact_pool_tracked_mint_and_instruction_prefix_are_required(self):
        for metadata in (dict(META, pair=key(30)), dict(META, address=key(30))):
            self.assertNotEqual(classify(fixture(), metadata)[0], 'processed')
            self.assertEqual(classify(fixture(), metadata)[1], [])
        for change in ('unknown_cash', 'shifted_prefix', 'wrong_pool', 'duplicate_mints'):
            with self.subTest(change=change):
                tx = fixture()
                accounts = tx['transaction']['message']['instructions'][0]['accounts']
                if change == 'unknown_cash':
                    accounts[3] = key(30)
                elif change == 'shifted_prefix':
                    accounts[2], accounts[3] = accounts[3], accounts[2]
                elif change == 'wrong_pool':
                    accounts[0], accounts[2] = accounts[2], accounts[0]
                else:
                    accounts[3] = MINT
                self.assertNotEqual(classify(tx)[0], 'processed')
                self.assertEqual(classify(tx)[1], [])

    def test_event_wallet_pool_and_both_user_token_accounts_must_match(self):
        for offset in (120, 152, 184, 216):
            with self.subTest(event_field=offset):
                tx = fixture()
                change_event(tx, offset, tape._b58decode(key(30)))
                state, events, reason = classify(tx)
                self.assertEqual((state, reason), ('unclassified', 'SWAP_EVENT_COVERAGE_INCOMPLETE'))
                self.assertEqual(events, [])

    def test_missing_or_extra_events_do_not_prove_complete_swap_coverage(self):
        for instruction_count, event_count in ((2, 1), (1, 2)):
            tx = fixture(directions=('BUY',)*instruction_count)
            events = tx['meta']['innerInstructions'][0]['instructions']
            events[:] = (events[:1]*event_count)
            self.assertEqual(classify(tx)[2], 'SWAP_EVENT_COVERAGE_INCOMPLETE')

    def test_duplicate_event_cannot_cover_a_different_instruction(self):
        tx = fixture(directions=('BUY', 'SELL'))
        events = tx['meta']['innerInstructions'][0]['instructions']
        events[1] = copy.deepcopy(events[0])
        state, decoded, reason = classify(tx)
        self.assertEqual((state, reason), ('unclassified', 'SWAP_EVENT_COVERAGE_INCOMPLETE'))
        self.assertEqual(len(decoded), 1)

    def test_unknown_failed_and_wrong_program_instructions_fail_closed(self):
        for change in ('unknown_discriminator', 'wrong_program', 'failed'):
            tx = fixture()
            instruction = tx['transaction']['message']['instructions'][0]
            if change == 'unknown_discriminator':
                instruction['data'] = tape._b58encode(bytes([255])*24)
            elif change == 'wrong_program':
                instruction['programId'] = key(30)
            else:
                tx['meta']['err'] = {'InstructionError': [0, 'failure']}
            self.assertNotEqual(classify(tx)[0], 'processed')
            self.assertEqual(classify(tx)[1], [])

    def test_signer_fresh_fx_and_event_causality_guards_remain_required(self):
        tx = fixture()
        for entry in tx['transaction']['message']['accountKeys']:
            entry['signer'] = False
        state, events, reason = classify(tx)
        self.assertEqual((state, reason), ('unclassified', 'SWAP_ACTOR_NOT_TRANSACTION_SIGNER'))
        self.assertIn('SWAP_ACTOR_NOT_TRANSACTION_SIGNER', events[0]['quality_flags'])
        for change in ({'quote_reference_at': NOW-60_001},
                       {'quote_reference_at': NOW+1},
                       {'quote_usd_reference': None},
                       {'quote_reference_source': 'SCANNER_QUOTE_ASSET_REFERENCE_ESTIMATE'}):
            self.assertEqual(classify(fixture(), dict(META, **change))[0], 'unclassified')
        tx = fixture()
        change_event(tx, 8, (NOW//1000+3).to_bytes(8, 'little'))
        self.assertEqual(classify(tx)[2], 'FUTURE_OR_INCONSISTENT_EVENT_TIME')
        self.assertEqual(classify(fixture(), available=NOW-1)[2], 'OBSERVATION_TIME_ORDER_INVALID')


if __name__ == '__main__':
    unittest.main()
