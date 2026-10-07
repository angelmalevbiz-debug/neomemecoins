"""Fee evidence must share the recorder's instruction and coverage proof."""
import unittest

import live_tape as tape
import strategy_lab as lab
from test_pump_event_fees import event_fixture
from test_tape_reversed_orientation import fixture, classify, NOW, META


def transaction_with_full_event(side='SELL', *, exact=False):
    tx = fixture(directions=(side,), exact_quote=exact)
    event = tx['meta']['innerInstructions'][0]['instructions'][0]
    original = tape._b58decode(event['data'])[8:]
    payload = bytearray(event_fixture(side, instruction_name='buy_exact_quote_in' if exact else 'buy'))
    payload[8:16] = (NOW//1000).to_bytes(8, 'little', signed=True)
    payload[16:24] = (2_000_000_000).to_bytes(8, 'little')
    payload[120:248] = original[120:248]
    event['data'] = tape._b58encode(tape.ANCHOR_EVENT_CPI + payload)
    return tx


class TapeFeeObservationTests(unittest.TestCase):
    def test_processed_reversed_pool_authenticates_fee_using_onchain_side(self):
        for side, token_side, exact in [('SELL', 'BUY', False),
                                        ('BUY', 'SELL', False), ('BUY', 'SELL', True)]:
            with self.subTest(side=side, exact=exact):
                state, events, reason = classify(transaction_with_full_event(side, exact=exact))
                self.assertEqual((state, reason), ('processed', None))
                fee = events[0]['fee_evidence']
                self.assertEqual(fee['instruction_side'], side)
                self.assertEqual(fee['token_direction'], token_side)
                self.assertEqual(fee['address'], META['address'])
                self.assertEqual(fee['pairAddress'], META['pair'])
                self.assertTrue(fee['provenance_authenticated'])
                self.assertTrue(fee['full_transaction_swap_coverage'])
                self.assertFalse(fee['entry_permission'])
                self.assertEqual(fee['fee_bps'], 30)

    def test_legacy_or_unknown_fee_payload_does_not_erase_supported_swap(self):
        state, events, reason = classify(fixture(directions=('SELL',)))
        self.assertEqual((state, reason), ('processed', None))
        self.assertNotIn('fee_evidence', events[0])
        tx = transaction_with_full_event()
        event = tx['meta']['innerInstructions'][0]['instructions'][0]
        event['data'] = tape._b58encode(tape._b58decode(event['data']) + b'unknown-tail')
        state, events, reason = classify(tx)
        self.assertEqual((state, reason), ('processed', None))
        self.assertNotIn('fee_evidence', events[0])

    def test_incomplete_transaction_or_missing_fx_never_authenticates_fee(self):
        tx = transaction_with_full_event()
        extra = fixture(directions=('BUY',))['transaction']['message']['instructions'][0]
        tx['transaction']['message']['instructions'].append(extra)
        state, events, reason = classify(tx)
        self.assertEqual((state, reason), ('unclassified', 'SWAP_EVENT_COVERAGE_INCOMPLETE'))
        self.assertFalse(events[0]['fee_evidence']['provenance_authenticated'])
        tx = transaction_with_full_event()
        state, events, _ = classify(tx, {**META, 'quote_usd_reference': None})
        self.assertEqual(state, 'unclassified')
        self.assertFalse(events[0]['fee_evidence']['provenance_authenticated'])
        tx['slot'] = None
        state, events, _ = classify(tx)
        self.assertEqual(state, 'processed')
        self.assertFalse(events[0]['fee_evidence']['provenance_authenticated'])

    def test_observed_fee_never_changes_existing_paper_execution_rate(self):
        _, events, _ = classify(transaction_with_full_event())
        c = {'dexId': 'pumpswap', 'quoteTokenAddress': lab.SOL_QUOTE_MINT,
             'priceUsd': .00015, 'priceNative': .000001, 'marketCap': 200_000}
        expected = lab.pumpswap_fee_bps(c)
        self.assertGreater(expected, events[0]['fee_evidence']['fee_bps'])
        self.assertEqual(lab.pumpswap_fee_bps({**c, 'fee_evidence': events[0]['fee_evidence']}), expected)

    def test_unknown_pool_instruction_keeps_swap_but_cannot_authenticate_fees(self):
        for program in (tape.PUMP_AMM, 'UnknownProgram'):
            with self.subTest(program=program):
                tx = transaction_with_full_event()
                tx['transaction']['message']['instructions'].append({
                    'programId': program, 'accounts': [META['pair']],
                    'data': tape._b58encode(bytes([255])*24)})
                state, events, reason = classify(tx)
                self.assertEqual((state, reason), ('processed', None))
                self.assertFalse(events[0]['fee_evidence']['provenance_authenticated'])
                self.assertFalse(events[0]['fee_evidence']['full_transaction_swap_coverage'])


if __name__ == '__main__':
    unittest.main()
