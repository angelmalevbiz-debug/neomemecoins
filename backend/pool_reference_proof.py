"""Prove a foreign pool reference executed no PumpSwap instruction.

No program whitelist, token-balance inference or guessed swap direction is used.
The complete JSONParsed instruction sequence must match the successful runtime
log call tree, with no Pump program invocation. A separate confirmed RPC owner
observation establishes the exact pool identity at or after the transaction slot.

RPC/CPI semantics: https://solana.com/docs/rpc/json-structures
Account ownership: https://solana.com/docs/core/accounts
"""
import re


PUMP_AMM = 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'
OWNER_PROOF_SOURCE = 'SOLANA_RPC_ACCOUNT_OWNER'
OWNER_MAX_AGE_MS = 30_000
_B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
_INVOKE = re.compile(r'^Program ([1-9A-HJ-NP-Za-km-z]+) invoke \[([0-9]+)\]$')
_SUCCESS = re.compile(r'^Program ([1-9A-HJ-NP-Za-km-z]+) success$')
_CONSUMED = re.compile(r'^Program ([1-9A-HJ-NP-Za-km-z]+) consumed [0-9]+ of [0-9]+ compute units$')
_RETURN = re.compile(r'^Program return: ([1-9A-HJ-NP-Za-km-z]+)(?: .*)?$')


def _public_key(value):
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        return False
    number = 0
    try:
        for char in value:
            number = number * 58 + _B58.index(char)
    except ValueError:
        return False
    size = (number.bit_length() + 7) // 8
    return len(value) - len(value.lstrip('1')) + size == 32


def _parsed_keys(tx):
    keys = tx['transaction']['message']['accountKeys']
    if not isinstance(keys, list) or not keys:
        raise ValueError('missing parsed account keys')
    result = []
    for key in keys:
        if (not isinstance(key, dict) or not _public_key(key.get('pubkey'))
                or type(key.get('writable')) is not bool
                or type(key.get('signer')) is not bool
                or ('source' in key and key['source'] not in ('transaction', 'lookupTable'))):
            raise ValueError('invalid parsed account key')
        result.append(key['pubkey'])
    if len(set(result)) != len(result):
        raise ValueError('ambiguous duplicate account key')
    return set(result)


def complete_program_execution_proof(tx, excluded_program=PUMP_AMM):
    """True only for a complete matching successful call tree without a program.

    Missing/null CPI or log metadata is unavailable evidence, never an empty
    execution tree. Malformed, omitted, extra, reordered or failed calls refuse
    this proof. In particular, an unknown Pump instruction remains unknown.
    """
    try:
        meta = tx['meta']
        if not isinstance(meta, dict) or 'err' not in meta or meta['err'] is not None:
            return False
        keys = _parsed_keys(tx)
        top = tx['transaction']['message']['instructions']
        inner, logs = meta.get('innerInstructions'), meta.get('logMessages')
        if (not isinstance(top, list) or not top or not isinstance(inner, list)
                or not isinstance(logs, list) or not logs):
            return False
        groups = {}
        for group in inner:
            if not isinstance(group, dict):
                return False
            index, instructions = group.get('index'), group.get('instructions')
            if (type(index) is not int or not 0 <= index < len(top)
                    or index in groups or not isinstance(instructions, list)):
                return False
            groups[index] = instructions
        expected = []
        for index, outer in enumerate(top):
            for instruction, is_inner in [(outer, False)] + [(i, True) for i in groups.get(index, [])]:
                if not isinstance(instruction, dict) or 'programIdIndex' in instruction:
                    return False
                program = instruction.get('programId')
                if program not in keys or program == excluded_program:
                    return False
                if 'accounts' in instruction:
                    accounts = instruction['accounts']
                    if (not isinstance(accounts, list)
                            or any(not isinstance(account, str) or account not in keys for account in accounts)):
                        return False
                    if not isinstance(instruction.get('data'), str):
                        return False
                elif not isinstance(instruction.get('parsed'), dict):
                    return False
                depth = instruction.get('stackHeight')
                if is_inner:
                    if type(depth) is not int or depth < 2:
                        return False
                else:
                    if depth is not None and (type(depth) is not int or depth != 1):
                        return False
                    depth = 1
                expected.append((program, depth))
        stack, actual = [], []
        for line in logs:
            if not isinstance(line, str) or 'truncat' in line.lower():
                return False
            invoke = _INVOKE.fullmatch(line)
            if invoke:
                program, depth = invoke.group(1), int(invoke.group(2))
                if program == excluded_program or depth != len(stack) + 1:
                    return False
                stack.append(program)
                actual.append((program, depth))
                continue
            success = _SUCCESS.fullmatch(line)
            if success:
                if not stack or stack.pop() != success.group(1):
                    return False
                continue
            consumed = _CONSUMED.fullmatch(line)
            returned = _RETURN.fullmatch(line)
            if consumed or returned:
                if not stack or stack[-1] != (consumed or returned).group(1):
                    return False
                continue
            if line.startswith(('Program log: ', 'Program data: ')):
                if not stack:
                    return False
                continue
            # Includes runtime failure logs and unknown runtime log formats.
            return False
        return not stack and actual == expected
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError):
        return False


def proves_no_pool_swap(tx, metadata, *, available_at):
    """A fresh authoritative owner observation plus complete no-Pump execution.

    Owner data is an additional exact-pool identity guard. The executed call
    tree is the reason this is a non-swap; a current owner observation alone
    does not reconstruct account ownership at an earlier transaction slot.
    """
    try:
        proof = metadata['pool_owner_proof']
        pair, mint = metadata['pair'], metadata['address']
        if (not isinstance(proof, dict) or not _public_key(pair) or not _public_key(mint)
                or proof.get('pairAddress') != pair or proof.get('address') != mint
                or proof.get('owner') != PUMP_AMM
                or proof.get('executable') is not False
                or proof.get('commitment') != 'confirmed'
                or proof.get('source') != OWNER_PROOF_SOURCE):
            return False
        checked, slot, tx_slot = proof.get('checked_at'), proof.get('context_slot'), tx.get('slot')
        if (type(available_at) is not int or type(checked) is not int or checked <= 0
                or not 0 <= available_at - checked <= OWNER_MAX_AGE_MS
                or type(slot) is not int or type(tx_slot) is not int
                or tx_slot <= 0 or slot < tx_slot):
            return False
        keys = _parsed_keys(tx)
        if pair not in keys:
            return False
        return complete_program_execution_proof(tx, PUMP_AMM)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError):
        return False
