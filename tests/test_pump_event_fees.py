"""Offline official-schema fixtures; none represent an observed live trade."""
import struct
import unittest

import pump_event_fees as fees


def event_fixture(side='SELL', *, gross=1_000_000, lp_bps=25, protocol_bps=5,
                  creator_bps=0, creator_present=False, cashback_bps=0,
                  buyback_bps=0, holder_duplicate=False, instruction_name='buy',
                  sdk_unclaimed=False):
    """Independent offset fixture for the complete published Anchor schema."""
    amounts = [(gross * value + 9_999) // 10_000
               for value in (lp_bps, protocol_bps, creator_bps, cashback_bps, buyback_bps)]
    if not creator_present:
        amounts[2] = 0
    charged = sum(amounts)
    name = instruction_name.encode('utf-8')
    length = (478 + len(name) if side == 'BUY' else 433) + (8 if sdk_unclaimed else 0)
    payload = bytearray(length)
    payload[:8] = fees.BUY_EVENT if side == 'BUY' else fees.SELL_EVENT
    struct.pack_into('<q', payload, 8, 1_791_400_000)
    # Distinct reserves and base quantity expose accidental field shifts.
    for offset, value in ((16, 40_000), (24, gross + charged + 100 if side == 'BUY' else 1),
                          (32, 17), (40, 18), (48, 19), (56, 20), (64, gross),
                          (72, lp_bps), (80, amounts[0]), (88, protocol_bps), (96, amounts[1]),
                          (104, gross + amounts[0] if side == 'BUY' else gross - amounts[0]),
                          (112, gross + charged if side == 'BUY' else gross - charged),
                          (344, creator_bps), (352, amounts[2])):
        struct.pack_into('<Q', payload, offset, value)
    for offset in (120, 152, 184, 216, 248, 280):
        payload[offset:offset + 32] = bytes([offset // 32]) * 32
    if creator_present:
        payload[312:344] = bytes([9]) * 32
    if side == 'BUY':
        payload[360] = 1
        for offset, value in ((361, 21), (369, 22), (377, 23), (385, 24), (393, 39_999)):
            struct.pack_into('<Q', payload, offset, value)
        struct.pack_into('<I', payload, 401, len(name))
        payload[405:405 + len(name)] = name
        tail = 405 + len(name)
    else:
        tail = 360
    for relative, value in ((0, cashback_bps), (8, amounts[3]),
                            (16, buyback_bps), (24, amounts[4])):
        struct.pack_into('<Q', payload, tail + relative, value)
    payload[tail + 32:tail + 48] = (-1_234).to_bytes(16, 'little', signed=True)
    payload[tail + 48] = 0
    struct.pack_into('<Q', payload, tail + 49, 1_000_000_000_000_000)
    struct.pack_into('<Q', payload, tail + 57, creator_bps if holder_duplicate else 0)
    struct.pack_into('<Q', payload, tail + 65, amounts[2] if holder_duplicate else 0)
    if sdk_unclaimed:
        struct.pack_into('<Q', payload, tail + 73, 31)
    return bytes(payload)


def with_u64(payload, offset, value):
    changed = bytearray(payload)
    struct.pack_into('<Q', changed, offset, value)
    return bytes(changed)


class CompletePumpEventFeesTests(unittest.TestCase):
    def test_flat_sell_reconciles_real_amount_loss_and_remains_unauthenticated(self):
        result = fees.decode_fee_evidence(event_fixture(), 'SELL')
        self.assertEqual(result['fee_bps'], 30)
        self.assertEqual(result['gross_quote_raw'], '1000000')
        self.assertEqual(result['user_quote_raw'], '997000')
        self.assertEqual(result['total_charged_fee_raw'], '3000')
        self.assertFalse(result['provenance_authenticated'])
        self.assertFalse(result['is_execution_quote'])
        self.assertFalse(result['entry_permission'])
        self.assertEqual(result['version'], 'OBSERVED_PUMP_EVENT_FEES_V1')

    def test_buy_and_exact_quote_in_use_full_variable_name_tail(self):
        for name in ('buy', 'buy_exact_quote_in'):
            with self.subTest(name=name):
                payload = event_fixture('BUY', instruction_name=name)
                result = fees.decode_fee_evidence(payload, name)
                self.assertEqual(result['instruction_name'], name)
                self.assertEqual(result['user_quote_raw'], '1003000')
                self.assertEqual(result['components']['lp']['raw_amount'], '2500')
                self.assertIsNotNone(fees.decode_fee_evidence(payload, 'BUY'))

    def test_current_sdk_known_unclaimed_append_is_not_a_charge(self):
        for side in ('BUY', 'SELL'):
            with self.subTest(side=side):
                result = fees.decode_fee_evidence(event_fixture(side, sdk_unclaimed=True), side)
                self.assertEqual(result['fee_bps'], 30)
                self.assertEqual(result['creator_fee_unclaimed_raw'], '31')
                self.assertEqual(result['schema'], 'PUMP_AMM_SDK_2_0_0_FULL_CREATOR_UNCLAIMED')

    def test_holder_rewards_repeat_creator_instead_of_double_charge(self):
        result = fees.decode_fee_evidence(event_fixture(creator_bps=30, creator_present=True,
                                                        holder_duplicate=True), 'SELL')
        self.assertEqual(result['fee_bps'], 60)
        self.assertEqual(result['total_charged_fee_raw'], '6000')
        self.assertTrue(result['holder_rewards_included_in_creator'])
        self.assertEqual(result['components']['holder_rewards'], result['components']['creator'])

    def test_unknown_holder_routing_or_double_charge_is_rejected(self):
        payload = event_fixture(creator_bps=30, creator_present=True, holder_duplicate=True)
        self.assertIsNone(fees.decode_fee_evidence(with_u64(payload, 425, 3001), 'SELL'))
        self.assertIsNone(fees.decode_fee_evidence(with_u64(payload, 112, 991000), 'SELL'))
        self.assertIsNone(fees.decode_fee_evidence(with_u64(payload, 417, 31), 'SELL'))

    def test_default_creator_can_waive_emitted_schedule_rate_but_no_positive_charge(self):
        result = fees.decode_fee_evidence(event_fixture(creator_bps=95), 'SELL')
        self.assertEqual(result['fee_bps'], 30)
        self.assertEqual(result['components']['creator']['emitted_bps'], 95)
        self.assertTrue(result['creator_fee_waived'])
        self.assertIsNone(fees.decode_fee_evidence(with_u64(event_fixture(creator_bps=95), 352, 9500), 'SELL'))

    def test_nondefault_creator_requires_full_ceiled_fee(self):
        payload = event_fixture(creator_bps=95, creator_present=True)
        self.assertEqual(fees.decode_fee_evidence(payload, 'SELL')['fee_bps'], 125)
        self.assertIsNone(fees.decode_fee_evidence(with_u64(payload, 352, 0), 'SELL'))

    def test_buyback_is_counted_only_with_gross_basis_and_separate_net_charge(self):
        for side in ('BUY', 'SELL'):
            with self.subTest(side=side):
                payload = event_fixture(side, buyback_bps=5)
                result = fees.decode_fee_evidence(payload, side)
                self.assertEqual(result['fee_bps'], 35)
                self.assertEqual(result['total_charged_fee_raw'], '3500')
                self.assertEqual(result['buyback_charge_basis'], 'SEPARATE_GROSS_QUOTE_FEE')
        payload = event_fixture(buyback_bps=5)
        # Buyback reported as a share of another fee is not a supported extra
        # gross-basis charge; the helper refuses it instead of guessing.
        self.assertIsNone(fees.decode_fee_evidence(with_u64(payload, 384, 1), 'SELL'))
        self.assertIsNone(fees.decode_fee_evidence(with_u64(payload, 112, 997000), 'SELL'))

    def test_nonzero_cashback_is_never_subtracted_as_income(self):
        payload = event_fixture(cashback_bps=10)
        self.assertIsNone(fees.decode_fee_evidence(payload, 'SELL'))
        self.assertIsNone(fees.decode_fee_evidence(with_u64(payload, 368, 0), 'SELL'))
        self.assertIsNone(fees.decode_fee_evidence(with_u64(event_fixture(), 368, 1), 'SELL'))

    def test_individually_rounded_tiny_swap_uses_higher_observed_cost(self):
        result = fees.decode_fee_evidence(event_fixture(gross=1000), 'SELL')
        self.assertEqual(result['nominal_charged_fee_bps'], 30)
        self.assertEqual(result['total_charged_fee_raw'], '4')
        self.assertEqual(result['fee_bps'], 40)

    def test_u64_values_are_retained_without_float_loss(self):
        result = fees.decode_fee_evidence(event_fixture(gross=9_007_199_254_740_993), 'SELL')
        self.assertEqual(result['gross_quote_raw'], '9007199254740993')
        self.assertGreaterEqual(result['fee_bps'], 30)

    def test_gross_intermediate_and_net_must_all_reconcile(self):
        payload = event_fixture()
        for offset in (64, 80, 96, 104, 112):
            with self.subTest(offset=offset):
                value = int.from_bytes(payload[offset:offset + 8], 'little')
                self.assertIsNone(fees.decode_fee_evidence(with_u64(payload, offset, value + 1), 'SELL'))

    def test_instruction_side_and_exact_buy_name_must_match(self):
        self.assertIsNone(fees.decode_fee_evidence(event_fixture(), 'BUY'))
        self.assertIsNone(fees.decode_fee_evidence(event_fixture('BUY'), 'SELL'))
        self.assertIsNone(fees.decode_fee_evidence(event_fixture('BUY'), 'buy_exact_quote_in'))
        self.assertIsNone(fees.decode_fee_evidence(event_fixture('BUY', instruction_name='buy_exact_quote_in'), 'buy'))
        for side in ('buy_v2', '', 'buy ', None, 1, {}):
            self.assertIsNone(fees.decode_fee_evidence(event_fixture('BUY'), side))

    def test_partial_legacy_or_unknown_full_tails_remain_unsupported(self):
        for payload, side in ((event_fixture(), 'SELL'), (event_fixture('BUY'), 'BUY')):
            for length in (248, 360, len(payload) - 1, len(payload) - 8, len(payload) - 16):
                with self.subTest(side=side, length=length):
                    self.assertIsNone(fees.decode_fee_evidence(payload[:length], side))
            for extra in (b'\0', bytes(7), bytes(9), bytes(16)):
                self.assertIsNone(fees.decode_fee_evidence(payload + extra, side))

    def test_malformed_string_utf8_and_bool_fields_are_rejected(self):
        payload = bytearray(event_fixture('BUY'))
        for offset in (360, 405 + len('buy') + 48):
            changed = payload.copy()
            changed[offset] = 2
            self.assertIsNone(fees.decode_fee_evidence(bytes(changed), 'BUY'))
        changed = payload.copy()
        struct.pack_into('<I', changed, 401, 2**32 - 1)
        self.assertIsNone(fees.decode_fee_evidence(bytes(changed), 'BUY'))
        changed = payload.copy()
        changed[405] = 255
        self.assertIsNone(fees.decode_fee_evidence(bytes(changed), 'BUY'))
        changed = payload.copy()
        changed[405:408] = b'bad'
        self.assertIsNone(fees.decode_fee_evidence(bytes(changed), 'BUY'))
        changed = bytearray(event_fixture())
        changed[408] = 2
        self.assertIsNone(fees.decode_fee_evidence(bytes(changed), 'SELL'))

    def test_invalid_amount_rate_and_transaction_limits_reject(self):
        payload = event_fixture()
        for offset in (16, 64, 112):
            self.assertIsNone(fees.decode_fee_evidence(with_u64(payload, offset, 0), 'SELL'))
        self.assertIsNone(fees.decode_fee_evidence(with_u64(payload, 72, 10001), 'SELL'))
        self.assertIsNone(fees.decode_fee_evidence(with_u64(payload, 24, 1_000_000), 'SELL'))
        for name in ('buy', 'buy_exact_quote_in'):
            self.assertIsNone(fees.decode_fee_evidence(with_u64(
                event_fixture('BUY', instruction_name=name), 24, 1_000_000), name))
        self.assertIsNone(fees.decode_fee_evidence(with_u64(event_fixture('BUY'), 393, 40_001), 'BUY'))
        self.assertIsNone(fees.decode_fee_evidence(event_fixture('BUY', gross=1), 'BUY'))

    def test_payload_type_discriminator_and_bound_are_strict(self):
        for payload in (None, '', {}, [], 1, bytes(513), bytes(433)):
            self.assertIsNone(fees.decode_fee_evidence(payload, 'SELL'))
        payload = bytearray(event_fixture())
        payload[0] ^= 1
        self.assertIsNone(fees.decode_fee_evidence(bytes(payload), 'SELL'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
