"""Recorder obtains authoritative owner observations for proven foreign noops."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import live_tape as tape
import tape_pool_owner_proof as owner_proof
from test_pool_reference_proof import fixture, NOW, POOL, MINT, PUMP
from test_tape_execution_repair import transaction, META as SWAP_META, NOW as SWAP_NOW, pubkey


def reference_fixture(*, pair=POOL, mint=MINT, slot=454_279_173):
    tx, metadata = fixture()
    metadata.pop('pool_owner_proof')
    tx['slot'] = slot
    for key in tx['transaction']['message']['accountKeys']:
        if key['pubkey'] == POOL:
            key['pubkey'] = pair
        elif key['pubkey'] == MINT:
            key['pubkey'] = mint
    instruction = tx['transaction']['message']['instructions'][0]
    instruction['accounts'] = [pair, mint, PUMP]
    metadata.update(pair=pair, address=mint)
    return tx, metadata


def owners_result(options, *, slot=None):
    return [{'result': {'context': {'slot': options[1]['minContextSlot'] if slot is None else slot},
                         'value': [{'owner': PUMP, 'executable': False, 'data': ['', 'base64']}
                                   for _ in options[0]]}}]


class TapeOwnerProofTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.clock = [NOW]
        self.rec = tape.TapeRecorder(Path(self.directory.name)/'tape.sqlite',
                                     clock=lambda: self.clock[0], tx_budget=4,
                                     historical_tx_budget=1)

    def tearDown(self):
        self.rec.close()
        self.directory.cleanup()

    def seed(self, signature, tx, metadata, *, event_time=None, state='pending', attempts=0):
        event_time = self.clock[0] if event_time is None else event_time
        with self.rec.db:
            self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,
                observed,metadata,state,attempts) VALUES(?,?,?,?,?,?,?,?)''',
                (signature, metadata['pair'], tx['slot'], event_time, self.clock[0],
                 json.dumps(metadata), state, attempts))

    def state(self, signature):
        return dict(self.rec.db.execute('SELECT * FROM signatures WHERE signature=?',
                                       (signature,)).fetchone())

    def provider(self, transactions, *, owner_answer=None):
        calls_seen = []

        def rpc(calls):
            calls_seen.append(copy.deepcopy(calls))
            if calls[0][0] == 'getTransaction':
                return [{'result': transactions[params[0]]} for _, params in calls]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], 'getMultipleAccounts')
            return owners_result(calls[0][1]) if owner_answer is None else owner_answer(calls[0][1])

        return rpc, calls_seen

    def test_complete_live_noop_gets_one_fresh_confirmed_owner_batch_after_bodies(self):
        transactions = {}
        identities = [(POOL, MINT), (POOL, MINT), (pubkey(20), pubkey(21))]
        for index, (pair, mint) in enumerate(identities):
            tx, metadata = reference_fixture(pair=pair, mint=mint, slot=454_279_173+index)
            signature = f'live-{index}'
            transactions[signature] = tx
            self.seed(signature, tx, metadata)
        rpc, seen = self.provider(transactions)

        def receiving(calls):
            answer = rpc(calls)
            self.clock[0] += 1
            return answer

        with patch.object(tape, 'classify_transaction', wraps=tape.classify_transaction) as classifier:
            self.rec.process(receiving)
        self.assertEqual([calls[0][0] for calls in seen], ['getTransaction', 'getMultipleAccounts'])
        pairs, options = seen[1][0][1]
        self.assertEqual(set(pairs), {POOL, pubkey(20)})
        self.assertEqual(len(pairs), 2)
        self.assertEqual(options, {'commitment': 'confirmed', 'minContextSlot': 454_279_175,
                                  'encoding': 'base64', 'dataSlice': {'offset': 0, 'length': 0}})
        for call in classifier.call_args_list:
            proof = call.args[1]['pool_owner_proof']
            self.assertEqual(proof['checked_at'], NOW+2)
            self.assertEqual(proof['context_slot'], 454_279_175)
            self.assertEqual(proof['owner'], PUMP)
            self.assertIs(proof['executable'], False)
        for signature in transactions:
            saved = self.state(signature)
            self.assertEqual(saved['state'], 'non_swap')
            self.assertEqual(saved['reason'], 'VERIFIED_FOREIGN_POOL_REFERENCE_NO_SWAP')
            self.assertNotIn('pool_owner_proof', json.loads(saved['metadata']))
        self.assertEqual(self.rec.db.execute('SELECT count(*) FROM events').fetchone()[0], 0)

    def test_missing_or_wrong_owner_and_non_readonly_schema_stay_unclassified(self):
        variations = [None, {}, {'owner': PUMP}, {'owner': PUMP, 'executable': True},
                      {'owner': PUMP, 'executable': 0}, {'owner': pubkey(14), 'executable': False}]
        for index, account in enumerate(variations):
            with self.subTest(account=account):
                tx, metadata = reference_fixture()
                signature = f'bad-owner-{index}'
                self.seed(signature, tx, metadata)
                rpc, _ = self.provider({signature: tx}, owner_answer=lambda options, item=account: [
                    {'result': {'context': {'slot': options[1]['minContextSlot']}, 'value': [item]}}])
                self.rec.process(rpc)
                self.assertEqual(self.state(signature)['state'], 'unclassified')

    def test_old_or_invalid_context_and_partial_answers_refuse_entire_batch(self):
        variants = [[], [{}], [{'error': {'code': -32016}}],
                    [{'result': {'context': {'slot': True}, 'value': []}}],
                    [{'result': {'context': {}, 'value': []}}],
                    [{'result': {'context': {'slot': 454_279_172},
                                 'value': [{'owner': PUMP, 'executable': False}]}}],
                    [{'result': {'context': {'slot': 454_279_173}, 'value': []}}]]
        for index, answer in enumerate(variants):
            with self.subTest(answer=answer):
                tx, metadata = reference_fixture()
                signature = f'bad-batch-{index}'
                self.seed(signature, tx, metadata)
                rpc, _ = self.provider({signature: tx}, owner_answer=lambda _, result=answer: result)
                self.rec.process(rpc)
                self.assertEqual(self.state(signature)['state'], 'unclassified')

    def test_error_field_with_valid_result_never_supplies_authoritative_owner_evidence(self):
        # JSON-RPC success and error envelopes are mutually exclusive. Even a
        # falsey malformed error must not turn an unavailable owner observation
        # into proof that clears current coverage.
        for index, error in enumerate(({}, None, {'code': -32016, 'message': 'unavailable'})):
            with self.subTest(error=error):
                tx, metadata = reference_fixture()
                signature = f'conflicting-envelope-{index}'
                self.seed(signature, tx, metadata)

                def conflicting_result(options, error_value=error):
                    answer = owners_result(options)
                    answer[0]['error'] = error_value
                    return answer

                rpc, seen = self.provider({signature: tx}, owner_answer=conflicting_result)
                self.rec.process(rpc)
                self.assertEqual(len(seen), 2)
                saved = self.state(signature)
                self.assertEqual(saved['state'], 'unclassified')
                self.assertEqual(saved['reason'], 'UNSUPPORTED_POOL_INSTRUCTION')
                self.assertEqual(self.rec.db.execute('SELECT count(*) FROM events').fetchone()[0], 0)

    def test_partial_multi_account_array_cannot_shift_proofs_between_pools(self):
        transactions = {}
        for index, (pair, mint) in enumerate([(POOL, MINT), (pubkey(20), pubkey(21))]):
            tx, metadata = reference_fixture(pair=pair, mint=mint)
            signature = f'partial-{index}'
            transactions[signature] = tx
            self.seed(signature, tx, metadata)
        rpc, seen = self.provider(transactions, owner_answer=lambda options: [
            {'result': {'context': {'slot': options[1]['minContextSlot']},
                        'value': [{'owner': PUMP, 'executable': False}]}}])
        self.rec.process(rpc)
        self.assertEqual(len(seen[1][0][1][0]), 2)
        self.assertTrue(all(self.state(signature)['state'] == 'unclassified' for signature in transactions))

    def test_context_must_reach_requested_maximum_slot_for_all_eligible_bodies(self):
        transactions = {}
        for index, slot in enumerate((454_279_173, 454_279_180)):
            tx, metadata = reference_fixture(pair=pubkey(20+index*2), mint=pubkey(21+index*2), slot=slot)
            signature = f'slot-{index}'
            transactions[signature] = tx
            self.seed(signature, tx, metadata)
        rpc, seen = self.provider(transactions, owner_answer=lambda options: owners_result(options, slot=454_279_179))
        self.rec.process(rpc)
        self.assertEqual(seen[1][0][1][1]['minContextSlot'], 454_279_180)
        self.assertTrue(all(self.state(signature)['state'] == 'unclassified' for signature in transactions))

    def test_provider_failure_never_changes_unknown_to_non_swap(self):
        tx, metadata = reference_fixture()
        self.seed('timeout', tx, metadata)

        def unavailable(_):
            raise tape.requests.Timeout('owner provider unavailable')

        rpc, seen = self.provider({'timeout': tx}, owner_answer=unavailable)
        self.rec.process(rpc)
        self.assertEqual(len(seen), 2)
        self.assertEqual(self.state('timeout')['state'], 'unclassified')

    def test_saved_owner_claim_is_not_trusted_and_metadata_is_not_rewritten(self):
        tx, metadata = fixture()
        original = copy.deepcopy(metadata)
        self.seed('saved-proof', tx, metadata)
        rpc, _ = self.provider({'saved-proof': tx}, owner_answer=lambda _: [])
        self.rec.process(rpc)
        self.assertEqual(self.state('saved-proof')['state'], 'unclassified')
        self.assertEqual(json.loads(self.state('saved-proof')['metadata']), original)

    def test_real_swaps_request_no_additional_owner_rpc(self):
        self.clock[0] = SWAP_NOW
        tx = transaction()
        self.seed('swap', tx, SWAP_META)

        def rpc(calls):
            self.assertTrue(all(method == 'getTransaction' for method, _ in calls))
            return [{'result': tx} for _ in calls]

        self.rec.process(rpc)
        self.assertEqual(self.state('swap')['state'], 'processed')
        self.assertEqual(self.rec.db.execute('SELECT count(*) FROM events').fetchone()[0], 1)

    def test_incomplete_execution_and_unknown_pump_calls_get_no_owner_request(self):
        for index, mode in enumerate(('missing-inner', 'missing-log', 'pump')):
            tx, metadata = reference_fixture()
            if mode == 'missing-inner':
                tx['meta']['innerInstructions'] = None
            elif mode == 'missing-log':
                tx['meta']['logMessages'] = None
            else:
                tx['transaction']['message']['instructions'][0]['programId'] = PUMP
                tx['meta']['logMessages'] = [f'Program {PUMP} invoke [1]', f'Program {PUMP} success']
            signature = f'incomplete-{index}'
            self.seed(signature, tx, metadata)
            rpc, seen = self.provider({signature: tx})
            self.rec.process(rpc)
            self.assertEqual(len(seen), 1)
            self.assertEqual(self.state(signature)['state'], 'unclassified')

    def test_historical_pending_or_terminal_unknown_rows_receive_no_new_proof(self):
        tx, metadata = reference_fixture()
        self.seed('live', tx, metadata)
        self.seed('old-pending', tx, metadata, event_time=NOW-600_000)
        self.seed('old-terminal', tx, metadata, event_time=NOW-600_000, state='unclassified', attempts=7)
        before = self.state('old-terminal')
        rpc, _ = self.provider({'live': tx, 'old-pending': tx})
        self.rec.process(rpc)
        self.assertEqual(self.state('live')['state'], 'non_swap')
        self.assertEqual(self.state('old-pending')['state'], 'unclassified')
        self.assertEqual(self.state('old-terminal'), before)

    def test_owner_request_budget_and_clock_order_are_enforced(self):
        candidates = [reference_fixture(pair=pubkey(20+index*2), mint=pubkey(21+index*2), slot=42+index)
                      for index in range(5)]
        seen = []

        def rpc(calls):
            seen.append(calls)
            return owners_result(calls[0][1])

        result = owner_proof.collect_pool_owner_proofs(candidates, rpc, clock=lambda: NOW,
            tx_budget=2, bodies_received_at=NOW, infra_programs=tape.INFRA_PROGRAMS)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0][0][1][0]), 2)
        self.assertEqual(len(result), 2)
        self.assertEqual(seen[0][0][1][1]['minContextSlot'], 43)
        self.assertEqual(owner_proof.collect_pool_owner_proofs(candidates, rpc, clock=lambda: NOW-1,
            tx_budget=2, bodies_received_at=NOW, infra_programs=tape.INFRA_PROGRAMS), {})


if __name__ == '__main__':
    unittest.main()
