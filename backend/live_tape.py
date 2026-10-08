#!/usr/bin/env python3
"""Durable, read-only Solana recorder; imports never touch account files.

The SQLite journal is authoritative. The bounded JSON projection is for UI.
The strict decoder currently supports official PumpSwap Buy/Sell instructions
and events. Unknown programs/ambiguous bodies degrade coverage, never imply
that there were no sellers. A signature is terminal only after classification.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
import math
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
import requests
import honest_quote_transport as quote_transport
import winner_ensemble
import entry_defense
from tape_pool_scheduler import PersonalEnginePositions, TapePoolScheduler
from pool_reference_proof import proves_no_pool_swap
from shared_snapshot_io import read_shared_text, replace_shared_snapshot
from tape_pool_owner_proof import collect_pool_owner_proofs, foreign_reference_candidate
from pump_event_fees import decode_fee_evidence

API_URL = os.getenv('NEO_LOCAL_API', 'http://127.0.0.1:8788/state')
RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://solana-rpc.publicnode.com')
OUT = Path(os.getenv('NEO_LIVE_TAPE_PATH', '/var/lib/neo-market/live_tape.json'))
MAX_TRACKED = int(os.getenv('NEO_TAPE_MAX_PAIRS', '12'))
MAX_EVENTS = int(os.getenv('NEO_TAPE_MAX_EVENTS', '1600'))
# PAGE_SIZE is a ceiling. Discovery shares the capped transaction budget
# across active pools and pagination rounds; a minimum page of one signature
# keeps each supported pool progressing when the pool count is high.
PAGE_SIZE = min(1000,max(1,int(os.getenv('NEO_TAPE_PAGE_SIZE','1000'))))
PAGE_BUDGET = max(1,int(os.getenv('NEO_TAPE_PAGES_PER_POLL','1')))
TX_BUDGET = max(1,int(os.getenv('NEO_TAPE_TX_PER_POLL','12')))
HISTORICAL_TX_BUDGET = max(0,int(os.getenv('NEO_TAPE_HISTORICAL_TX_PER_POLL','1')))
RPC_BATCH_SIZE = max(1,int(os.getenv('NEO_TAPE_RPC_BATCH_SIZE','20')))
RPC_TRANSACTION_CONCURRENCY = max(1,int(os.getenv('NEO_TAPE_RPC_TX_CONCURRENCY','4')))
WINDOW_MS = 300_000
DECISION_FLOW_WINDOW_MS = 30_000
MAX_DISCOVERY_LAG_MS = DECISION_FLOW_WINDOW_MS
POLL_SECONDS = float(os.getenv('NEO_TAPE_POLL_SECONDS','2.0'))
ATOMIC_REPLACE_ATTEMPTS = 8
PUMP_AMM = 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
WSOL = 'So11111111111111111111111111111111111111112'
# Official pump-fun/pump-public-docs/idl/pump_amm.json checked 2026-10-05.
SWAP_DISCRIMINATORS = {bytes([102,6,61,18,1,218,235,234]):'BUY',
                       bytes([198,46,21,82,180,217,232,112]):'BUY',
                       bytes([51,230,133,164,1,127,131,173]):'SELL'}
EVENT_DISCRIMINATORS = {bytes([103,244,82,31,44,245,119,119]):'BUY',
                        bytes([62,47,55,10,165,3,220,42]):'SELL'}
ANCHOR_EVENT_CPI = bytes([228,69,165,46,81,203,154,29])
# Every decoded event names the decoder path that produced it. The default
# path is the validated one. A newer path stays a shadow path: its events are
# recorded with a DECODER_PATH_UNVALIDATED quality flag, which every consumer
# already treats as DEGRADED flow, until the owner lists the version in
# NEO_TAPE_VALIDATED_DECODER_VERSIONS after a shadow comparison.
DECODER_VERSION = 'PUMP_SWAP_EVENT_DECODER_V1'
REVERSED_CASH_LEG_DECODER_VERSION = 'PUMP_SWAP_REVERSED_CASH_LEG_EVENT_V2'
SHADOW_DECODER_VERSIONS = (REVERSED_CASH_LEG_DECODER_VERSION,)
UNVALIDATED_DECODER_FLAG = 'DECODER_PATH_UNVALIDATED'
# Events of an unvalidated decoder path are journaled in their own table so
# the shadow comparison keeps them while the UI projection, its MAX_EVENTS
# truncation guard and every flow consumer read only the validated table.
SHADOW_EVENTS_TABLE = 'shadow_events'
# A body counts as decoded for seat shedding when its events carry no flag
# other than these FX-reference flags: a Jupiter SOL/USD outage leaves the
# swap itself exactly decoded, so it must not release every candidate's seat.
FX_REFERENCE_FLAGS = frozenset({'QUOTE_USD_UNKNOWN','QUOTE_ASSET_USD_REFERENCE_ESTIMATE'})
# In-memory decode-yield window used by seat shedding; never a journal query.
YIELD_WINDOW_MS = max(60_000,int(os.getenv('NEO_TAPE_YIELD_WINDOW_MS','7200000')))
_B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
NON_SWAP_DISCRIMINATORS = {hashlib.sha256(('global:'+name).encode()).digest()[:8]
                          for name in ('create_pool','deposit','withdraw','collect_coin_creator_fee','extend_account',
                                       'claim_token_incentives','sync_user_volume_accumulator','close_user_volume_accumulator')}
INFRA_PROGRAMS = {'11111111111111111111111111111111','TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
                  'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb','ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'}
SESSION = requests.Session()
SESSION.headers.update({'content-type':'application/json','user-agent':'NEO-LiveTape/3.0'})


def validated_decoder_versions():
    """The default path is always validated; shadow paths need explicit listing."""
    configured = {v.strip() for v in os.getenv('NEO_TAPE_VALIDATED_DECODER_VERSIONS','').split(',') if v.strip()}
    return frozenset({DECODER_VERSION}|configured)


VALIDATED_DECODER_VERSIONS = validated_decoder_versions()
STATUS = {'status':'starting','tracked_pairs':0,'updated_at':0,
          'source':'solana-mainnet-http-live','poll_seconds':POLL_SECONDS,'schema_version':3,
          'decoder':{'default_version':DECODER_VERSION,'shadow_versions':list(SHADOW_DECODER_VERSIONS),
                     'validated_versions':sorted(VALIDATED_DECODER_VERSIONS),
                     'unvalidated_event_flag':UNVALIDATED_DECODER_FLAG,
                     'unvalidated_flow_quality':'DEGRADED'}}
_RECORDER = None
_NEXT_REFERENCE_AT = 0


def scheduler_registry_path(out=OUT):
    """The scheduler's ticker registry sidecar next to the tape file (live_tape.ticker_registry.json)."""
    out = Path(out)
    return out.with_name(f'{out.stem}.ticker_registry.json')


# The scheduler's DEFENSIVE_ENTRY_LAYER_V1 keeps its ticker registry in that
# sidecar, so a tape restart keeps the ticker memory and its coverage; a registry
# without current coverage merges main's and the Lab's sidecars (read-only).
_POOL_SCHEDULER = TapePoolScheduler(
    registry_path=scheduler_registry_path(),
    registry_seed_paths=entry_defense.sibling_registry_paths(scheduler_registry_path()))


def flush_scheduler_registry():
    """Save the seat screen's ticker registry now (clean shutdown; never raises).

    The registry saves at most every 5 minutes while the tape runs; the service
    runner calls this on a clean stop so a restart loses no ticker sighting.
    """
    try:
        return bool(_POOL_SCHEDULER.defense.registry.flush())
    except Exception:
        return False


# Personal-engine reads run on the refresher thread, so they use their own
# session rather than sharing the poll's connection pool across threads.
PERSONAL_SESSION = requests.Session()
PERSONAL_SESSION.headers.update({'user-agent':'NEO-LiveTape/3.0'})


def personal_engine_state(port,timeout_seconds):
    """GET one personal PAPER engine's /state on the loopback interface only."""
    response = PERSONAL_SESSION.get(f'http://127.0.0.1:{int(port)}/state',timeout=timeout_seconds,
                                    allow_redirects=False)
    response.raise_for_status()
    return response.json()


# Refreshed by its own daemon thread (started in main()); the tape poll only
# reads the latest snapshot and never waits on a personal engine.
_PERSONAL_ENGINES = PersonalEnginePositions(lambda port,timeout: personal_engine_state(port,timeout))


def shared_quote_reference():
    """One shared background FX observation, never one API request per token."""
    global _NEXT_REFERENCE_AT
    reference=quote_transport.quote_asset_reference()
    current=now_ms()
    if current>=_NEXT_REFERENCE_AT and (not reference or current-reference['observed_at']>=45_000):
        quote_transport.quote(WSOL,USDC,1_000_000_000,purpose='background',slippage_bps=100)
        reference=quote_transport.quote_asset_reference()
        completed=now_ms()
        refreshed=bool(reference and 0<=completed-reference['observed_at']<45_000)
        # A transient missed refresh must get another chance before the old
        # 60-second reference expires. Calls still use the shared transport's
        # quota, exit priority and provider cooldown; authorization failures
        # retain the ordinary interval rather than repeatedly hitting auth.
        error=quote_transport.last_error().get('code')
        retry_ms=45_000 if refreshed or error in {'AUTHENTICATION_REQUIRED','ACCESS_DENIED'} else 5_000
        _NEXT_REFERENCE_AT=completed+retry_ms
    return reference


def now_ms():
    return int(time.time()*1000)


def _b58decode(value):
    number = 0
    for char in value:
        number = number*58+_B58.index(char)
    raw = number.to_bytes((number.bit_length()+7)//8,'big') if number else b''
    return b'\0'*(len(value)-len(value.lstrip('1')))+raw


def _b58encode(value):
    number,out = int.from_bytes(value,'big'),''
    while number:
        number,remainder = divmod(number,58)
        out = _B58[remainder]+out
    return '1'*(len(value)-len(value.lstrip(b'\0')))+out


def key_of(item):
    return str(item.get('pubkey') if isinstance(item,dict) else item)


def feed_snapshot():
    response = SESSION.get(API_URL,timeout=5)
    response.raise_for_status()
    state = response.json()
    rows,observed = {},now_ms()
    # Seat shedding reads the recorder's in-memory decode yield only; the
    # scheduler still never decides whether to trade.
    decode_yield = _RECORDER.decode_yield if _RECORDER is not None else None
    # Open positions of every personal PAPER engine in the account registry
    # are pinned like main's. This reads the refresher's latest in-memory
    # snapshot only (no HTTP), so a slow or dead engine never delays the poll.
    personal_positions, personal = _PERSONAL_ENGINES.latest(observed)
    coins, scheduling = _POOL_SCHEDULER.select(state, now=observed, max_tracked=MAX_TRACKED,
                                               decode_yield=decode_yield,
                                               personal_positions=personal_positions)
    # entry_scheduling reaches every user's /state; personal-engine aggregates
    # stay in the tape file's operator-only status.
    STATUS['personal_engines'] = {**personal,**scheduling.pop('personal_pins_operator',{})}
    STATUS['entry_scheduling'] = scheduling
    shared_reference=shared_quote_reference()
    for coin in coins:
        pair,mint = coin.get('pairAddress'),coin.get('address')
        if not pair or not mint or pair in rows:
            continue
        price,native = float(coin.get('priceUsd') or 0),float(coin.get('priceNative') or 0)
        rows[pair] = {'pair':pair,'address':mint,'symbol':coin.get('symbol') or '?','dexId':coin.get('dexId'),
                      'quote_usd_reference':shared_reference['usd_per_unit'] if shared_reference else (price/native if price>0 and native>0 else None),
                      'quote_reference_at':shared_reference['observed_at'] if shared_reference else observed,
                      'quote_reference_source':shared_reference['source'] if shared_reference else 'SCANNER_QUOTE_ASSET_REFERENCE_ESTIMATE'}
    return list(rows.values())


def align_rpc_answers(calls,data):
    """Missing/duplicate ids remain explicit retry errors, without shifting rows."""
    if not isinstance(data,list):
        raise RuntimeError('RPC batch response is not a list')
    indexed,duplicates = {},set()
    for answer in data:
        if not isinstance(answer,dict) or type(answer.get('id')) is not int:
            continue
        identifier = answer['id']
        if identifier in indexed:
            duplicates.add(identifier)
        indexed[identifier] = answer
    return [({'id':i,'error':{'code':'DUPLICATE_RPC_ID'}} if i in duplicates else
             indexed.get(i,{'id':i,'error':{'code':'MISSING_RPC_ID'}})) for i in range(1,len(calls)+1)]


def rpc_batch(calls):
    if not calls:
        return []
    # PublicNode accepts batched signature discovery, but rejects a batch of
    # getTransaction calls with -32600. Keep body retrieval bounded and
    # concurrent as individual JSON-RPC requests instead of retrying an
    # unsupported provider batch forever.
    if all(method == 'getTransaction' for method,_ in calls):
        def fetch_one(indexed_call):
            index,(method,params) = indexed_call
            payload = {'jsonrpc':'2.0','id':1,'method':method,'params':params}
            try:
                response = SESSION.post(RPC_URL,json=payload,timeout=15)
                response.raise_for_status()
                answer = response.json()
                return align_rpc_answers([calls[index]],
                    [answer] if isinstance(answer,dict) else answer)[0]
            except (requests.RequestException,RuntimeError,ValueError):
                # A failed body must not discard other successfully fetched
                # transactions in this bounded concurrent group.
                return {'id':1,'error':{'code':'TRANSACTION_RPC_UNAVAILABLE'}}
        with ThreadPoolExecutor(max_workers=min(RPC_TRANSACTION_CONCURRENCY,len(calls))) as pool:
            return list(pool.map(fetch_one,enumerate(calls)))
    if len(calls)>RPC_BATCH_SIZE:
        answers=[]
        for start in range(0,len(calls),RPC_BATCH_SIZE):
            answers.extend(rpc_batch(calls[start:start+RPC_BATCH_SIZE]))
        return answers
    payload = [{'jsonrpc':'2.0','id':i+1,'method':method,'params':params} for i,(method,params) in enumerate(calls)]
    response = SESSION.post(RPC_URL,json=payload,timeout=15)
    response.raise_for_status()
    return align_rpc_answers(calls,response.json())


def _instruction_records(tx):
    message = (tx.get('transaction') or {}).get('message') or {}
    keys = [key_of(k) for k in message.get('accountKeys') or []]
    loaded = (tx.get('meta') or {}).get('loadedAddresses') or {}
    if keys and not isinstance((message.get('accountKeys') or [None])[0],dict):
        keys += list(loaded.get('writable') or [])+list(loaded.get('readonly') or [])
    instructions = list(message.get('instructions') or [])
    for group in (tx.get('meta') or {}).get('innerInstructions') or []:
        instructions += list(group.get('instructions') or [])
    records = []
    for index,instruction in enumerate(instructions):
        try:
            program = instruction.get('programId') or keys[instruction['programIdIndex']]
            accounts = [keys[a] if type(a) is int else key_of(a) for a in instruction.get('accounts') or []]
            data = instruction.get('data') or ''
            records.append((index,program,accounts,_b58decode(data) if isinstance(data,str) else b''))
        except (KeyError,IndexError,ValueError,TypeError):
            continue
    return keys,records


def _token_account_closed_in_transaction(tx,account):
    """True only when a parsed spl-token closeAccount targets ``account``.

    A wrapped SOL/USDC account that is created and closed inside one
    transaction appears in neither preTokenBalances nor postTokenBalances.
    This is the only accepted explanation for a missing cash-leg balance entry.
    """
    message = (tx.get('transaction') or {}).get('message') or {}
    instructions = list(message.get('instructions') or [])
    for group in (tx.get('meta') or {}).get('innerInstructions') or []:
        instructions += list(group.get('instructions') or [])
    for instruction in instructions:
        if not isinstance(instruction,dict):
            continue
        parsed = instruction.get('parsed')
        if (isinstance(parsed,dict) and parsed.get('type')=='closeAccount'
                and instruction.get('program')=='spl-token'
                and (parsed.get('info') or {}).get('account')==account):
            return True
    return False


def classify_transaction(tx,metadata,*,observed_at=None,ingested_at=None):
    """Returns classification, every distinct swap, reason. No delta guessing."""
    observed = now_ms() if observed_at is None else int(observed_at)
    available = now_ms() if ingested_at is None else int(ingested_at)
    if observed<=0 or available<=0 or observed>available:
        return 'unclassified',[],'OBSERVATION_TIME_ORDER_INVALID'
    if not isinstance(tx,dict) or not isinstance(tx.get('meta'),dict):
        return 'retry',[],'TRANSACTION_NOT_AVAILABLE'
    meta = tx['meta']
    if meta.get('err') is not None:
        return 'failed',[],'ONCHAIN_TRANSACTION_FAILED'
    if not isinstance(tx.get('transaction'),dict) or not isinstance(tx['transaction'].get('message'),dict):
        return 'unclassified',[],'TRANSACTION_SCHEMA_MISMATCH'
    keys,instructions = _instruction_records(tx)
    message=tx['transaction']['message']
    signers={key_of(key) for key in message.get('accountKeys') or [] if isinstance(key,dict) and key.get('signer')}
    if not signers and type((message.get('header') or {}).get('numRequiredSignatures')) is int:
        signers=set(keys[:message['header']['numRequiredSignatures']])
    swaps,unknown = [],False
    for index,program,accounts,data in instructions:
        if metadata['pair'] not in accounts:
            continue
        if program==PUMP_AMM and data[:8] in SWAP_DISCRIMINATORS and len(accounts)>=9:
            # Pump's base/quote order is onchain identity, whereas market
            # providers can display either mint as the tracked token. A pool
            # with WSOL as base still trades its quote token; its token side is
            # the inverse of the instruction's base-token BUY/SELL side.
            tracked_base = accounts[3]==metadata['address']
            tracked_quote = (accounts[4]==metadata['address']
                             and accounts[3] in (USDC,WSOL))
            if (accounts[0]==metadata['pair'] and accounts[3]!=accounts[4]
                    and (tracked_base or tracked_quote)):
                instruction_name=('buy_exact_quote_in' if data[:8]==bytes([198,46,21,82,180,217,232,112])
                                  else 'buy' if SWAP_DISCRIMINATORS[data[:8]]=='BUY' else 'sell')
                swaps.append((index,SWAP_DISCRIMINATORS[data[:8]],accounts,instruction_name))
            else:
                unknown = True
        elif (program==PUMP_AMM and data[:8] not in NON_SWAP_DISCRIMINATORS) or (program!=PUMP_AMM and program not in INFRA_PROGRAMS):
            unknown = True
    if not swaps:
        if unknown and proves_no_pool_swap(tx,metadata,available_at=available):
            return 'non_swap',[],'VERIFIED_FOREIGN_POOL_REFERENCE_NO_SWAP'
        return ('unclassified',[],'UNSUPPORTED_POOL_INSTRUCTION') if unknown else ('non_swap',[],'NO_SUPPORTED_SWAP')
    decimals = {}
    for balance in (meta.get('preTokenBalances') or [])+(meta.get('postTokenBalances') or []):
        try:
            account = keys[int(balance['accountIndex'])]
            entry = (balance['mint'],int(balance['uiTokenAmount']['decimals']))
            if account in decimals and decimals[account]!=entry:
                return 'unclassified',[],'TOKEN_METADATA_INCONSISTENT'
            decimals[account] = entry
        except (IndexError,KeyError,ValueError,TypeError):
            return 'unclassified',[],'TOKEN_METADATA_MISSING'
    stack,decoded,event_payloads = [],[],[]
    # Anchor emit_cpi events are encoded as inner self-instructions; emit!
    # events use Program data logs. Prefer CPI when supplied, to avoid counting
    # a dual representation twice while retaining repeated real swaps.
    cpi = [(index,data[8:]) for index,program,_,data in instructions
           if program==PUMP_AMM and data[:8]==ANCHOR_EVENT_CPI and data[8:16] in EVENT_DISCRIMINATORS]
    for log_index,line in enumerate(meta.get('logMessages') or []):
        invoke = re.match(r'^Program (\w+) invoke \[(\d+)\]$',line)
        if invoke:
            stack = stack[:int(invoke.group(2))-1]+[invoke.group(1)]
            continue
        if re.match(r'^Program \w+ (success|failed)',line):
            if stack:
                stack.pop()
            continue
        if not line.startswith('Program data: ') or not stack or stack[-1]!=PUMP_AMM:
            continue
        try:
            payload = base64.b64decode(line[14:],validate=True)
        except ValueError:
            continue
        event_payloads.append((log_index,payload))
    if cpi:
        event_payloads = [(1_000_000+index,payload) for index,payload in cpi]
    matched_swap_indexes = set()
    for log_index,payload in event_payloads:
        direction = EVENT_DISCRIMINATORS.get(payload[:8])
        if not direction or len(payload)<248:
            continue
        pool,wallet,base_account,quote_account = [_b58encode(payload[a:a+32]) for a in (120,152,184,216)]
        matches = [x for x in swaps if x[0] not in matched_swap_indexes
                   and x[1]==direction and x[2][0]==pool and x[2][1]==wallet
                   and x[2][5]==base_account and x[2][6]==quote_account]
        if pool!=metadata['pair']:
            continue
        if not matches:
            return 'unclassified',decoded,'SWAP_EVENT_COVERAGE_INCOMPLETE'
        accounts = matches[0][2]
        base_mint,quote_mint = accounts[3],accounts[4]
        base_info,quote_info = decimals.get(base_account),decimals.get(quote_account)
        reversed_pool = quote_mint==metadata['address']
        token_mint,token_info = (quote_mint,quote_info) if reversed_pool else (base_mint,base_info)
        cash_mint,cash_info = (base_mint,base_info) if reversed_pool else (quote_mint,quote_info)
        if not token_info or token_info[0]!=token_mint:
            reason = 'TRACKED_QUOTE_DECIMALS_OR_MINT_MISSING' if reversed_pool else 'BASE_DECIMALS_OR_MINT_MISSING'
            return 'unclassified',[],reason
        if cash_mint not in (USDC,WSOL):
            return 'unclassified',[],'UNSUPPORTED_QUOTE_ASSET'
        quote_decimals = 6 if cash_mint==USDC else 9
        decoder_version = DECODER_VERSION
        if reversed_pool and cash_info is None:
            # Real bodies (2026-10-08, TWEETCRAFT/SHITCOIN pools) show the
            # user's wrapped SOL base account created and closed inside the
            # swap transaction, so it has no pre/post balance entry. The cash
            # mint is the instruction's base mint, already restricted to
            # USDC/WSOL, and the event's base leg equals the pool vault delta;
            # the same wrapped-account pattern is tolerated on the quote side
            # of TRACKED_BASE pools. No scanner price or stable-asset
            # assumption is used. This path carries its own decoder version
            # and stays an unvalidated shadow path until listed as validated.
            if not _token_account_closed_in_transaction(tx,base_account):
                return 'unclassified',[],'BASE_DECIMALS_OR_MINT_MISSING'
            decoder_version = REVERSED_CASH_LEG_DECODER_VERSION
        elif reversed_pool and cash_info!=(cash_mint,quote_decimals):
            return 'unclassified',[],'BASE_DECIMALS_OR_MINT_MISSING'
        if cash_info and cash_info!=(cash_mint,quote_decimals):
            return 'unclassified',[],'QUOTE_DECIMALS_OR_MINT_MISMATCH'
        event_time = int.from_bytes(payload[8:16],'little',signed=True)*1000
        if not tx.get('blockTime') or not 0<event_time<=available or abs(event_time-int(tx['blockTime'])*1000)>2000:
            return 'unclassified',[],'FUTURE_OR_INCONSISTENT_EVENT_TIME'
        base_raw,quote_raw = int.from_bytes(payload[16:24],'little'),int.from_bytes(payload[112:120],'little')
        if base_raw<=0 or quote_raw<=0 or not 0<=token_info[1]<=18:
            return 'unclassified',[],'INVALID_EVENT_AMOUNT'
        token_raw,cash_raw = (quote_raw,base_raw) if reversed_pool else (base_raw,quote_raw)
        token_direction = ('SELL' if direction=='BUY' else 'BUY') if reversed_pool else direction
        quote_amount = cash_raw/10**quote_decimals
        usd,flags,valuation = None,[],'UNKNOWN'
        if wallet not in signers:
            flags.append('SWAP_ACTOR_NOT_TRANSACTION_SIGNER')
        if cash_mint==USDC:
            usd,valuation = quote_amount,'ACTUAL_USDC_QUOTE_LEG'
        else:
            reference,reference_time = metadata.get('quote_usd_reference'),int(metadata.get('quote_reference_at') or 0)
            if reference and math.isfinite(float(reference)) and float(reference)>0 and 0<=available-reference_time<=60_000 and abs(event_time-reference_time)<=60_000:
                usd = quote_amount*float(reference)
                valuation = metadata.get('quote_reference_source') or 'QUOTE_ASSET_REFERENCE_ESTIMATE'
                if valuation!='JUPITER_CONVERSION_QUOTE_REFERENCE':
                    flags.append('QUOTE_ASSET_USD_REFERENCE_ESTIMATE')
            else:
                flags.append('QUOTE_USD_UNKNOWN')
        decoder_validated = decoder_version in VALIDATED_DECODER_VERSIONS
        if not decoder_validated:
            flags.append(UNVALIDATED_DECODER_FLAG)
        # One event proves one instruction. Duplicate events cannot compensate
        # for a different unmatched BUY/SELL in the same transaction.
        matched_swap_indexes.add(matches[0][0])
        decoded.append({'ts':event_time,'event_time':event_time,'observed_at':observed,'ingested_at':available,'available_at':available,
                        'event_index':log_index,'direction':token_direction,'wallet':wallet,'address':token_mint,'pairAddress':pool,
                        'symbol':metadata.get('symbol','?'),'token_raw_amount':str(token_raw),'token_decimals':token_info[1],
                        'token_amount':token_raw/10**token_info[1],'quote_asset':cash_mint,'quote_raw_amount':str(cash_raw),
                        'quote_decimals':quote_decimals,'quote_amount':quote_amount,'usd_amount':round(usd,8) if usd is not None else None,
                        'usd_valuation_source':valuation,'quality_flags':flags,'program_id':PUMP_AMM,'slot':tx.get('slot'),
                        'usd_valuation_estimated':cash_mint!=USDC,'quote_reference_at':metadata.get('quote_reference_at') if cash_mint!=USDC else None,
                        'pool_orientation':'TRACKED_QUOTE' if reversed_pool else 'TRACKED_BASE',
                        'onchain_direction':direction,'onchain_base_mint':base_mint,'onchain_quote_mint':quote_mint,
                        'onchain_base_raw_amount':str(base_raw),'onchain_quote_raw_amount':str(quote_raw),
                        'decoder_version':decoder_version,'decoder_validated':decoder_validated,
                        'cash_leg_balance_metadata':'PRESENT' if cash_info else 'WRAPPED_ACCOUNT_CLOSED_IN_TRANSACTION' if reversed_pool else 'ABSENT',
                        'provider':metadata.get('provider','solana-rpc'),'note':token_direction,'confirmed_swap':True})
        fee_evidence=decode_fee_evidence(payload,matches[0][3])
        if fee_evidence is not None:
            # Preserve complete gross/net fee facts for later evaluation.
            # No estimated execution rate or entry/exit limit changes here.
            decoded[-1]['fee_evidence']={**fee_evidence,
                'address':token_mint,'pairAddress':pool,'program_id':PUMP_AMM,
                'event_time':event_time,'observed_at':observed,'available_at':available,
                'slot':tx.get('slot'),'token_direction':token_direction}
    if len(decoded)!=len(swaps):
        return 'unclassified',decoded,'SWAP_EVENT_COVERAGE_INCOMPLETE'
    flags = {flag for event in decoded for flag in event['quality_flags']}
    if flags:
        # Preserve the coverage failure and the raw event, while naming the
        # actual defect. Missing signer evidence is not a missing FX quote.
        # A shadow decoder path alone is named as such; any other defect keeps
        # its existing name so the default path's reasons are unchanged.
        reason = ('SWAP_ACTOR_NOT_TRANSACTION_SIGNER' if 'SWAP_ACTOR_NOT_TRANSACTION_SIGNER' in flags
                  else UNVALIDATED_DECODER_FLAG if flags=={UNVALIDATED_DECODER_FLAG}
                  else 'QUOTE_USD_UNKNOWN_OR_ESTIMATED_WITHOUT_ROUTE')
        return 'unclassified',decoded,reason
    for event in decoded:
        if event.get('fee_evidence') is not None:
            # The existing swap decoder can retain recognized swaps alongside
            # unknown pool instructions. Such a transaction does not prove
            # complete fee provenance, even when its known swaps reconcile.
            event['fee_evidence']['provenance_authenticated']=(not unknown
                and type(tx.get('slot')) is int and tx['slot']>0)
            event['fee_evidence']['full_transaction_swap_coverage']=not unknown
    return 'processed',decoded,None


def parse_trade(tx,metadata):
    _,events,_ = classify_transaction(tx,metadata)
    return events[0] if len(events)==1 else None


def _is_shadow_event(event):
    return UNVALIDATED_DECODER_FLAG in (event.get('quality_flags') or ())


def _pair_filter(pairs):
    """SQL fragment restricting the pending queue to the current selection."""
    if pairs is None:
        return '',()
    pairs = tuple(dict.fromkeys(pairs))
    if not pairs:
        return 'AND 0 ',()
    return 'AND pair IN ('+','.join('?'*len(pairs))+') ',pairs


class TapeRecorder:
    def __init__(self,path,*,clock=now_ms,page_size=PAGE_SIZE,page_budget=PAGE_BUDGET,tx_budget=TX_BUDGET,
                 historical_tx_budget=HISTORICAL_TX_BUDGET,yield_window_ms=YIELD_WINDOW_MS):
        self.path,self.clock = Path(path),clock
        self.page_size,self.page_budget,self.tx_budget = page_size,page_budget,tx_budget
        self.historical_tx_budget = max(0,int(historical_tx_budget))
        # Per pool, one in-memory row per poll: (classified_at, bodies, decoded
        # swaps, engine-usable swaps, shadow-path swaps). Restart starts from zero,
        # so shedding is conservative and never touches the durable journal.
        self.yield_window_ms = max(60_000,int(yield_window_ms))
        self._yield = {}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS pairs(pair TEXT PRIMARY KEY,mint TEXT NOT NULL,cursor TEXT,
                before_sig TEXT,scan_head TEXT,complete_since INTEGER,last_poll INTEGER NOT NULL DEFAULT 0,reason TEXT,metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS signatures(signature TEXT NOT NULL,pair TEXT NOT NULL,slot INTEGER,event_time INTEGER,
                observed INTEGER NOT NULL,metadata TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,next_retry INTEGER NOT NULL DEFAULT 0,reason TEXT,PRIMARY KEY(signature,pair));
            CREATE INDEX IF NOT EXISTS pending_idx ON signatures(state,next_retry,observed);
            CREATE INDEX IF NOT EXISTS signature_pair_window_idx
                ON signatures(pair,event_time,observed,state);
            CREATE INDEX IF NOT EXISTS pending_fresh_idx
                ON signatures(COALESCE(event_time,observed) DESC,observed DESC,slot DESC,next_retry)
                WHERE state='pending';
            CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,signature TEXT NOT NULL,pair TEXT NOT NULL,
                event_time INTEGER NOT NULL,available INTEGER NOT NULL,payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS event_time_idx ON events(event_time);
            CREATE INDEX IF NOT EXISTS event_pair_time_idx ON events(pair,event_time);
            CREATE TABLE IF NOT EXISTS shadow_events(event_id TEXT PRIMARY KEY,signature TEXT NOT NULL,pair TEXT NOT NULL,
                event_time INTEGER NOT NULL,available INTEGER NOT NULL,payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS shadow_event_time_idx ON shadow_events(event_time);
            CREATE INDEX IF NOT EXISTS shadow_event_pair_time_idx ON shadow_events(pair,event_time);
        ''')
        self.db.commit()

    def close(self):
        self.db.close()

    def _record_yield(self,classified_at,counts):
        for pair,(bodies,decoded,usable,shadow) in counts.items():
            if bodies:
                self._yield.setdefault(pair,[]).append((int(classified_at),bodies,decoded,usable,shadow))

    def decode_yield(self,pair,since=0):
        """Bodies classified for one pool and the swaps they yielded.

        ``decoded_swaps`` counts events whose only quality flags, if any, are
        FX-reference flags (the swap legs are exact; only the SOL/USD value
        is missing or estimated). ``usable_swaps`` counts events without
        quality flags, i.e. the only events the engine and the Lab accept as
        flow. ``shadow_swaps`` counts events recorded by an unvalidated
        decoder path; they are never decoded or usable. Only rows at or after
        ``since`` (and within the in-memory window) are counted.
        """
        current = self.clock()
        floor = max(int(since or 0),current-self.yield_window_ms)
        rows = [row for row in self._yield.get(pair,[]) if row[0]>=current-self.yield_window_ms]
        if rows:
            self._yield[pair] = rows
        else:
            self._yield.pop(pair,None)
        rows = [row for row in rows if row[0]>=floor]
        return {'bodies':sum(r[1] for r in rows),'decoded_swaps':sum(r[2] for r in rows),
                'usable_swaps':sum(r[3] for r in rows),'shadow_swaps':sum(r[4] for r in rows),
                'first_body_at':rows[0][0] if rows else None,'last_body_at':rows[-1][0] if rows else None,
                'window_ms':self.yield_window_ms,'since':floor}

    def discover(self,feed,rpc):
        active = []
        for metadata in feed:
            metadata = dict(metadata,provider='solana-rpc:'+str(urlsplit(RPC_URL).hostname or 'unknown'))
            pair,mint = metadata['pair'],metadata['address']
            existing = self.db.execute('SELECT * FROM pairs WHERE pair=?',(pair,)).fetchone()
            if existing and existing['mint']!=mint:
                with self.db:
                    self.db.execute('UPDATE pairs SET reason=? WHERE pair=?',('POOL_MINT_CHANGED',pair))
                continue
            if not existing:
                with self.db:
                    self.db.execute('INSERT INTO pairs(pair,mint,metadata) VALUES(?,?,?)',(pair,mint,json.dumps(metadata)))
            active.append(metadata)
        # Reserve only work actually due in the same fresh window that process()
        # services. A fixed half-budget left unused body capacity even when
        # there were no retries. Every pool still gets at least one signature
        # per round, including pinned pools; process() retains its exact cap.
        # Only the current selection's bodies are due: poll() restricts
        # process() to the same pools, so de-selected or shed pools' pending
        # signatures must not shrink the discovery budget either.
        observed = self.clock()
        selection,selection_params = _pair_filter(metadata['pair'] for metadata in feed)
        due = self.db.execute('''SELECT count(*) n FROM (
            SELECT signature FROM signatures INDEXED BY pending_fresh_idx
            WHERE state='pending' AND next_retry<=?
                AND COALESCE(event_time,observed)>=? '''+selection+'''
            ORDER BY COALESCE(event_time,observed) DESC,observed DESC,slot DESC
            LIMIT ?)''', (observed,observed-WINDOW_MS,*selection_params,self.tx_budget)).fetchone()['n']
        discovery_remaining = max(len(active)*self.page_budget,self.tx_budget-due)
        # One shared page batch per round, rather than one HTTP call per pool.
        # Unused capacity from short pages can serve remaining pools next round.
        for round_index in range(self.page_budget):
            if not active:
                break
            page_limit = max(1,min(self.page_size,
                                  discovery_remaining // (len(active)*(self.page_budget-round_index))))
            calls,states = [],[]
            for metadata in active:
                state = self.db.execute('SELECT * FROM pairs WHERE pair=?',(metadata['pair'],)).fetchone()
                observed = self.clock()
                # A pool may leave the small discovery set for minutes or hours.
                # Replaying every signature since its old cursor makes current
                # order flow look incomplete indefinitely. Start a new causal
                # window after an actual monitoring gap, then require the full
                # decision window to be collected before entries can use it.
                scan_head_time = None
                if state['scan_head']:
                    scan_row = self.db.execute(
                        'SELECT event_time FROM signatures WHERE signature=? AND pair=?',
                        (state['scan_head'], metadata['pair'])).fetchone()
                    if scan_row and scan_row['event_time']:
                        scan_head_time = int(scan_row['event_time'])
                inactive_gap = bool(state['last_poll'] and
                                    observed - int(state['last_poll']) > DECISION_FLOW_WINDOW_MS)
                stalled_scan = bool(state['before_sig'] and state['scan_head'] and
                                    (scan_head_time is None or
                                     observed - scan_head_time > MAX_DISCOVERY_LAG_MS))
                failed_scan = state['reason'] in {
                    'SIGNATURE_RPC_UNAVAILABLE', 'SIGNATURE_SCHEMA_MISMATCH',
                    'PROVIDER_PAGINATION_STALLED'}
                if inactive_gap or stalled_scan or failed_scan:
                    with self.db:
                        self.db.execute('''UPDATE pairs SET cursor=NULL,before_sig=NULL,
                            scan_head=NULL,complete_since=NULL,reason=NULL WHERE pair=?''',
                            (metadata['pair'],))
                    state = self.db.execute('SELECT * FROM pairs WHERE pair=?',
                                            (metadata['pair'],)).fetchone()
                options = {'limit':page_limit,'commitment':'confirmed'}
                if state['cursor']:
                    options['until'] = state['cursor']
                if state['before_sig']:
                    options['before'] = state['before_sig']
                calls.append(('getSignaturesForAddress',[metadata['pair'],options]))
                states.append(state)
            answers = rpc(calls)
            next_active = []
            for index,(metadata,state) in enumerate(zip(active,states)):
                pair = metadata['pair']
                answer = answers[index] if index<len(answers) else {'error':{'code':'MISSING_RPC_ID'}}
                if answer.get('error') or not isinstance(answer.get('result'),list):
                    with self.db:
                        self.db.execute('UPDATE pairs SET reason=?,last_poll=? WHERE pair=?',('SIGNATURE_RPC_UNAVAILABLE',self.clock(),pair))
                    continue
                rows = answer['result']
                if len(rows)>page_limit or any(not isinstance(row,dict) or not isinstance(row.get('signature'),str) or not row['signature']
                       or type(row.get('slot')) is not int or (row.get('blockTime') is not None and type(row['blockTime']) is not int)
                       for row in rows):
                    with self.db:
                        self.db.execute('UPDATE pairs SET reason=?,last_poll=? WHERE pair=?',('SIGNATURE_SCHEMA_MISMATCH',self.clock(),pair))
                    continue
                discovery_remaining = max(0,discovery_remaining-len(rows))
                observed,cutoff = self.clock(),self.clock()-DECISION_FLOW_WINDOW_MS
                head = state['scan_head'] or (rows[0].get('signature') if rows else state['cursor'])
                bootstrap = state['cursor'] is None
                reached_time = bootstrap and any(r.get('blockTime') and int(r['blockTime'])*1000<cutoff for r in rows)
                complete = len(rows)<page_limit or reached_time or any(r.get('signature')==state['cursor'] for r in rows)
                before = rows[-1].get('signature') if rows else state['before_sig']
                stalled = not complete and before==state['before_sig']
                with self.db:
                    for row in rows:
                        signature = row.get('signature')
                        if not signature or (bootstrap and row.get('blockTime') and int(row['blockTime'])*1000<cutoff):
                            continue
                        self.db.execute('''INSERT OR IGNORE INTO signatures(signature,pair,slot,event_time,observed,metadata,state,reason)
                            VALUES(?,?,?,?,?,?,?,?)''',(signature,pair,row.get('slot'),int(row['blockTime'])*1000 if row.get('blockTime') else None,
                            observed,json.dumps(metadata),'failed' if row.get('err') else 'pending','ONCHAIN_TRANSACTION_FAILED' if row.get('err') else None))
                    oldest = state['complete_since'] if state['complete_since'] is not None else cutoff
                    self.db.execute('''UPDATE pairs SET cursor=?,before_sig=?,scan_head=?,complete_since=?,last_poll=?,reason=?,metadata=? WHERE pair=?''',
                        (head if complete else state['cursor'],None if complete else before,None if complete else head,
                         oldest if complete else state['complete_since'],observed,
                         None if complete else ('PROVIDER_PAGINATION_STALLED' if stalled else 'PAGINATION_PENDING'),json.dumps(metadata),pair))
                if not complete and not stalled:
                    next_active.append(metadata)
            active = next_active

    def process(self,rpc,pairs=None):
        # Prioritize on-chain time: an old pagination row discovered just now
        # must not displace an already observed live transaction. Large legacy
        # retry queues remain durable, but get only a small maintenance budget
        # instead of filling every unused live request slot on each poll.
        # ``pairs`` is the current selection: fresh bodies of pools that were
        # shed or de-selected stay pending and do not spend the live budget.
        current = self.clock()
        cutoff = current-WINDOW_MS
        order = 'ORDER BY COALESCE(event_time,observed) DESC,observed DESC,slot DESC LIMIT ?'
        selection,selection_params = _pair_filter(pairs)
        pending = list(self.db.execute(
            "SELECT * FROM signatures INDEXED BY pending_fresh_idx WHERE state='pending' AND next_retry<=? "
            'AND COALESCE(event_time,observed)>=? '+selection+order,
            (current,cutoff,*selection_params,self.tx_budget)))
        historical_budget = min(self.historical_tx_budget,max(0,self.tx_budget-len(pending)))
        if historical_budget:
            pending.extend(self.db.execute(
                "SELECT * FROM signatures INDEXED BY pending_fresh_idx WHERE state='pending' AND next_retry<=? "
                'AND COALESCE(event_time,observed)<? '+order,
                (current,cutoff,historical_budget)))
        groups = {}
        for row in pending:
            groups.setdefault(row['signature'],[]).append(row)
        calls = [('getTransaction',[sig,{'encoding':'jsonParsed','commitment':'confirmed','maxSupportedTransactionVersion':1}]) for sig in groups]
        if not calls:
            return
        try:
            answers = rpc(calls)
        except (requests.RequestException,RuntimeError,ValueError):
            # Discovery has already committed. Preserve every pending item and
            # back off the bodies rather than re-requesting them every second.
            answers = [{'error':{'code':'TRANSACTION_RPC_UNAVAILABLE'}} for _ in calls]
        bodies_received_at = self.clock()
        # A current owner observation can help only complete successful foreign
        # references with no Pump execution. Keep it ephemeral: no saved metadata
        # claim is trusted and no terminal historical classification is rewritten.
        classification_metadata, owner_candidates, owner_candidate_keys = {},[],set()
        for signature,answer in zip(groups,answers):
            tx = answer.get('result') if not answer.get('error') else None
            for row in groups[signature]:
                try:
                    metadata = json.loads(row['metadata'])
                    metadata.pop('pool_owner_proof',None)
                    classification_metadata[(signature,row['pair'])] = metadata
                    event_time = row['event_time'] if row['event_time'] is not None else row['observed']
                    if (tx is not None and event_time>=cutoff
                            and foreign_reference_candidate(tx,metadata,INFRA_PROGRAMS)):
                        owner_candidates.append((tx,metadata))
                        owner_candidate_keys.add((signature,row['pair']))
                except (ValueError,TypeError,AttributeError):
                    pass
        owner_proofs = collect_pool_owner_proofs(owner_candidates,rpc,clock=self.clock,
            tx_budget=self.tx_budget,bodies_received_at=bodies_received_at,infra_programs=INFRA_PROGRAMS)
        yield_counts = {}
        for signature,answer in zip(groups,answers):
            tx = answer.get('result') if not answer.get('error') else None
            for row in groups[signature]:
                if tx is None:
                    classification,events,reason = 'retry',[],str((answer.get('error') or {}).get('code') or 'TRANSACTION_NULL')
                else:
                    try:
                        metadata = classification_metadata[(signature,row['pair'])]
                        proof = (owner_proofs.get((metadata['pair'],metadata['address']))
                                 if (signature,row['pair']) in owner_candidate_keys else None)
                        if proof:
                            metadata = dict(metadata,pool_owner_proof=proof)
                        classification,events,reason = classify_transaction(tx,metadata,observed_at=row['observed'],ingested_at=self.clock())
                    except (ValueError,TypeError,KeyError,IndexError,AttributeError,OverflowError):
                        classification,events,reason = 'unclassified',[],'TRANSACTION_SCHEMA_MISMATCH'
                if classification!='retry':
                    bodies,decoded,usable,shadow = yield_counts.get(row['pair'],(0,0,0,0))
                    yield_counts[row['pair']] = (
                        bodies+1,
                        decoded+sum(1 for event in events if set(event.get('quality_flags') or ())<=FX_REFERENCE_FLAGS),
                        usable+sum(1 for event in events if not event.get('quality_flags')),
                        shadow+sum(1 for event in events if _is_shadow_event(event)))
                with self.db:
                    for event in events:
                        event.update(signature=signature,slot=event.get('slot') or row['slot'])
                        event_id = f"{signature}:{row['pair']}:{event['event_index']}"
                        event['event_id'] = event_id
                        # Shadow-path events never enter the projection table.
                        table = SHADOW_EVENTS_TABLE if _is_shadow_event(event) else 'events'
                        self.db.execute(f'INSERT OR IGNORE INTO {table} VALUES(?,?,?,?,?,?)',
                            (event_id,signature,row['pair'],event['event_time'],event['available_at'],json.dumps(event,allow_nan=False)))
                    attempts = row['attempts']+1
                    delay = min(60_000,1000*2**min(attempts-1,6))
                    self.db.execute('UPDATE signatures SET state=?,attempts=?,next_retry=?,reason=? WHERE signature=? AND pair=?',
                        ('pending' if classification=='retry' else classification,attempts,self.clock()+delay if classification=='retry' else 0,reason,signature,row['pair']))
        self._record_yield(self.clock(),yield_counts)

    def snapshot(self,feed):
        current,cutoff = self.clock(),self.clock()-WINDOW_MS
        coverage = {}
        for metadata in feed:
            pair = metadata['pair']
            state = self.db.execute('SELECT * FROM pairs WHERE pair=?',(pair,)).fetchone()
            if not state:
                continue
            # Unknown event times only affect decisions for the five-minute
            # window in which we actually observed the signature.
            # Entry gates consume a 30-second flow window. Older unresolved
            # signatures remain visible in the durable tape and 5-minute chart,
            # but must not poison completeness for the current decision window.
            decision_cutoff = current - DECISION_FLOW_WINDOW_MS
            # These disjoint ranges use the covering pool/time index. A single
            # OR predicate made SQLite scan the durable history of every pool
            # on each poll; an unknown timestamp still uses observation time.
            recent_rows = '''SELECT state,observed FROM signatures
                WHERE pair=? AND event_time>=?
                UNION ALL SELECT state,observed FROM signatures
                WHERE pair=? AND event_time IS NULL AND observed>=?'''
            recent_params = (pair,decision_cutoff,pair,decision_cutoff)
            counters = {r['state']:r['n'] for r in self.db.execute(
                f'SELECT state,count(*) n FROM ({recent_rows}) GROUP BY state',recent_params)}
            pending = self.db.execute(
                f"SELECT count(*) n,min(observed) oldest FROM ({recent_rows}) WHERE state='pending'",
                recent_params).fetchone()
            latest = self.db.execute('SELECT max(event_time) t FROM events WHERE pair=?',(pair,)).fetchone()['t']
            reason = state['reason']
            if pending['n']:
                reason = reason or 'TRANSACTION_BACKLOG'
            if counters.get('unclassified',0):
                reason = reason or 'UNCLASSIFIED_TRANSACTIONS'
            if current-state['last_poll']>max(10_000,int(POLL_SECONDS*5000)):
                reason = reason or 'SIGNATURE_POLL_STALE'
            status = 'COMPLETE' if state['complete_since'] is not None and not reason else ('UNKNOWN' if state['complete_since'] is None else 'DEGRADED')
            coverage[pair] = {'address':state['mint'],'pairAddress':pair,'status':status,'complete_since_ms':state['complete_since'],
                              'last_poll_at':state['last_poll'],'backlog':pending['n'],'oldest_pending_at':pending['oldest'],
                              'pagination_pending':bool(state['before_sig']),'unclassified':counters.get('unclassified',0),
                              'max_lag_ms':max(0,current-latest) if latest else None,'reason':reason}
        events = [json.loads(r['payload']) for r in self.db.execute('SELECT payload FROM events WHERE event_time>=? ORDER BY event_time DESC,available DESC LIMIT ?',(cutoff,MAX_EVENTS))]
        total_window = self.db.execute('SELECT count(*) n FROM events WHERE event_time>=?',(cutoff,)).fetchone()['n']
        truncated = total_window>len(events)
        if truncated:
            for record in coverage.values():
                record.update(status='DEGRADED',reason='UI_WINDOW_TRUNCATED')
        counts = {r['state']:r['n'] for r in self.db.execute('SELECT state,count(*) n FROM signatures GROUP BY state')}
        current_backlog = sum(record['backlog'] for record in coverage.values())
        total_backlog = counts.get('pending',0)
        return {**STATUS,'status':'online' if coverage and all(r['status']=='COMPLETE' for r in coverage.values()) else 'degraded',
                'updated_at':current,'tracked_pairs':len(feed),'events':events,'pair_coverage':coverage,
                'coverage':sum(r['status']=='COMPLETE' for r in coverage.values())/max(1,len(feed)),
                'backlog':total_backlog,'current_backlog':current_backlog,
                'stale_pending':max(0,total_backlog-current_backlog),
                'classifications':counts,'events_total':self.db.execute('SELECT count(*) n FROM events').fetchone()['n'],
                # Journaled for the shadow comparison only; never projected and
                # never counted against MAX_EVENTS.
                'shadow_events_window':self.db.execute(
                    f'SELECT count(*) n FROM {SHADOW_EVENTS_TABLE} WHERE event_time>=?',(cutoff,)).fetchone()['n'],
                'window_ms':WINDOW_MS,'projection_truncated':truncated,
                'lag_ms':max((max(0,current-r['oldest_pending_at']) for r in coverage.values() if r['oldest_pending_at']),default=0)}

    def poll(self,feed,rpc=rpc_batch):
        self.discover(feed,rpc)
        self.process(rpc,pairs=[metadata['pair'] for metadata in feed])
        return self.snapshot(feed)


def atomic_write(payload):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    temporary = OUT.with_name(OUT.name+f'.{os.getpid()}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('w',encoding='utf-8') as handle:
            json.dump(payload,handle,ensure_ascii=False,allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        # External readers such as antivirus/indexers may still refuse deletion.
        # Preserve atomicity, bound retry time and report persistent failures.
        for attempt in range(ATOMIC_REPLACE_ATTEMPTS):
            try:
                replace_shared_snapshot(temporary,OUT)
                return
            except PermissionError:
                if attempt + 1 >= ATOMIC_REPLACE_ATTEMPTS:
                    raise
                time.sleep(min(.4, .025 * (2 ** attempt)))
    finally:
        # A failed projection must not leave partial or rejected temp files.
        temporary.unlink(missing_ok=True)


def poll_once():
    global _RECORDER
    if _RECORDER is None:
        _RECORDER = TapeRecorder(Path(os.getenv('NEO_TAPE_DB_PATH',str(OUT.with_suffix('.sqlite3')))))
    payload = _RECORDER.poll(feed_snapshot())
    atomic_write(payload)
    return payload


def failure_summary(exc):
    """Name the failing file/error code without paths, messages or credentials."""
    details=[]
    filename=getattr(exc,'filename2',None) or getattr(exc,'filename',None)
    if isinstance(filename,str) and filename:
        basename=filename.replace('\\','/').rsplit('/',1)[-1]
        basename=re.sub(r'[^A-Za-z0-9._-]','_',basename)[:120]
        if basename:details.append(f'file={basename}')
    winerror=getattr(exc,'winerror',None)
    if type(winerror) is int:details.append(f'winerror={winerror}')
    return type(exc).__name__+(f" ({', '.join(details)})" if details else '')


def main():
    failures=0
    _PERSONAL_ENGINES.start()
    while True:
        started = time.monotonic()
        try:
            poll_once()
            failures=0
        except Exception as exc:
            failures+=1
            print(f'Live tape poll failed: {failure_summary(exc)}', file=sys.stderr, flush=True)
            old = {}
            try:
                saved = json.loads(read_shared_text(OUT,encoding='utf-8'))
                if isinstance(saved, dict):
                    old = saved
            except (OSError,ValueError):
                pass
            old={**STATUS,**old}
            old.update(status='degraded',error=type(exc).__name__,updated_at=now_ms())
            coverage = old.get('pair_coverage', {})
            if isinstance(coverage, dict):
                for record in coverage.values():
                    if isinstance(record, dict):
                        record.update(status='DEGRADED',reason='RECORDER_UNAVAILABLE')
            try:
                atomic_write(old)
            except (OSError, ValueError, TypeError) as projection_error:
                # A locked/full disk can reject both the normal projection and
                # its degraded replacement. Keep the durable recorder alive;
                # consumers already reject stale confirmed-flow evidence.
                print(f'Live tape failure projection unavailable: {failure_summary(projection_error)}',
                      file=sys.stderr, flush=True)
        delay=min(60,2**min(failures,6)) if failures else POLL_SECONDS
        time.sleep(max(.25,delay-(time.monotonic()-started)))


if __name__=='__main__':
    main()
