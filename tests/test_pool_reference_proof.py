"""Coverage accepts a proven foreign reference, never guesses missing swaps."""
import copy
import unittest

import live_tape
from pool_reference_proof import complete_program_execution_proof, proves_no_pool_swap


NOW = 1_791_391_400_000
POOL = 'CyJwKnLiiY4fmiwqMCSWFSHxo7T9Ubshgs2uRAuJCrk5'
MINT = '7VertkgF9KLhxxJXHX6uaWuoYZTP9LdGj2bWmVXVpump'
WRAPPER = 'HVi6VyyLvTtFTA8f8atavxVjUKi8WjmnydfKgoZKzt7H'
TOKEN = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
PUMP = live_tape.PUMP_AMM


def fixture():
    # Same execution shape as the live Agency version-1 wrapper probe:
    # a writable pool reference, one successful foreign call, and no CPI.
    keys = [{'pubkey': key, 'writable': key == POOL, 'signer': False,
             'source': 'transaction'} for key in [POOL, MINT, WRAPPER, PUMP, TOKEN]]
    tx = {'version': 1, 'slot': 454_279_173, 'blockTime': NOW // 1000,
          'transaction': {'message': {'accountKeys': keys, 'instructions': [
              {'programId': WRAPPER, 'accounts': [POOL, MINT, PUMP],
               'data': '21', 'stackHeight': 1}]}},
          'meta': {'err': None, 'innerInstructions': [], 'logMessages': [
              f'Program {WRAPPER} invoke [1]',
              f'Program {WRAPPER} consumed 47351 of 300000 compute units',
              f'Program {WRAPPER} success']}}
    metadata = {'pair': POOL, 'address': MINT, 'symbol': 'Agency',
                'pool_owner_proof': {'pairAddress': POOL, 'address': MINT,
                                     'owner': PUMP, 'executable': False,
                                     'checked_at': NOW, 'context_slot': tx['slot'],
                                     'commitment': 'confirmed',
                                     'source': 'SOLANA_RPC_ACCOUNT_OWNER'}}
    return tx, metadata


class PoolReferenceProofTests(unittest.TestCase):
    def test_complete_writable_foreign_reference_with_confirmed_owner_is_non_swap(self):
        tx, metadata = fixture()
        self.assertTrue(complete_program_execution_proof(tx))
        self.assertTrue(proves_no_pool_swap(tx, metadata, available_at=NOW))
        self.assertEqual(live_tape.classify_transaction(
            tx, metadata, observed_at=NOW, ingested_at=NOW),
            ('non_swap', [], 'VERIFIED_FOREIGN_POOL_REFERENCE_NO_SWAP'))

    def test_no_owner_observation_never_exonerates_a_writable_foreign_reference(self):
        tx, metadata = fixture()
        metadata.pop('pool_owner_proof')
        self.assertFalse(proves_no_pool_swap(tx, metadata, available_at=NOW))
        self.assertEqual(live_tape.classify_transaction(
            tx, metadata, observed_at=NOW, ingested_at=NOW),
            ('unclassified', [], 'UNSUPPORTED_POOL_INSTRUCTION'))

    def test_owner_identity_freshness_slot_source_and_commitment_are_all_required(self):
        changes = [('owner', WRAPPER), ('pairAddress', MINT), ('address', POOL),
                   ('executable', True), ('executable', None),
                   ('checked_at', NOW-30_001), ('checked_at', NOW+1),
                   ('checked_at', 0), ('checked_at', True),
                   ('context_slot', 454_279_172), ('context_slot', True),
                   ('commitment', 'processed'), ('source', 'SCANNER_ESTIMATE')]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                tx, metadata = fixture()
                metadata['pool_owner_proof'][field] = value
                self.assertFalse(proves_no_pool_swap(tx, metadata, available_at=NOW))

    def test_owner_observation_alone_does_not_replace_missing_execution_evidence(self):
        for field in ['innerInstructions', 'logMessages']:
            for value in [None, {}]:
                with self.subTest(field=field, value=value):
                    tx, metadata = fixture()
                    tx['meta'][field] = value
                    self.assertFalse(proves_no_pool_swap(tx, metadata, available_at=NOW))
            tx, metadata = fixture()
            tx['meta'].pop(field)
            self.assertFalse(proves_no_pool_swap(tx, metadata, available_at=NOW))

    def test_unknown_pump_discriminator_is_never_proven_a_foreign_non_swap(self):
        tx, metadata = fixture()
        tx['transaction']['message']['instructions'][0]['programId'] = PUMP
        tx['meta']['logMessages'] = [f'Program {PUMP} invoke [1]', f'Program {PUMP} success']
        self.assertFalse(complete_program_execution_proof(tx))
        self.assertEqual(live_tape.classify_transaction(
            tx, metadata, observed_at=NOW, ingested_at=NOW),
            ('unclassified', [], 'UNSUPPORTED_POOL_INSTRUCTION'))

    def test_pump_cpi_or_unrecorded_pump_log_is_never_ignored(self):
        for add_inner in [True, False]:
            with self.subTest(add_inner=add_inner):
                tx, metadata = fixture()
                if add_inner:
                    tx['meta']['innerInstructions'] = [{'index': 0, 'instructions': [
                        {'programId': PUMP, 'accounts': [POOL], 'data': '21', 'stackHeight': 2}]}]
                tx['meta']['logMessages'][1:1] = [f'Program {PUMP} invoke [2]', f'Program {PUMP} success']
                self.assertFalse(proves_no_pool_swap(tx, metadata, available_at=NOW))

    def test_unknown_nested_cpi_can_be_proven_only_with_matching_complete_logs(self):
        tx, metadata = fixture()
        tx['meta']['innerInstructions'] = [{'index': 0, 'instructions': [
            {'programId': TOKEN, 'parsed': {'type': 'getAccountDataSize'}, 'stackHeight': 2}]}]
        tx['meta']['logMessages'][1:1] = [f'Program {TOKEN} invoke [2]', f'Program {TOKEN} success']
        self.assertTrue(proves_no_pool_swap(tx, metadata, available_at=NOW))
        tx['meta']['innerInstructions'] = []
        self.assertFalse(proves_no_pool_swap(tx, metadata, available_at=NOW))

    def test_recorded_cpi_with_missing_or_wrong_depth_logs_refuses_proof(self):
        tx, _ = fixture()
        tx['meta']['innerInstructions'] = [{'index': 0, 'instructions': [
            {'programId': TOKEN, 'parsed': {'type': 'getAccountDataSize'}, 'stackHeight': 2}]}]
        self.assertFalse(complete_program_execution_proof(tx))
        tx['meta']['logMessages'][1:1] = [f'Program {TOKEN} invoke [3]', f'Program {TOKEN} success']
        self.assertFalse(complete_program_execution_proof(tx))

    def test_truncated_failed_extra_missing_or_reordered_calls_refuse_proof(self):
        cases = [lambda t: t['meta']['logMessages'].append('Log truncated'),
                 lambda t: t['meta']['logMessages'].__setitem__(-1, f'Program {WRAPPER} failed: custom error'),
                 lambda t: t['meta']['logMessages'].pop(),
                 lambda t: t['meta']['logMessages'].__setitem__(0, f'Program {TOKEN} invoke [1]'),
                 lambda t: t['meta']['logMessages'].extend([f'Program {TOKEN} invoke [1]', f'Program {TOKEN} success']),
                 lambda t: t['meta']['logMessages'].reverse()]
        for mutate in cases:
            tx, metadata = fixture()
            mutate(tx)
            self.assertFalse(proves_no_pool_swap(tx, metadata, available_at=NOW))

    def test_malformed_or_unresolved_jsonparsed_records_refuse_proof(self):
        cases = [lambda t: t['transaction']['message']['accountKeys'].__setitem__(0, POOL),
                 lambda t: t['transaction']['message']['accountKeys'][0].pop('writable'),
                 lambda t: t['transaction']['message']['accountKeys'][0].__setitem__('pubkey', 'invalid'),
                 lambda t: t['transaction']['message']['accountKeys'].append(copy.deepcopy(t['transaction']['message']['accountKeys'][0])),
                 lambda t: t['transaction']['message']['instructions'][0].__setitem__('programIdIndex', 2),
                 lambda t: t['transaction']['message']['instructions'][0].__setitem__('accounts', [999]),
                 lambda t: t['meta'].__setitem__('innerInstructions', [{'index': 99, 'instructions': []}]),
                 lambda t: t['meta'].__setitem__('innerInstructions', [{'index': 0, 'instructions': []}, {'index': 0, 'instructions': []}])]
        for mutate in cases:
            tx, _ = fixture()
            mutate(tx)
            self.assertFalse(complete_program_execution_proof(tx))

    def test_empty_tree_or_failed_transaction_cannot_be_proof(self):
        tx, _ = fixture()
        tx['meta']['err'] = {'InstructionError': [0, 'Custom']}
        self.assertFalse(complete_program_execution_proof(tx))
        tx, _ = fixture()
        tx['transaction']['message']['instructions'] = []
        self.assertFalse(complete_program_execution_proof(tx))

    def test_user_log_text_cannot_spoof_a_runtime_invocation(self):
        tx, metadata = fixture()
        tx['meta']['logMessages'].insert(1, f'Program log: Program {PUMP} invoke [2]')
        self.assertTrue(proves_no_pool_swap(tx, metadata, available_at=NOW))

    def test_classification_preserves_unknown_writable_reference_with_bad_tree(self):
        tx, metadata = fixture()
        tx['meta']['logMessages'].pop()
        self.assertEqual(live_tape.classify_transaction(
            tx, metadata, observed_at=NOW, ingested_at=NOW),
            ('unclassified', [], 'UNSUPPORTED_POOL_INSTRUCTION'))


if __name__ == '__main__':
    unittest.main()
