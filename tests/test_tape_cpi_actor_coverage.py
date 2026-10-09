"""Known program-authorized swaps never impersonate independent wallets."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import live_tape as tape
import strategy_lab as lab
from test_tape_execution_repair import META, NOW, PAIR, pubkey, transaction

ROUTER = pubkey(90)


def routed():
    tx = transaction()
    msg = tx['transaction']['message']
    swap = copy.deepcopy(msg['instructions'][0])
    swap['stackHeight'] = 2
    for key in msg['accountKeys']:
        key['signer'] = False
    msg['accountKeys'].append({'pubkey':pubkey(91), 'signer':True})
    msg['instructions'] = [{'programId':ROUTER, 'accounts':[PAIR], 'data':'1'}]
    tx['meta']['innerInstructions'] = [{'index':0, 'instructions':[swap]}]
    tx['meta']['logMessages'] = [f'Program {ROUTER} invoke [1]'] + [
        line.replace('invoke [1]', 'invoke [2]') for line in tx['meta']['logMessages']
    ] + [f'Program {ROUTER} success']
    return tx


def classify(tx):
    return tape.classify_transaction(tx, META, observed_at=NOW, ingested_at=NOW+1)


class CpiActorCoverageTests(unittest.TestCase):
    def test_authenticated_cpi_is_known_but_still_excluded_from_wallet_flow(self):
        status, events, reason = classify(routed())
        self.assertEqual(status, 'processed')
        self.assertEqual(reason, 'VERIFIED_CPI_SWAP_EXCLUDED_FROM_WALLET_FLOW')
        self.assertEqual(events[0]['actor_authority'], 'PROGRAM_SIGNED_CPI')
        self.assertEqual(events[0]['quality_flags'], ['SWAP_ACTOR_NOT_TRANSACTION_SIGNER'])

    def test_missing_or_contradictory_context_remains_unclassified(self):
        for defect in ('height', 'parent', 'logs', 'no_transaction_signer'):
            tx = routed()
            if defect == 'height':
                tx['meta']['innerInstructions'][0]['instructions'][0].pop('stackHeight')
            elif defect == 'parent':
                tx['meta']['innerInstructions'][0]['index'] = 10
            elif defect == 'logs':
                tx['meta']['logMessages'] = [line for line in tx['meta']['logMessages']
                                            if line != f'Program {tape.PUMP_AMM} success']
            else:
                for key in tx['transaction']['message']['accountKeys']: key['signer'] = False
            status, _, reason = classify(tx)
            self.assertEqual((status, reason), ('unclassified','SWAP_ACTOR_NOT_TRANSACTION_SIGNER'), defect)

    def test_incomplete_event_or_failed_execution_never_gets_known_coverage(self):
        tx = routed(); tx['meta']['logMessages'] = [
            line for line in tx['meta']['logMessages'] if not line.startswith('Program data:')]
        self.assertEqual(classify(tx)[2], 'SWAP_EVENT_COVERAGE_INCOMPLETE')
        tx = routed(); tx['meta']['err'] = {'InstructionError':[0,'bad']}
        self.assertEqual(classify(tx)[0], 'failed')

    def test_known_non_wallet_swap_does_not_poison_coverage_or_supply_entry_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            rec = tape.TapeRecorder(Path(directory)/'db.sqlite', clock=lambda:NOW+1000,
                                    page_size=10,tx_budget=10)
            def rpc(calls):
                return [{'result':routed() if method=='getTransaction' else [
                    {'signature':'known-cpi','slot':42,'blockTime':NOW//1000,'err':None}]
                } for method,_ in calls]
            state = rec.poll([META],rpc)
            self.assertEqual(state['pair_coverage'][PAIR]['status'],'COMPLETE')
            self.assertEqual(state['events'][0]['quality_flags'],['SWAP_ACTOR_NOT_TRANSACTION_SIGNER'])
            path = Path(directory)/'tape.json'
            path.write_text(json.dumps(state),encoding='utf-8')
            with patch.object(lab,'LIVE_TAPE_PATH',path),patch.object(lab,'now_ms',return_value=NOW+1001):
                self.assertEqual(lab.flow_map(),{})
            rec.close()


if __name__ == '__main__':
    unittest.main()
