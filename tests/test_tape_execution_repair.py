"""Deterministic recorder/transport regressions; isolated DB/cache and clock."""
import base64
import copy
import json
import requests
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import live_tape as tape
import engine_execution as execution
import honest_quote_transport as transport
import pumpswap_stop_quote as pump


def pubkey(number):
    return tape._b58encode(bytes([number])*32)


MINT,PAIR,WALLET,BASE_ACCOUNT,QUOTE_ACCOUNT = [pubkey(n) for n in range(1,6)]
NOW = 1_800_000_000_000
META = {'pair':PAIR,'address':MINT,'symbol':'TEST'}


def transaction(direction='BUY',quote_mint=tape.USDC,swaps=1):
    keys = [PAIR,WALLET,pubkey(6),MINT,quote_mint,BASE_ACCOUNT,QUOTE_ACCOUNT,pubkey(7),pubkey(8)]
    data = next(k for k,v in tape.SWAP_DISCRIMINATORS.items() if v==direction)
    discriminator = next(k for k,v in tape.EVENT_DISCRIMINATORS.items() if v==direction)
    payload = discriminator+b''.join(int(x).to_bytes(8,'little',signed=x<0) for x in
        [NOW//1000,1_234_000_000,0,0,0,0,0,150_000_000,0,0,0,0,0,151_000_000])
    payload += b''.join(tape._b58decode(x) for x in [PAIR,WALLET,BASE_ACCOUNT,QUOTE_ACCOUNT])
    logs = []
    for _ in range(swaps):
        logs += [f'Program {tape.PUMP_AMM} invoke [1]',
                 'Program data: '+base64.b64encode(payload).decode(),f'Program {tape.PUMP_AMM} success']
    return {'slot':42,'blockTime':NOW//1000,'transaction':{'message':{'accountKeys':[{'pubkey':x,'signer':x==WALLET} for x in keys],
            'instructions':[{'programId':tape.PUMP_AMM,'accounts':keys,'data':tape._b58encode(data)} for _ in range(swaps)]}},
            'meta':{'err':None,'logMessages':logs,'preTokenBalances':[
                {'accountIndex':5,'mint':MINT,'uiTokenAmount':{'amount':'0','decimals':6}},
                {'accountIndex':6,'mint':quote_mint,'uiTokenAmount':{'amount':'999','decimals':6 if quote_mint==tape.USDC else 9}}],
                'postTokenBalances':[]}}


def non_swap():
    return {'blockTime':NOW//1000,'slot':42,'transaction':{'message':{'accountKeys':[{'pubkey':PAIR}],
            'instructions':[{'programId':'11111111111111111111111111111111','accounts':[PAIR],'data':'1'}]}},
            'meta':{'err':None,'preTokenBalances':[],'postTokenBalances':[]}}


class ParserTests(unittest.TestCase):
    def test_feed_snapshot_limits_order_flow_to_supported_pumpswap_pools(self):
        class Response:
            def raise_for_status(self):
                return None
            def json(self):
                return {
                    'feed': [
                        {'address':pubkey(20),'pairAddress':pubkey(21),'dexId':'raydium',
                         'priceUsd':1,'priceNative':1,'txns':{'m5':{'buys':100,'sells':100}}},
                        {'address':pubkey(22),'pairAddress':pubkey(23),'dexId':'pumpfun',
                         'priceUsd':1,'priceNative':1,'txns':{'m5':{'buys':90,'sells':90}}},
                        {'address':pubkey(24),'pairAddress':pubkey(25),'dexId':'pumpswap',
                         'priceUsd':1,'priceNative':1,'quoteTokenAddress':tape.WSOL,'txns':{'m5':{'buys':10,'sells':10}}},
                        {'address':pubkey(26),'pairAddress':pubkey(27),'dexId':'pumpswap',
                         'priceUsd':1,'priceNative':1,'quoteTokenAddress':tape.WSOL,'txns':{'m5':{'buys':9,'sells':9}}},
                        {'address':pubkey(32),'pairAddress':pubkey(33),'dexId':'pumpswap',
                         'priceUsd':1,'priceNative':1,'quoteTokenAddress':pubkey(34),'txns':{'m5':{'buys':500,'sells':500}}},
                    ],
                    'positions': [
                        {'address':pubkey(28),'pairAddress':pubkey(29),
                         'coin_snapshot':{'dexId':'pumpswap','symbol':'PIN','priceUsd':1,'quoteTokenAddress':tape.WSOL}},
                        {'address':pubkey(30),'pairAddress':pubkey(31),
                         'coin_snapshot':{'dexId':'raydium','symbol':'UNSUPPORTED','priceUsd':1}},
                    ],
                }

        with patch.object(tape.SESSION,'get',return_value=Response()), \
             patch.object(tape,'shared_quote_reference',return_value=None), \
             patch.object(tape,'MAX_TRACKED',1):
            feed=tape.feed_snapshot()
        self.assertEqual({row['pair'] for row in feed},{pubkey(29)})
        self.assertLessEqual(len(feed),1)
        self.assertTrue(all(row['dexId']=='pumpswap' for row in feed))

    def test_known_current_pumpswap_non_swap_instruction_does_not_degrade_flow(self):
        discriminator=tape.hashlib.sha256(b'global:boost_buy_and_burn').digest()[:8]
        self.assertIn(discriminator,tape.NON_SWAP_DISCRIMINATORS)
        tx=non_swap()
        tx['transaction']['message']['instructions'][0]={'programId':tape.PUMP_AMM,'accounts':[PAIR],'data':tape._b58encode(discriminator)}
        self.assertEqual(tape.classify_transaction(tx,META,observed_at=NOW,ingested_at=NOW)[0],'non_swap')

    def test_feed_snapshot_prefers_verifiable_activity_over_rpc_saturating_pool(self):
        class Response:
            def raise_for_status(self): return None
            def json(self):
                return {'feed':[
                    {'address':pubkey(35),'pairAddress':pubkey(36),'dexId':'pumpswap','score':100,
                     'quoteTokenAddress':tape.WSOL,'priceUsd':1,'priceNative':1,'txns':{'m5':{'buys':400,'sells':300}}},
                    {'address':pubkey(37),'pairAddress':pubkey(38),'dexId':'pumpswap','score':90,
                     'quoteTokenAddress':tape.WSOL,'priceUsd':1,'priceNative':1,'txns':{'m5':{'buys':30,'sells':20}}},
                ],'positions':[]}
        with patch.object(tape.SESSION,'get',return_value=Response()), patch.object(tape,'shared_quote_reference',return_value=None), patch.object(tape,'MAX_TRACKED',1):
            feed=tape.feed_snapshot()
        self.assertEqual(feed[0]['pair'],pubkey(38))

    def test_projection_atomic_replace_retries_transient_windows_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / 'live_tape.json'
            destination.write_text('{"old": true}', encoding='utf-8')
            replace = tape.os.replace
            attempts = []

            def transient_lock(source, target):
                attempts.append((source, target))
                if len(attempts) < 3:
                    raise PermissionError('temporary Windows sharing violation')
                replace(source, target)

            with patch.object(tape, 'OUT', destination), patch.object(tape.os, 'replace', side_effect=transient_lock), \
                 patch.object(tape.time, 'sleep') as delay:
                tape.atomic_write({'status': 'ok', 'events': []})
            self.assertEqual(len(attempts), 3)
            self.assertEqual(delay.call_count, 2)
            self.assertEqual(json.loads(destination.read_text(encoding='utf-8')),
                             {'status': 'ok', 'events': []})
            self.assertFalse(list(root.glob('*.tmp')))

    def test_transfer_is_not_trade_even_with_signer_token_delta(self):
        tx=transaction();tx['transaction']['message']['instructions']=[{'programId':'11111111111111111111111111111111','accounts':[PAIR],'data':'1'}]
        state,events,_=tape.classify_transaction(tx,META,ingested_at=NOW)
        self.assertEqual(state,'non_swap');self.assertEqual(events,[])

    def test_actual_quote_leg_not_scanner_token_price(self):
        state,events,_=tape.classify_transaction(transaction(),dict(META,price=99999),observed_at=NOW,ingested_at=NOW+1500)
        self.assertEqual(state,'processed');self.assertEqual(events[0]['usd_amount'],151)
        self.assertEqual(events[0]['token_raw_amount'],'1234000000')
        self.assertEqual(events[0]['available_at'],NOW+1500)

    def test_multiple_swaps_kept_separate(self):
        state,events,_=tape.classify_transaction(transaction(swaps=2),META,ingested_at=NOW)
        self.assertEqual(state,'processed');self.assertEqual(len(events),2)
        self.assertNotEqual(events[0]['event_index'],events[1]['event_index'])

    def test_anchor_cpi_event_decoded_without_program_data_logs(self):
        tx=transaction()
        payload=base64.b64decode(tx['meta']['logMessages'][1][14:])
        tx['meta']['logMessages']=[]
        tx['meta']['innerInstructions']=[{'index':0,'instructions':[
            {'programId':tape.PUMP_AMM,'accounts':[],
             'data':tape._b58encode(tape.ANCHOR_EVENT_CPI+payload)}]}]
        state,events,_=tape.classify_transaction(tx,META,ingested_at=NOW)
        self.assertEqual(state,'processed');self.assertEqual(events[0]['usd_amount'],151)

    def test_cpi_and_log_representation_not_double_counted(self):
        tx=transaction()
        payload=base64.b64decode(tx['meta']['logMessages'][1][14:])
        tx['meta']['innerInstructions']=[{'index':0,'instructions':[
            {'programId':tape.PUMP_AMM,'accounts':[],
             'data':tape._b58encode(tape.ANCHOR_EVENT_CPI+payload)}]}]
        state,events,_=tape.classify_transaction(tx,META,ingested_at=NOW)
        self.assertEqual(state,'processed');self.assertEqual(len(events),1)

    def test_wrong_pool_mint_event_rejected(self):
        state,events,_=tape.classify_transaction(transaction(),dict(META,address=pubkey(9)),ingested_at=NOW)
        self.assertNotEqual(state,'processed');self.assertEqual(events,[])

    def test_future_timestamp_and_unknown_quote_fail_closed(self):
        self.assertEqual(tape.classify_transaction(transaction(),META,ingested_at=NOW-1000)[0],'unclassified')
        self.assertEqual(tape.classify_transaction(transaction(quote_mint=pubkey(10)),META,ingested_at=NOW)[0],'unclassified')

    def test_spoofed_program_log_not_swap(self):
        tx=transaction();tx['meta']['logMessages'][0]='Program '+pubkey(11)+' invoke [1]'
        state,events,_=tape.classify_transaction(tx,META,ingested_at=NOW)
        self.assertEqual(state,'unclassified');self.assertEqual(events,[])

    def test_unknown_sol_usd_is_not_fictitious_usdc(self):
        state,events,_=tape.classify_transaction(transaction(quote_mint=tape.WSOL),META,ingested_at=NOW)
        self.assertEqual(state,'unclassified');self.assertIsNone(events[0]['usd_amount'])
        self.assertEqual(events[0]['quote_raw_amount'],'151000000')

    def test_shared_conversion_quote_values_actual_sol_leg_with_provenance(self):
        metadata=dict(META,quote_usd_reference=180,quote_reference_at=NOW,
                      quote_reference_source='JUPITER_CONVERSION_QUOTE_REFERENCE')
        state,events,_=tape.classify_transaction(transaction(quote_mint=tape.WSOL),metadata,ingested_at=NOW)
        self.assertEqual(state,'processed');self.assertAlmostEqual(events[0]['usd_amount'],27.18)
        self.assertTrue(events[0]['usd_valuation_estimated']);self.assertFalse(events[0]['quality_flags'])
        metadata['quote_reference_at']=NOW+1
        self.assertEqual(tape.classify_transaction(transaction(quote_mint=tape.WSOL),metadata,ingested_at=NOW)[0],'unclassified')

    def test_unverified_swap_actor_does_not_inflate_independent_wallets(self):
        tx=transaction()
        for key in tx['transaction']['message']['accountKeys']:key['signer']=False
        state,events,_=tape.classify_transaction(tx,META,ingested_at=NOW)
        self.assertEqual(state,'unclassified');self.assertIn('SWAP_ACTOR_NOT_TRANSACTION_SIGNER',events[0]['quality_flags'])

    def test_failed_swap_not_counted(self):
        tx=transaction();tx['meta']['err']={'InstructionError':[0,'failure']}
        self.assertEqual(tape.classify_transaction(tx,META,ingested_at=NOW)[0],'failed')

    def test_unknown_pool_program_degrades(self):
        tx=transaction();tx['transaction']['message']['instructions'][0]['programId']=pubkey(14)
        self.assertEqual(tape.classify_transaction(tx,META,ingested_at=NOW)[0],'unclassified')


class RpcAndDurability(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.clock=[NOW+1000]
        self.path=Path(self.directory.name)/'tape.sqlite3'
        self.rec=tape.TapeRecorder(self.path,clock=lambda:self.clock[0],page_size=100,page_budget=1,tx_budget=60)

    def tearDown(self):
        self.rec.close();self.directory.cleanup()

    def test_ids_missing_shuffled_and_duplicated(self):
        calls=[('x',[])]*3
        answers=tape.align_rpc_answers(calls,[{'id':3,'result':'three'},{'id':1,'result':'one'}])
        self.assertEqual(answers[0]['result'],'one');self.assertIn('error',answers[1]);self.assertEqual(answers[2]['result'],'three')
        self.assertIn('error',tape.align_rpc_answers(calls,[{'id':1,'result':1},{'id':1,'result':2}])[0])

    def test_signature_discovery_batches_are_bounded_for_public_rpc(self):
        posted=[]
        class Response:
            def __init__(self,payload): self.payload=payload
            def raise_for_status(self): return None
            def json(self): return [{'id':row['id'],'result':[]} for row in self.payload]
        def post(_url,*,json,timeout): posted.append(json);return Response(json)
        calls=[('getSignaturesForAddress',[f'p{i}',{}]) for i in range(10)]
        with patch.object(tape.SESSION,'post',side_effect=post): answers=tape.rpc_batch(calls)
        self.assertEqual(len(answers),10)
        self.assertEqual([len(payload) for payload in posted],[4,4,2])

    def test_get_transaction_respects_single_call_public_rpc_contract(self):
        posted=[]
        class Response:
            def __init__(self,payload): self.payload=payload
            def raise_for_status(self): return None
            def json(self): return [{'id':row['id'],'result':None} for row in self.payload]
        def post(_url,*,json,timeout):
            posted.append(json);return Response(json)
        calls=[('getTransaction',[f's{i}',{}]) for i in range(3)]
        with patch.object(tape.SESSION,'post',side_effect=post):
            answers=tape.rpc_batch(calls)
        self.assertEqual(len(answers),3)
        self.assertEqual([len(payload) for payload in posted],[1,1,1])

    def test_get_transaction_transport_failure_is_isolated(self):
        class Response:
            def raise_for_status(self): return None
            def json(self): return [{'id':1,'result':None}]
        def post(_url,*,json,timeout):
            if json[0]['params'][0]=='a': raise requests.ConnectionError('reset')
            return Response()
        with patch.object(tape.SESSION,'post',side_effect=post):
            answers=tape.rpc_batch([('getTransaction',['a',{}]),('getTransaction',['b',{}])])
        self.assertEqual(answers[0]['error']['code'],'TRANSACTION_RPC_UNAVAILABLE')
        self.assertIsNone(answers[1]['result'])

    def test_get_transaction_concurrency_is_bounded(self):
        self.assertGreaterEqual(tape.GET_TRANSACTION_WORKERS,1)
        self.assertLessEqual(tape.GET_TRANSACTION_WORKERS,4)

    def test_get_transaction_requests_support_version_one(self):
        with self.rec.db:
            self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,observed,metadata,state,attempts,next_retry)
                VALUES('v1',?,?,?,?,?,'pending',0,0)''',(PAIR,1,NOW,NOW,json.dumps(META)))
        seen=[]
        def rpc(calls):
            seen.extend(calls);return [{'result':None} for _ in calls]
        self.rec.process(rpc)
        self.assertEqual(seen[0][1][1]['maxSupportedTransactionVersion'],1)

    def test_over_60_signatures_never_silently_dropped_and_restart_resume(self):
        rows=[{'signature':f's{i:03}','slot':i,'blockTime':NOW//1000,'err':None} for i in range(180,0,-1)]
        requests=[]
        def rpc(calls):
            out=[]
            for method,params in calls:
                if method=='getTransaction':
                    out.append({'result':non_swap()});continue
                options=params[1];requests.append(dict(options))
                start=next((i+1 for i,r in enumerate(rows) if r['signature']==options.get('before')),0)
                stop=next((i for i,r in enumerate(rows) if r['signature']==options.get('until')),len(rows))
                out.append({'result':rows[start:min(stop,start+options['limit'])]})
            return out
        first=self.rec.poll([META],rpc)
        self.assertEqual(first['backlog'],40);self.assertEqual(first['pair_coverage'][PAIR]['status'],'UNKNOWN')
        self.rec.close();self.rec=tape.TapeRecorder(self.path,clock=lambda:self.clock[0],page_size=100,page_budget=1,tx_budget=60)
        self.rec.poll([META],rpc);final=self.rec.poll([META],rpc)
        self.assertEqual(final['classifications']['non_swap'],180);self.assertEqual(final['backlog'],0)
        self.assertEqual(final['pair_coverage'][PAIR]['status'],'COMPLETE');self.assertEqual(requests[1]['before'],'s081')
        self.rec.poll([META],rpc)
        self.assertEqual(self.rec.db.execute('SELECT count(*) FROM signatures').fetchone()[0],180)

    def test_current_pending_signatures_are_processed_before_old_retries(self):
        self.rec.tx_budget=1
        with self.rec.db:
            for signature,observed,slot in (('old',NOW-600_000,1),('current',NOW,2)):
                self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,observed,metadata,state,attempts,next_retry)
                    VALUES(?,?,?,?,?,?,'pending',0,0)''',
                    (signature,PAIR,slot,observed,observed,json.dumps(META)))
        requested=[]

        def rpc(calls):
            requested.extend(params[0] for method,params in calls if method=='getTransaction')
            return [{'result':non_swap()} for _ in calls]

        self.rec.process(rpc)
        self.assertEqual(requested,['current'])
        self.assertEqual(self.rec.db.execute("SELECT state FROM signatures WHERE signature='old'").fetchone()[0],'pending')
        self.assertEqual(self.rec.db.execute("SELECT state FROM signatures WHERE signature='current'").fetchone()[0],'non_swap')

    def test_old_unknown_history_does_not_block_complete_current_coverage(self):
        def rpc(calls):
            return [{'result':[]} for _ in calls]

        self.rec.poll([META],rpc)
        old=self.clock[0]-tape.WINDOW_MS-10
        with self.rec.db:
            self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,observed,metadata,state,attempts,next_retry)
                VALUES('stale-pending',?,?, ?,?,?, 'pending',0,0)''',
                (PAIR,1,old,old,json.dumps(META)))
            self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,observed,metadata,state,attempts,next_retry)
                VALUES('stale-unknown',?,?,NULL,?,?,'unclassified',1,0)''',
                (PAIR,2,old,json.dumps(META)))
        snapshot=self.rec.snapshot([META])
        self.assertEqual(snapshot['status'],'online')
        self.assertEqual(snapshot['backlog'],1)
        self.assertEqual(snapshot['current_backlog'],0)
        self.assertEqual(snapshot['stale_pending'],1)
        self.assertEqual(snapshot['pair_coverage'][PAIR]['unclassified'],0)

    def test_transaction_null_is_pending_then_success_after_restart(self):
        available=[False]
        def rpc(calls):
            return [{'result':([{'signature':'s','slot':42,'blockTime':NOW//1000}] if method=='getSignaturesForAddress' else
                    (transaction() if available[0] else None))} for method,_ in calls]
        self.rec.poll([META],rpc)
        self.assertEqual(self.rec.snapshot([META])['backlog'],1)
        self.rec.close();self.rec=tape.TapeRecorder(self.path,clock=lambda:self.clock[0])
        available[0]=True;self.clock[0]+=1001
        snapshot=self.rec.poll([META],rpc)
        self.assertEqual(snapshot['backlog'],0);self.assertEqual(snapshot['events_total'],1)
        self.assertEqual(snapshot['events'][0]['available_at'],self.clock[0])
        self.rec.poll([META],rpc);self.assertEqual(self.rec.snapshot([META])['events_total'],1)

    def test_processing_crash_leaves_discovery_durable(self):
        def rpc(calls):
            if calls[0][0]=='getTransaction':raise RuntimeError('provider down')
            return [{'result':[{'signature':'queued','slot':42,'blockTime':NOW//1000}]}]
        snapshot=self.rec.poll([META],rpc)
        self.assertEqual(snapshot['backlog'],1)
        self.assertEqual(self.rec.snapshot([META])['backlog'],1)
        self.rec.close();self.rec=tape.TapeRecorder(self.path,clock=lambda:self.clock[0])
        self.clock[0]+=1001
        self.rec.process(lambda calls:[{'result':non_swap()} for _ in calls])
        self.assertEqual(self.rec.snapshot([META])['backlog'],0)


class ExecutionHonesty(unittest.TestCase):
    def test_force_refresh_cannot_use_favorable_quote_from_before_exit_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'cache.json'
            path.write_text(json.dumps({'_received_at':NOW-100,'outAmount':'999999999'}))
            with patch.object(transport,'now_ms',return_value=NOW):
                self.assertIsNotNone(transport._cached(path))
                self.assertIsNone(transport._cached(path,min_received_at=NOW))

    def test_delay_revalidates_quote_freshness_before_fill(self):
        clock=[NOW]
        q={'inputMint':execution.USDC,'outputMint':MINT,'inAmount':'1','outAmount':'2','otherAmountThreshold':'1',
           'swapMode':'ExactIn','routePlan':[{}],'priceImpactPct':'0','_received_at':NOW-1900}
        def delayed(_):clock[0]+=250
        with patch.object(execution,'stamp',side_effect=lambda:clock[0]),patch.object(execution.time,'sleep',side_effect=delayed),patch.object(execution.transport,'quote',return_value=q):
            self.assertIsNone(execution._request(execution.USDC,MINT,1))

    def test_no_boolean_or_fractional_raw_quantity(self):
        for raw in (True,3.5,'3.5',0,-1):
            with patch.object(execution.transport,'quote') as provider:
                self.assertIsNone(execution.exit_quote(MINT,raw))
                provider.assert_not_called()
    def test_stale_signal_rejected_even_with_fresh_quote(self):
        q={'quoted_at':NOW,'simulated_fill_at':NOW}
        self.assertTrue(execution.signal_fresh_at_commit(NOW-7000,q,now=NOW))
        self.assertFalse(execution.signal_fresh_at_commit(NOW-9000,q,now=NOW))
        self.assertFalse(execution.signal_fresh_at_commit(NOW+1,q,now=NOW))

    def test_slot_lag_and_malformed_amount_rejected(self):
        q={'inputMint':execution.USDC,'outputMint':MINT,'inAmount':'1','outAmount':'2','otherAmountThreshold':'1',
           'swapMode':'ExactIn','routePlan':[{}],'priceImpactPct':'0','_received_at':NOW,'contextSlot':10,'_latest_slot':36}
        with patch.object(execution,'stamp',return_value=NOW):
            self.assertFalse(execution.valid(q,execution.USDC,MINT,1,NOW))
            q['_latest_slot']=30;self.assertTrue(execution.valid(q,execution.USDC,MINT,1,NOW))
            q['inAmount']=True;self.assertFalse(execution.valid(q,execution.USDC,MINT,1,NOW))

    def test_legacy_facade_preserves_actual_quantity_and_never_returns_stale(self):
        import paper_execution_quotes as legacy
        position={'address':MINT,'pairAddress':PAIR,'jupiter_token_raw_amount':4,'jupiter_token_raw_expected':99}
        with patch.object(execution,'position_mark',return_value=None) as mark:
            self.assertIsNone(legacy.position_mark(position,{},.1))
            self.assertEqual(mark.call_args.args[0]['jupiter_token_raw_amount'],4)


class TransportContracts(unittest.TestCase):
    def test_http_failures_are_explicit_and_read_only(self):
        class Response:
            def __init__(self,status):self.status_code=status;self.headers={}
            def raise_for_status(self):pass
            def json(self):return {'unexpected':'schema'}
        class Http:
            def __init__(self,status):self.status=status;self.calls=[]
            def get(self,*args,**kw):self.calls.append((args,kw));return Response(self.status)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.multiple(transport,ROOT=root,LOCK=root/'lock',STAMP=root/'stamp',COOLDOWN=root/'cooldown',INTERVAL=0):
                for status,code in [(401,'AUTHENTICATION_REQUIRED'),(403,'ACCESS_DENIED'),(200,'SCHEMA_MISMATCH'),(429,'RATE_LIMITED')]:
                    http=Http(status);transport.LOCAL.http=http
                    self.assertIsNone(transport.quote(execution.USDC,MINT,1))
                    self.assertEqual(transport.last_error()['code'],code)
                    self.assertEqual(len(http.calls),1)
                    self.assertIn('/swap/v1/quote',http.calls[0][0][0])
                    self.assertNotIn('userPublicKey',http.calls[0][1]['params'])
        if hasattr(transport.LOCAL,'http'):del transport.LOCAL.http


class PumpEvidence(unittest.TestCase):
    def test_integer_fee_rounding_at_large_raw_quantities(self):
        result=pump.quote_from_reserves(10**22,2**63-17,0,10**16,{'lp':25,'protocol':5,'creator':10})
        for name,bps in [('lp',25),('protocol',5),('creator',10)]:
            self.assertEqual(result[name+'_fee_raw'],(result['raw_quote_out']*bps+9999)//10000)
        with self.assertRaises(ValueError):pump.quote_from_reserves(1.5,100,0,1,{'lp':0})

    def test_vault_mint_authority_owner_and_state_verified(self):
        data=bytearray(165)
        data[:32]=tape._b58decode(MINT);data[32:64]=tape._b58decode(PAIR)
        data[64:72]=(123).to_bytes(8,'little');data[108]=1
        value={'owner':pump.TOKEN_PROGRAM,'data':[base64.b64encode(data).decode(),'base64']}
        self.assertEqual(pump._validate_token_account(value,MINT,PAIR),123)
        for mint,authority in [(pubkey(9),PAIR),(MINT,pubkey(9))]:
            with self.assertRaises(ValueError):pump._validate_token_account(value,mint,authority)
        value['owner']=pubkey(9)
        with self.assertRaises(ValueError):pump._validate_token_account(value,MINT,PAIR)

    def test_missing_conversion_cannot_book_scanner_sol_usd(self):
        position={'address':MINT,'pairAddress':PAIR,'jupiter_token_raw_amount':99,'quantity':99}
        with patch.object(pump.jupiter,'position_mark',return_value=None):
            self.assertIsNone(pump.position_mark(position,{'priceUsd':999999},.1,999999))
        with patch.object(pump.jupiter,'prepare_entry',return_value=None):
            self.assertIsNone(pump.prepare_entry({'address':MINT,'pairAddress':PAIR},200,999999))

    def test_cached_reserve_observation_requires_verified_accounts(self):
        with patch.object(pump,'_SNAPSHOT_CACHE',{PAIR:{'quoted_at':NOW,'pool':{'base_mint':MINT,'quote_mint':pump.WSOL}}}),patch.object(pump,'now_ms',return_value=NOW):
            self.assertIsNone(pump.cached_snapshot(PAIR,MINT))


if __name__=='__main__':unittest.main()
