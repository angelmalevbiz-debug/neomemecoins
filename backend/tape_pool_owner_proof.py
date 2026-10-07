"""Bounded confirmed RPC ownership evidence for complete no-Pump call trees.

Only a live foreign instruction that references a tracked pool and has a fully
matched successful instruction/log tree is eligible. One getMultipleAccounts
request per recorder poll observes owners after transaction bodies arrived.
This is no swap-direction inference and is never a cached ownership claim.
"""
import requests
from pool_reference_proof import complete_program_execution_proof, PUMP_AMM

_B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def _public_key(value):
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        return False
    try:
        number = 0
        for char in value:
            number = number * 58 + _B58.index(char)
        size = (number.bit_length() + 7) // 8
        return len(value) - len(value.lstrip('1')) + size == 32
    except ValueError:
        return False


def foreign_reference_candidate(tx, metadata, infra_programs):
    """Refuse owner work for swaps, incomplete execution, or invalid identity."""
    try:
        pair, mint, slot = metadata['pair'], metadata['address'], tx.get('slot')
        if (not _public_key(pair) or not _public_key(mint)
                or type(slot) is not int or slot <= 0
                or not complete_program_execution_proof(tx, PUMP_AMM)):
            return False
        instructions = list(tx['transaction']['message']['instructions'])
        for group in tx['meta']['innerInstructions']:
            instructions.extend(group['instructions'])
        return any(pair in instruction.get('accounts', [])
                   and instruction['programId'] not in infra_programs
                   for instruction in instructions)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def collect_pool_owner_proofs(candidates, rpc, *, clock, tx_budget,
                              bodies_received_at, infra_programs):
    """Return only current exact identities backed by aligned confirmed owners.

    The RPC address array is unique and bounded by the transaction budget and
    Solana's 100-account ceiling. A malformed/short array or an older context
    rejects the entire observation; null/invalid accounts receive no proof.
    """
    selected = {}
    identities = set()
    maximum_slot = 0
    limit = min(100, max(0, int(tx_budget)))
    for tx, metadata in candidates:
        if not foreign_reference_candidate(tx, metadata, infra_programs):
            continue
        pair, mint = metadata['pair'], metadata['address']
        if pair not in selected and len(selected) >= limit:
            continue
        selected.setdefault(pair, None)
        identities.add((pair, mint))
        maximum_slot = max(maximum_slot, tx['slot'])
    if not selected:
        return {}
    pairs = list(selected)
    calls = [('getMultipleAccounts', [pairs, {
        'commitment': 'confirmed', 'minContextSlot': maximum_slot,
        'encoding': 'base64', 'dataSlice': {'offset': 0, 'length': 0},
    }])]
    try:
        answers = rpc(calls)
        checked = clock()
        if (not isinstance(answers, list) or len(answers) != 1
                or not isinstance(answers[0], dict) or answers[0].get('error')
                or type(checked) is not int or checked <= 0
                or type(bodies_received_at) is not int or checked < bodies_received_at):
            return {}
        result = answers[0].get('result')
        if not isinstance(result, dict) or not isinstance(result.get('context'), dict):
            return {}
        context_slot, values = result['context'].get('slot'), result.get('value')
        if (type(context_slot) is not int or context_slot < maximum_slot
                or not isinstance(values, list) or len(values) != len(pairs)):
            return {}
        owners = {}
        for pair, account in zip(pairs, values):
            if (isinstance(account, dict) and account.get('owner') == PUMP_AMM
                    and account.get('executable') is False):
                owners[pair] = account['owner']
        return {(pair, mint): {
            'pairAddress': pair, 'address': mint, 'owner': owners[pair],
            'executable': False, 'checked_at': checked, 'context_slot': context_slot,
            'commitment': 'confirmed', 'source': 'SOLANA_RPC_ACCOUNT_OWNER',
        } for pair, mint in identities if pair in owners}
    except (requests.RequestException, RuntimeError, OSError, AttributeError,
            KeyError, TypeError, ValueError, IndexError, OverflowError):
        return {}
