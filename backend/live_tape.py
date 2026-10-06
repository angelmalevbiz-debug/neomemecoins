#!/usr/bin/env python3
"""Durable, read-only Solana recorder; imports never touch account files.

The SQLite journal is authoritative. The bounded JSON projection is for UI.
The strict decoder currently supports official PumpSwap Buy/Sell instructions
and events. Unknown programs/ambiguous bodies degrade coverage, never imply
that there were no sellers. A signature is terminal only after classification.
"""
import base64
import concurrent.futures
import json
import hashlib
import math
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit
import requests
import honest_quote_transport as quote_transport

API_URL = os.getenv('NEO_LOCAL_API', 'http://127.0.0.1:8788/state')
RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://solana-rpc.publicnode.com')
OUT = Path(os.getenv('NEO_LIVE_TAPE_PATH', '/var/lib/neo-market/live_tape.json'))
MAX_TRACKED = int(os.getenv('NEO_TAPE_MAX_PAIRS', '12'))
MAX_EVENTS = int(os.getenv('NEO_TAPE_MAX_EVENTS', '1600'))
PAGE_SIZE = min(1000,max(1,int(os.getenv('NEO_TAPE_PAGE_SIZE','30'))))
MIN_TRACK_ACTIVITY = max(0,int(os.getenv('NEO_TAPE_MIN_TXNS_M5','3')))
MAX_TRACK_ACTIVITY = max(MIN_TRACK_ACTIVITY,int(os.getenv('NEO_TAPE_MAX_TXNS_M5','120')))
PAGE_BUDGET = max(1,int(os.getenv('NEO_TAPE_PAGES_PER_POLL','1')))
TX_BUDGET = max(1,int(os.getenv('NEO_TAPE_TX_PER_POLL','40')))
RPC_BATCH_SIZE = max(1,int(os.getenv('NEO_TAPE_RPC_BATCH_SIZE','20')))
GET_TRANSACTION_WORKERS = max(1,min(4,int(os.getenv('NEO_TAPE_GET_TRANSACTION_WORKERS','4'))))
WINDOW_MS = 300_000
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
_B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
NON_SWAP_INSTRUCTIONS = (
    'admin_cto_pool','admin_update_token_incentives','boost_buy_and_burn','claim_cashback',
    'claim_token_incentives','close_user_volume_accumulator','collect_coin_creator_fee','create_config',
    'create_pool','deposit','disable','extend_account','init_boost','init_user_volume_accumulator',
    'migrate_pool_coin_creator','set_boost_authority','set_coin_creator','set_reserved_fee_recipients',
    'sync_user_volume_accumulator','toggle_boost','toggle_cashback_enabled','toggle_mayhem_mode',
    'transfer_creator_fees_to_pump','transfer_creator_fees_to_pump_v2','update_admin',
    'update_buyback_config','update_creator_fee_config','update_fee_config','withdraw',
)
NON_SWAP_DISCRIMINATORS = {hashlib.sha256(('global:'+name).encode()).digest()[:8] for name in NON_SWAP_INSTRUCTIONS}
INFRA_PROGRAMS = {'11111111111111111111111111111111','TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
                  'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb','ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'}
SESSION = requests.Session()
SESSION.headers.update({'content-type':'application/json','user-agent':'NEO-LiveTape/3.0'})
STATUS = {'status':'starting','tracked_pairs':0,'updated_at':0,
          'source':'solana-mainnet-http-live','poll_seconds':POLL_SECONDS,'schema_version':3}
_RECORDER = None
_NEXT_REFERENCE_AT = 0


def shared_quote_reference():
    """One shared background FX observation, never one API request per token."""
    global _NEXT_REFERENCE_AT
    reference=quote_transport.quote_asset_reference()
    current=now_ms()
    if current>=_NEXT_REFERENCE_AT and (not reference or current-reference['observed_at']>=45_000):
        _NEXT_REFERENCE_AT=current+45_000
        quote_transport.quote(WSOL,USDC,1_000_000_000,purpose='background',slippage_bps=100)
        reference=quote_transport.quote_asset_reference()
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
    coins = list(state.get('feed',[]) or [])
    def activity_of(coin):
        tx = (coin.get('txns') or {}).get('m5') or {}
        return float(tx.get('buys') or 0)+float(tx.get('sells') or 0)
    def priority(coin):
        return (float(coin.get('score') or 0),activity_of(coin))
    coins.sort(key=priority,reverse=True)
    pinned = [dict(p.get('coin_snapshot') or {},address=p.get('address'),pairAddress=p.get('pairAddress'))
              for p in state.get('positions',[])]
    # The strict transaction decoder below only verifies the PumpSwap AMM.
    # Other DEX pools stay outside this flow tape rather than being mislabeled
    # as zero-flow or making verified PumpSwap coverage permanently degraded.
    supported_quote = lambda coin: coin.get('quoteTokenAddress') in (WSOL,USDC)
    pinned_supported = [coin for coin in pinned if str(coin.get('dexId') or '').lower() == 'pumpswap' and supported_quote(coin)]
    supported_coins = [coin for coin in coins if str(coin.get('dexId') or '').lower() == 'pumpswap'
                       and supported_quote(coin) and MIN_TRACK_ACTIVITY <= activity_of(coin) <= MAX_TRACK_ACTIVITY]
    rows,observed = {},now_ms()
    shared_reference=shared_quote_reference()
    for coin in (pinned_supported+supported_coins)[:MAX_TRACKED]:
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
    # PublicNode permits only one getTransaction per JSON-RPC request.
    # Respect that provider contract while retaining normal batching for
    # cheaper methods such as getSignaturesForAddress.
    methods={method for method,_ in calls}
    if methods=={'getTransaction'} and len(calls)>1:
        # Keep every provider request single-call, but drain independent
        # transaction bodies concurrently so one slow response cannot age
        # the entire five-minute flow window.
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(GET_TRANSACTION_WORKERS,len(calls))) as pool:
            return list(pool.map(lambda call: rpc_batch([call])[0],calls))
    batch_limit=1 if 'getTransaction' in methods else min(RPC_BATCH_SIZE,4) if 'getSignaturesForAddress' in methods else RPC_BATCH_SIZE
    if len(calls)>batch_limit:
        answers=[]
        for start in range(0,len(calls),batch_limit):
            chunk=calls[start:start+batch_limit]
            answers.extend(rpc_batch(chunk))
        return answers
    payload = [{'jsonrpc':'2.0','id':i+1,'method':method,'params':params} for i,(method,params) in enumerate(calls)]
    try:
        response = SESSION.post(RPC_URL,json=payload,timeout=15)
        response.raise_for_status()
        return align_rpc_answers(calls,response.json())
    except (requests.RequestException,RuntimeError,ValueError):
        code='TRANSACTION_RPC_UNAVAILABLE' if all(method=='getTransaction' for method,_ in calls) else 'RPC_TRANSPORT_UNAVAILABLE'
        return [{'id':i+1,'error':{'code':code}} for i in range(len(calls))]


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
            if accounts[0]==metadata['pair'] and accounts[3]==metadata['address']:
                swaps.append((index,SWAP_DISCRIMINATORS[data[:8]],accounts))
            else:
                unknown = True
        elif (program==PUMP_AMM and data[:8] not in NON_SWAP_DISCRIMINATORS) or (program!=PUMP_AMM and program not in INFRA_PROGRAMS):
            unknown = True
    if not swaps:
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
    for log_index,payload in event_payloads:
        direction = EVENT_DISCRIMINATORS.get(payload[:8])
        if not direction or len(payload)<248:
            continue
        pool,wallet,base_account,quote_account = [_b58encode(payload[a:a+32]) for a in (120,152,184,216)]
        matches = [x for x in swaps if x[1]==direction and x[2][0]==pool and x[2][1]==wallet
                   and x[2][5]==base_account and x[2][6]==quote_account]
        if not matches or pool!=metadata['pair']:
            continue
        accounts = matches[0][2]
        base_mint,quote_mint = accounts[3],accounts[4]
        base_info,quote_info = decimals.get(base_account),decimals.get(quote_account)
        if not base_info or base_info[0]!=base_mint:
            return 'unclassified',[],'BASE_DECIMALS_OR_MINT_MISSING'
        if quote_mint not in (USDC,WSOL):
            return 'unclassified',[],'UNSUPPORTED_QUOTE_ASSET'
        quote_decimals = 6 if quote_mint==USDC else 9
        if quote_info and quote_info!=(quote_mint,quote_decimals):
            return 'unclassified',[],'QUOTE_DECIMALS_OR_MINT_MISMATCH'
        event_time = int.from_bytes(payload[8:16],'little',signed=True)*1000
        if not tx.get('blockTime') or not 0<event_time<=available or abs(event_time-int(tx['blockTime'])*1000)>2000:
            return 'unclassified',[],'FUTURE_OR_INCONSISTENT_EVENT_TIME'
        base_raw,quote_raw = int.from_bytes(payload[16:24],'little'),int.from_bytes(payload[112:120],'little')
        if base_raw<=0 or quote_raw<=0 or not 0<=base_info[1]<=18:
            return 'unclassified',[],'INVALID_EVENT_AMOUNT'
        quote_amount = quote_raw/10**quote_decimals
        usd,flags,valuation = None,[],'UNKNOWN'
        if wallet not in signers:
            flags.append('SWAP_ACTOR_NOT_TRANSACTION_SIGNER')
        if quote_mint==USDC:
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
        decoded.append({'ts':event_time,'event_time':event_time,'observed_at':observed,'ingested_at':available,'available_at':available,
                        'event_index':log_index,'direction':direction,'wallet':wallet,'address':base_mint,'pairAddress':pool,
                        'symbol':metadata.get('symbol','?'),'token_raw_amount':str(base_raw),'token_decimals':base_info[1],
                        'token_amount':base_raw/10**base_info[1],'quote_asset':quote_mint,'quote_raw_amount':str(quote_raw),
                        'quote_decimals':quote_decimals,'quote_amount':quote_amount,'usd_amount':round(usd,8) if usd is not None else None,
                        'usd_valuation_source':valuation,'quality_flags':flags,'program_id':PUMP_AMM,'slot':tx.get('slot'),
                        'usd_valuation_estimated':quote_mint!=USDC,'quote_reference_at':metadata.get('quote_reference_at') if quote_mint!=USDC else None,
                        'provider':metadata.get('provider','solana-rpc'),'note':direction,'confirmed_swap':True})
    if len(decoded)!=len(swaps):
        return 'unclassified',decoded,'SWAP_EVENT_COVERAGE_INCOMPLETE'
    if any(e['quality_flags'] for e in decoded):
        return 'unclassified',decoded,'QUOTE_USD_UNKNOWN_OR_ESTIMATED_WITHOUT_ROUTE'
    return 'processed',decoded,None


def parse_trade(tx,metadata):
    _,events,_ = classify_transaction(tx,metadata)
    return events[0] if len(events)==1 else None


class TapeRecorder:
    def __init__(self,path,*,clock=now_ms,page_size=PAGE_SIZE,page_budget=PAGE_BUDGET,tx_budget=TX_BUDGET):
        self.path,self.clock = Path(path),clock
        self.page_size,self.page_budget,self.tx_budget = page_size,page_budget,tx_budget
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
            CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,signature TEXT NOT NULL,pair TEXT NOT NULL,
                event_time INTEGER NOT NULL,available INTEGER NOT NULL,payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS event_time_idx ON events(event_time);
        ''')
        self.db.commit()

    def close(self):
        self.db.close()

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
        # One shared page batch per round, rather than one HTTP call per pool.
        # The provider batch helper splits to its configured maximum safely.
        for _ in range(self.page_budget):
            if not active:
                break
            calls,states = [],[]
            for metadata in active:
                state = self.db.execute('SELECT * FROM pairs WHERE pair=?',(metadata['pair'],)).fetchone()
                options = {'limit':self.page_size,'commitment':'confirmed'}
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
                if any(not isinstance(row,dict) or not isinstance(row.get('signature'),str) or not row['signature']
                       or type(row.get('slot')) is not int or (row.get('blockTime') is not None and type(row['blockTime']) is not int)
                       for row in rows):
                    with self.db:
                        self.db.execute('UPDATE pairs SET reason=?,last_poll=? WHERE pair=?',('SIGNATURE_SCHEMA_MISMATCH',self.clock(),pair))
                    continue
                observed,cutoff = self.clock(),self.clock()-WINDOW_MS
                head = state['scan_head'] or (rows[0].get('signature') if rows else state['cursor'])
                bootstrap = state['cursor'] is None
                reached_time = bootstrap and any(r.get('blockTime') and int(r['blockTime'])*1000<cutoff for r in rows)
                complete = len(rows)<self.page_size or reached_time or any(r.get('signature')==state['cursor'] for r in rows)
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

    def process(self,rpc):
        # Drain the freshest actionable signatures first. Historical retries can
        # remain durable without starving the current five-minute decision window.
        pending = list(self.db.execute("SELECT * FROM signatures WHERE state='pending' AND next_retry<=? ORDER BY observed DESC,slot DESC LIMIT ?",(self.clock(),self.tx_budget)))
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
        for signature,answer in zip(groups,answers):
            tx = answer.get('result') if not answer.get('error') else None
            for row in groups[signature]:
                if tx is None:
                    classification,events,reason = 'retry',[],str((answer.get('error') or {}).get('code') or 'TRANSACTION_NULL')
                else:
                    try:
                        classification,events,reason = classify_transaction(tx,json.loads(row['metadata']),observed_at=row['observed'],ingested_at=self.clock())
                    except (ValueError,TypeError,KeyError,IndexError,AttributeError,OverflowError):
                        classification,events,reason = 'unclassified',[],'TRANSACTION_SCHEMA_MISMATCH'
                with self.db:
                    for event in events:
                        event.update(signature=signature,slot=event.get('slot') or row['slot'])
                        event_id = f"{signature}:{row['pair']}:{event['event_index']}"
                        event['event_id'] = event_id
                        self.db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)',
                            (event_id,signature,row['pair'],event['event_time'],event['available_at'],json.dumps(event,allow_nan=False)))
                    attempts = row['attempts']+1
                    delay = min(60_000,1000*2**min(attempts-1,6))
                    self.db.execute('UPDATE signatures SET state=?,attempts=?,next_retry=?,reason=? WHERE signature=? AND pair=?',
                        ('pending' if classification=='retry' else classification,attempts,self.clock()+delay if classification=='retry' else 0,reason,signature,row['pair']))

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
            recent_filter = '(event_time>=? OR (event_time IS NULL AND observed>=?))'
            counters = {r['state']:r['n'] for r in self.db.execute(
                f'SELECT state,count(*) n FROM signatures WHERE pair=? AND {recent_filter} GROUP BY state',
                (pair,cutoff,cutoff))}
            pending = self.db.execute(
                f"SELECT count(*) n,min(observed) oldest FROM signatures WHERE pair=? AND state='pending' AND {recent_filter}",
                (pair,cutoff,cutoff)).fetchone()
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
                'window_ms':WINDOW_MS,'projection_truncated':truncated,
                'lag_ms':max((max(0,current-r['oldest_pending_at']) for r in coverage.values() if r['oldest_pending_at']),default=0)}

    def poll(self,feed,rpc=rpc_batch):
        self.discover(feed,rpc)
        self.process(rpc)
        return self.snapshot(feed)


def atomic_write(payload):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    temporary = OUT.with_name(OUT.name+f'.{os.getpid()}.tmp')
    with temporary.open('w',encoding='utf-8') as handle:
        json.dump(payload,handle,ensure_ascii=False,allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    # Windows antivirus/indexers can briefly hold a just-written target open.
    # Retry only transient replacement permission errors; preserve atomicity and
    # report a genuine persistent failure to the recorder's backoff loop.
    for attempt in range(ATOMIC_REPLACE_ATTEMPTS):
        try:
            os.replace(temporary,OUT)
            break
        except PermissionError:
            if attempt + 1 >= ATOMIC_REPLACE_ATTEMPTS:
                raise
            time.sleep(min(.4, .025 * (2 ** attempt)))
    else:  # pragma: no cover - the loop either replaces or raises
        raise PermissionError('Unable to replace the live-tape projection')


def poll_once():
    global _RECORDER
    if _RECORDER is None:
        _RECORDER = TapeRecorder(Path(os.getenv('NEO_TAPE_DB_PATH',str(OUT.with_suffix('.sqlite3')))))
    payload = _RECORDER.poll(feed_snapshot())
    atomic_write(payload)
    return payload


def main():
    failures=0
    while True:
        started = time.monotonic()
        try:
            poll_once()
            failures=0
        except Exception as exc:
            failures+=1
            old = {}
            try:
                old = json.loads(OUT.read_text(encoding='utf-8'))
            except (OSError,ValueError):
                pass
            old={**STATUS,**old}
            old.update(status='degraded',error=type(exc).__name__,updated_at=now_ms())
            for record in old.get('pair_coverage',{}).values():
                record.update(status='DEGRADED',reason='RECORDER_UNAVAILABLE')
            atomic_write(old)
        delay=min(60,2**min(failures,6)) if failures else POLL_SECONDS
        time.sleep(max(.25,delay-(time.monotonic()-started)))


if __name__=='__main__':
    main()
