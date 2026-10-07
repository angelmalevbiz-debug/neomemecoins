"""Deterministic recorder/transport regressions; isolated DB/cache and clock."""
import base64
import copy
import json
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
                         'priceUsd':1,'priceNative':1,'txns':{'m5':{'buys':10,'sells':10}}},
                        {'address':pubkey(26),'pairAddress':pubkey(27),'dexId':'pumpswap',
                         'priceUsd':1,'priceNative':1,'txns':{'m5':{'buys':9,'sells':9}}},
                    ],
                    'positions': [
                        {'address':pubkey(28),'pairAddress':pubkey(29),
                         'coin_snapshot':{'dexId':'pumpswap','symbol':'PIN','priceUsd':1}},
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

    def test_feed_snapshot_prioritizes_actionable_moderate_flow_pools(self):
        class Response:
            def raise_for_status(self):
                return None
            def json(self):
                def coin(index, *, score, activity):
                    return {
                        'address': pubkey(index), 'pairAddress': pubkey(index+1),
                        'symbol': f'C{index}', 'dexId': 'pumpswap',
                        'score': score, 'liquidityUsd': 12_000, 'marketCap': 100_000,
                        'ageMinutes': 30, 'priceUsd': 1, 'priceNative': 1,
                        'priceChange': {'m5': 5, 'h1': 10},
                        'txns': {'m5': {'buys': activity*2//3, 'sells': activity//3}},
                        'volume': {'h1': 6_000},
                    }
                return {'feed': [coin(40, score=99, activity=900),
                                 coin(42, score=90, activity=600),
                                 coin(46, score=99, activity=9),
                                 coin(44, score=80, activity=60)], 'positions': []}

        with patch.object(tape.SESSION, 'get', return_value=Response()), \
             patch.object(tape, 'shared_quote_reference', return_value=None), \
             patch.object(tape, 'MAX_TRACKED', 1):
            feed = tape.feed_snapshot()
        self.assertEqual([row['symbol'] for row in feed], ['C44'])

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

    def test_transient_fx_refresh_retries_before_reference_expiry_then_keeps_normal_interval(self):
        clock=[NOW]
        reference=[{'observed_at':NOW-45_000,'usd_per_unit':180,
                    'source':'JUPITER_CONVERSION_QUOTE_REFERENCE'}]
        attempts=[]
        def current_reference():
            return reference[0] if clock[0]-reference[0]['observed_at']<=60_000 else None
        def refresh(*args,**kwargs):
            attempts.append((clock[0],kwargs))
            if len(attempts)==1:
                return None
            reference[0]=dict(reference[0],observed_at=clock[0])
            return {'_received_at':clock[0]}
        with patch.object(tape,'now_ms',side_effect=lambda:clock[0]), \
             patch.object(tape,'_NEXT_REFERENCE_AT',0), \
             patch.object(tape.quote_transport,'quote_asset_reference',side_effect=current_reference), \
             patch.object(tape.quote_transport,'quote',side_effect=refresh), \
             patch.object(tape.quote_transport,'last_error',return_value={'code':'TIMEOUT'}):
            self.assertEqual(tape.shared_quote_reference()['observed_at'],NOW-45_000)
            clock[0]=NOW+4999
            tape.shared_quote_reference()
            self.assertEqual(len(attempts),1)
            clock[0]=NOW+5000
            self.assertEqual(tape.shared_quote_reference()['observed_at'],NOW+5000)
            self.assertEqual(len(attempts),2)
            self.assertLess(clock[0],NOW+15_000)
            clock[0]=NOW+49_999
            tape.shared_quote_reference()
            self.assertEqual(len(attempts),2)
            clock[0]=NOW+50_000
            tape.shared_quote_reference()
            self.assertEqual(len(attempts),3)
        self.assertTrue(all(kwargs['purpose']=='background' for _,kwargs in attempts))

    def test_fx_authorization_failure_keeps_normal_retry_interval(self):
        clock=[NOW]
        with patch.object(tape,'now_ms',side_effect=lambda:clock[0]), \
             patch.object(tape,'_NEXT_REFERENCE_AT',0), \
             patch.object(tape.quote_transport,'quote_asset_reference',return_value=None), \
             patch.object(tape.quote_transport,'quote',return_value=None) as provider, \
             patch.object(tape.quote_transport,'last_error',return_value={'code':'AUTHENTICATION_REQUIRED'}):
            self.assertIsNone(tape.shared_quote_reference())
            clock[0]=NOW+5000
            self.assertIsNone(tape.shared_quote_reference())
            self.assertEqual(provider.call_count,1)
            clock[0]=NOW+45_000
            self.assertIsNone(tape.shared_quote_reference())
            self.assertEqual(provider.call_count,2)

    def test_unverified_swap_actor_does_not_inflate_independent_wallets(self):
        tx=transaction()
        for key in tx['transaction']['message']['accountKeys']:key['signer']=False
        state,events,reason=tape.classify_transaction(tx,META,ingested_at=NOW)
        self.assertEqual(state,'unclassified');self.assertIn('SWAP_ACTOR_NOT_TRANSACTION_SIGNER',events[0]['quality_flags'])
        self.assertEqual(reason,'SWAP_ACTOR_NOT_TRANSACTION_SIGNER')

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

    def test_indexed_decision_window_preserves_boundaries_and_unknown_times(self):
        self.rec.poll([META],lambda calls:[{'result':[]} for _ in calls])
        cutoff=self.clock[0]-tape.DECISION_FLOW_WINDOW_MS
        with self.rec.db:
            self.rec.db.executemany('''INSERT INTO signatures(signature,pair,slot,
                event_time,observed,metadata,state) VALUES(?,?,1,?,?,?,'pending')''',
                ((f'historical-{i}',PAIR,cutoff-1,NOW,json.dumps(META)) for i in range(4000)))
            for signature,pair,event_time,observed,state in (
                    ('boundary-known',PAIR,cutoff,NOW-100,'pending'),
                    ('boundary-unknown',PAIR,None,cutoff,'pending'),
                    ('current-unknown',PAIR,None,NOW,'unclassified'),
                    ('old-unknown',PAIR,None,cutoff-1,'unclassified'),
                    ('other-pool',pubkey(20),NOW,NOW,'pending')):
                self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,
                    event_time,observed,metadata,state) VALUES(?,?,2,?,?,?,?)''',
                    (signature,pair,event_time,observed,json.dumps(META),state))
        queries=[]
        self.rec.db.set_trace_callback(queries.append)
        try:
            snapshot=self.rec.snapshot([META])
        finally:
            self.rec.db.set_trace_callback(None)
        coverage=snapshot['pair_coverage'][PAIR]
        self.assertEqual(coverage['backlog'],2)
        self.assertEqual(coverage['oldest_pending_at'],cutoff)
        self.assertEqual(coverage['unclassified'],1)
        self.assertEqual(coverage['status'],'DEGRADED')
        self.assertEqual(snapshot['backlog'],4003)
        self.assertEqual(self.rec.db.execute('SELECT count(*) FROM signatures').fetchone()[0],4005)
        windows=[query for query in queries if 'UNION ALL SELECT state,observed' in query]
        self.assertEqual(len(windows),2)
        for query in windows:
            plan=' '.join(row['detail'] for row in self.rec.db.execute('EXPLAIN QUERY PLAN '+query))
            self.assertIn('signature_pair_window_idx (pair=? AND event_time>?)',plan)
            self.assertIn('signature_pair_window_idx (pair=? AND event_time=? AND observed>?)',plan)
            self.assertNotIn('SCAN signatures',plan)
            # Bound SQLite VM work, rather than wall time on the test machine.
            ticks=[]
            self.rec.db.set_progress_handler(lambda:ticks.append(1) or 0,100)
            try:
                list(self.rec.db.execute(query))
            finally:
                self.rec.db.set_progress_handler(None,0)
            self.assertLess(len(ticks),10)

    def test_indexed_pending_queue_keeps_fresh_priority_and_retry_deadline(self):
        self.rec.tx_budget=2
        with self.rec.db:
            self.rec.db.executemany('''INSERT INTO signatures(signature,pair,slot,
                event_time,observed,metadata,state) VALUES(?,?,1,?,?,?,'pending')''',
                ((f'historical-{i}',PAIR,NOW-600_000,NOW,json.dumps(META)) for i in range(4000)))
            for signature,event_time,observed,retry in (
                    ('waiting',NOW+500,NOW+500,self.clock[0]+1),
                    ('live-known',NOW-100,NOW,0),('live-unknown',None,NOW+100,0)):
                self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,
                    event_time,observed,metadata,state,next_retry) VALUES(?,?,2,?,?,?,'pending',?)''',
                    (signature,PAIR,event_time,observed,json.dumps(META),retry))
        queries=[];requested=[]
        def rpc(calls):
            requested.extend(params[0] for _,params in calls)
            return [{'result':non_swap()} for _ in calls]
        self.rec.db.set_trace_callback(queries.append)
        try:
            self.rec.process(rpc)
        finally:
            self.rec.db.set_trace_callback(None)
        self.assertEqual(requested,['live-unknown','live-known'])
        self.assertEqual(self.rec.db.execute("SELECT state FROM signatures WHERE signature='waiting'").fetchone()[0],'pending')
        self.assertEqual(self.rec.db.execute("SELECT count(*) FROM signatures WHERE state='pending'").fetchone()[0],4001)
        selection=next(query for query in queries if 'SELECT * FROM signatures INDEXED BY' in query)
        plan=' '.join(row['detail'] for row in self.rec.db.execute('EXPLAIN QUERY PLAN '+selection))
        self.assertIn('pending_fresh_idx',plan)
        self.assertNotIn('TEMP B-TREE',plan)

    def test_index_migration_preserves_existing_rows_and_is_idempotent(self):
        self.rec.poll([META],lambda calls:[{'result':[]} for _ in calls])
        with self.rec.db:
            self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,
                observed,metadata,state,attempts,next_retry,reason)
                VALUES('retained',?,42,?,?,?,'pending',7,?,'TRANSACTION_NULL')''',
                (PAIR,NOW-600_000,NOW,json.dumps(META),NOW+5000))
            self.rec.db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',
                ('retained-event','retained',PAIR,NOW-600_000,NOW,'{"retained":true}'))
            for index in ('signature_pair_window_idx','pending_fresh_idx','event_pair_time_idx'):
                self.rec.db.execute('DROP INDEX '+index)
        before={table:[tuple(row) for row in self.rec.db.execute('SELECT * FROM '+table)]
                for table in ('pairs','signatures','events')}
        for _ in range(2):
            self.rec.close();self.rec=tape.TapeRecorder(self.path,clock=lambda:self.clock[0])
            after={table:[tuple(row) for row in self.rec.db.execute('SELECT * FROM '+table)]
                   for table in before}
            self.assertEqual(after,before)
            indexes={row['name'] for row in self.rec.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertTrue({'signature_pair_window_idx','pending_fresh_idx','event_pair_time_idx'}<=indexes)
        plan=' '.join(row['detail'] for row in self.rec.db.execute(
            'EXPLAIN QUERY PLAN SELECT max(event_time) FROM events WHERE pair=?',(PAIR,)))
        self.assertIn('event_pair_time_idx (pair=?)',plan)

    def test_ids_missing_shuffled_and_duplicated(self):
        calls=[('x',[])]*3
        answers=tape.align_rpc_answers(calls,[{'id':3,'result':'three'},{'id':1,'result':'one'}])
        self.assertEqual(answers[0]['result'],'one');self.assertIn('error',answers[1]);self.assertEqual(answers[2]['result'],'three')
        self.assertIn('error',tape.align_rpc_answers(calls,[{'id':1,'result':1},{'id':1,'result':2}])[0])

    def test_get_transaction_uses_singleton_rpc_requests_while_signatures_stay_batched(self):
        payloads=[]

        class Response:
            def __init__(self,payload):self.payload=payload
            def raise_for_status(self):return None
            def json(self):
                if isinstance(self.payload,dict):
                    return {'id':1,'result':self.payload['params'][0]}
                return [{'id':i+1,'result':request['params'][0]}
                        for i,request in enumerate(self.payload)]

        def post(url,json,timeout):
            payloads.append(json)
            return Response(json)

        signatures=[f'transaction-{i}' for i in range(8)]
        transaction_calls=[('getTransaction',[signature,{'encoding':'jsonParsed'}]) for signature in signatures]
        with patch.object(tape.SESSION,'post',side_effect=post), patch.object(tape,'RPC_TRANSACTION_CONCURRENCY',4):
            answers=tape.rpc_batch(transaction_calls)
            transaction_payloads=list(payloads)
            payloads.clear()
            signature_calls=[('getSignaturesForAddress',[pubkey(20+i),{'limit':1}]) for i in range(8)]
            signature_answers=tape.rpc_batch(signature_calls)

        self.assertEqual(len(transaction_payloads),len(transaction_calls))
        self.assertTrue(all(isinstance(payload,dict) and payload['method']=='getTransaction'
                            for payload in transaction_payloads))
        self.assertEqual([answer['result'] for answer in answers],signatures)
        self.assertEqual(len(payloads),1)
        self.assertEqual(len(payloads[0]),len(signature_calls))
        self.assertEqual(len(signature_answers),len(signature_calls))

    def test_one_failed_transaction_body_preserves_other_successful_answers(self):
        class Response:
            def __init__(self,signature):self.signature=signature
            def raise_for_status(self):return None
            def json(self):return {'id':1,'result':self.signature}

        def post(url,json,timeout):
            signature=json['params'][0]
            if signature=='provider-failure':
                raise tape.requests.Timeout('one transaction timed out')
            return Response(signature)

        calls=[('getTransaction',[signature,{}]) for signature in
               ['first','provider-failure','third']]
        with patch.object(tape.SESSION,'post',side_effect=post):
            answers=tape.rpc_batch(calls)
        self.assertEqual(answers[0]['result'],'first')
        self.assertEqual(answers[1]['error']['code'],'TRANSACTION_RPC_UNAVAILABLE')
        self.assertEqual(answers[2]['result'],'third')

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
        self.rec.tx_budget=180
        first=self.rec.poll([META],rpc)
        self.assertEqual(first['backlog'],0);self.assertEqual(first['pair_coverage'][PAIR]['status'],'UNKNOWN')
        self.rec.close();self.rec=tape.TapeRecorder(self.path,clock=lambda:self.clock[0],page_size=100,page_budget=1,tx_budget=180)
        self.rec.poll([META],rpc);final=self.rec.poll([META],rpc)
        self.assertEqual(final['classifications']['non_swap'],180);self.assertEqual(final['backlog'],0)
        self.assertEqual(final['pair_coverage'][PAIR]['status'],'COMPLETE');self.assertEqual(requests[1]['before'],'s091')
        self.rec.poll([META],rpc)
        self.assertEqual(self.rec.db.execute('SELECT count(*) FROM signatures').fetchone()[0],180)

    def test_stale_pagination_restarts_at_live_head_without_erasing_history(self):
        now=self.clock[0]
        old_times={'old-cursor':now-3*tape.WINDOW_MS,
                   'old-page':now-2*tape.WINDOW_MS,
                   'stale-head':now-tape.WINDOW_MS-1}
        with self.rec.db:
            self.rec.db.execute('''INSERT INTO pairs(pair,mint,cursor,before_sig,scan_head,
                complete_since,last_poll,reason,metadata) VALUES(?,?,?,?,?,?,?,?,?)''',
                (PAIR,MINT,'old-cursor','old-page','stale-head',now-4*tape.WINDOW_MS,
                 now-1000,'PAGINATION_PENDING',json.dumps(META)))
            for index,(signature,event_time) in enumerate(old_times.items()):
                self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,
                    observed,metadata,state) VALUES(?,?,?,?,?,?,'processed')''',
                    (signature,PAIR,index+1,event_time,now-5000,json.dumps(META)))

        requests=[]
        def rpc(calls):
            result=[]
            for method,params in calls:
                if method=='getSignaturesForAddress':
                    requests.append(params[1])
                    result.append({'result':[{'signature':'fresh-head','slot':999,
                                              'blockTime':now//1000,'err':None}]})
                else:
                    result.append({'result':non_swap()})
            return result

        snapshot=self.rec.poll([META],rpc)
        state=self.rec.db.execute('SELECT * FROM pairs WHERE pair=?',(PAIR,)).fetchone()
        coverage=snapshot['pair_coverage'][PAIR]
        self.assertEqual(len(requests),1)
        self.assertNotIn('before',requests[0])
        self.assertNotIn('until',requests[0])
        self.assertEqual(state['cursor'],'fresh-head')
        self.assertIsNone(state['before_sig'])
        self.assertIsNone(state['scan_head'])
        self.assertEqual(coverage['status'],'COMPLETE')
        self.assertGreaterEqual(coverage['complete_since_ms'],now-tape.DECISION_FLOW_WINDOW_MS)
        self.assertEqual(self.rec.db.execute('SELECT count(*) FROM signatures WHERE pair=?',(PAIR,)).fetchone()[0],4)

    def test_inactive_pair_restarts_fresh_window_even_without_pending_page(self):
        now=self.clock[0]
        with self.rec.db:
            self.rec.db.execute('''INSERT INTO pairs(pair,mint,cursor,before_sig,scan_head,
                complete_since,last_poll,reason,metadata) VALUES(?,?,?,?,?,?,?,?,?)''',
                (PAIR,MINT,'old-cursor',None,None,now-tape.WINDOW_MS,
                 now-tape.DECISION_FLOW_WINDOW_MS-1,None,json.dumps(META)))
            self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,
                observed,metadata,state) VALUES(?,?,?,?,?,?,'processed')''',
                ('old-cursor',PAIR,1,now-tape.WINDOW_MS,now-tape.WINDOW_MS,json.dumps(META)))

        requests=[]
        def rpc(calls):
            out=[]
            for _,params in calls:
                requests.append(params[1])
                out.append({'result':[{'signature':'fresh-head','slot':2,
                                       'blockTime':now//1000,'err':None}]})
            return out

        self.rec.discover([META],rpc)
        self.assertNotIn('until',requests[0])
        self.assertEqual(self.rec.db.execute('SELECT cursor FROM pairs WHERE pair=?',(PAIR,)).fetchone()[0],
                         'fresh-head')
        self.assertIsNone(self.rec.db.execute('SELECT before_sig FROM pairs WHERE pair=?',(PAIR,)).fetchone()[0])
        self.assertEqual(self.rec.db.execute('SELECT count(*) FROM signatures WHERE pair=?',(PAIR,)).fetchone()[0],2)

    def test_large_single_rpc_page_completes_fresh_pool_without_false_pagination(self):
        self.rec.page_size=1000
        self.rec.tx_budget=200
        rows=[{'signature':f'fresh-{i:03}','slot':i,'blockTime':NOW//1000,'err':None}
              for i in range(80,0,-1)]
        signature_pages=[]

        def rpc(calls):
            answers=[]
            for method,params in calls:
                if method=='getSignaturesForAddress':
                    signature_pages.append(params[1]['limit'])
                    answers.append({'result':rows})
                else:
                    answers.append({'result':non_swap()})
            return answers

        snapshot=self.rec.poll([META],rpc)
        self.assertEqual(signature_pages,[100])
        self.assertEqual(snapshot['pair_coverage'][PAIR]['status'],'COMPLETE')
        self.assertFalse(snapshot['pair_coverage'][PAIR]['pagination_pending'])
        self.assertEqual(snapshot['backlog'],0)
        self.assertEqual(snapshot['classifications']['non_swap'],80)

    def test_signature_discovery_budget_is_shared_across_active_pools(self):
        self.rec.page_size=1000
        self.rec.tx_budget=60
        feed=[dict(META,address=pubkey(20+i),pair=pubkey(40+i)) for i in range(12)]
        limits=[]

        def rpc(calls):
            answers=[]
            for method,params in calls:
                if method=='getSignaturesForAddress':
                    limits.append(params[1]['limit'])
                    answers.append({'result':[]})
                else:
                    answers.append({'result':non_swap()})
            return answers

        snapshot=self.rec.poll(feed,rpc)
        self.assertEqual(len(limits),12)
        self.assertEqual(set(limits),{2})
        self.assertLessEqual(sum(limits),self.rec.tx_budget//2)
        self.assertEqual(snapshot['tracked_pairs'],12)
        self.assertEqual(snapshot['coverage'],1.0)

    def test_current_pending_signatures_are_processed_before_old_retries(self):
        self.rec.tx_budget=1
        with self.rec.db:
            for signature,observed,slot in (('old',NOW-600_000,1),('current',NOW,2)):
                self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,observed,metadata,state,attempts,next_retry)
                    VALUES(?,?,?,?,?,?,'pending',0,0)''',
                    (signature,PAIR,slot,observed,observed,json.dumps(META)))
        requested=[]

        def rpc(calls):
            requested.extend((params[0],params[1]) for method,params in calls if method=='getTransaction')
            return [{'result':non_swap()} for _ in calls]

        self.rec.process(rpc)
        self.assertEqual([signature for signature,_ in requested],['current'])
        self.assertEqual(requested[0][1]['maxSupportedTransactionVersion'],1)
        self.assertEqual(self.rec.db.execute("SELECT state FROM signatures WHERE signature='old'").fetchone()[0],'pending')
        self.assertEqual(self.rec.db.execute("SELECT state FROM signatures WHERE signature='current'").fetchone()[0],'non_swap')

    def test_large_historical_queue_cannot_fill_unused_live_request_budget(self):
        self.rec.tx_budget=48
        self.rec.historical_tx_budget=1
        with self.rec.db:
            for i in range(100):
                self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,
                    observed,metadata,state) VALUES(?,?,?,?,?,?,'pending')''',
                    (f'old-{i}',PAIR,i,NOW-600_000,NOW-600_000,json.dumps(META)))
            for i in range(2):
                self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,
                    observed,metadata,state) VALUES(?,?,?,?,?,?,'pending')''',
                    (f'live-{i}',PAIR,100+i,NOW,NOW,json.dumps(META)))
        requested=[]
        def rpc(calls):
            requested.extend(params[0] for _,params in calls)
            return [{'result':non_swap()} for _ in calls]
        self.rec.process(rpc)
        self.assertEqual(set(requested[:2]),{'live-0','live-1'})
        self.assertEqual(len(requested),3)
        self.assertEqual(self.rec.db.execute("SELECT count(*) FROM signatures WHERE state='pending'").fetchone()[0],99)

    def test_old_page_discovered_now_cannot_displace_live_onchain_time(self):
        self.rec.tx_budget=1
        with self.rec.db:
            for signature,event_time,observed in (
                    ('old-page',NOW-tape.WINDOW_MS-1,NOW+500),('live',NOW,NOW)):
                self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,
                    observed,metadata,state) VALUES(?,?,1,?,?,?,'pending')''',
                    (signature,PAIR,event_time,observed,json.dumps(META)))
        requested=[]
        def rpc(calls):
            requested.extend(params[0] for _,params in calls)
            return [{'result':non_swap()} for _ in calls]
        self.rec.process(rpc)
        self.assertEqual(requested,['live'])
        self.assertEqual(self.rec.db.execute("SELECT state FROM signatures WHERE signature='old-page'").fetchone()[0],'pending')

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

    def test_unknown_trade_older_than_decision_window_does_not_poison_live_flow(self):
        def rpc(calls):
            return [{'result':[]} for _ in calls]

        self.rec.poll([META],rpc)
        old=self.clock[0]-tape.DECISION_FLOW_WINDOW_MS-1
        with self.rec.db:
            self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,observed,
                metadata,state,attempts,next_retry) VALUES('older-unknown',?,?, ?,?,?, 'unclassified',1,0)''',
                (PAIR,1,old,old,json.dumps(META)))
        snapshot=self.rec.snapshot([META])
        self.assertEqual(snapshot['pair_coverage'][PAIR]['status'],'COMPLETE')
        self.assertEqual(snapshot['pair_coverage'][PAIR]['unclassified'],0)
        self.assertEqual(snapshot['status'],'online')

    def test_unclassified_signature_in_decision_window_blocks_live_flow(self):
        def rpc(calls):
            return [{'result':[]} for _ in calls]

        self.rec.poll([META],rpc)
        current=self.clock[0]-tape.DECISION_FLOW_WINDOW_MS+1
        with self.rec.db:
            self.rec.db.execute('''INSERT INTO signatures(signature,pair,slot,event_time,observed,
                metadata,state,attempts,next_retry) VALUES('current-unknown',?,?, ?,?,?, 'unclassified',1,0)''',
                (PAIR,1,current,current,json.dumps(META)))
        snapshot=self.rec.snapshot([META])
        self.assertEqual(snapshot['pair_coverage'][PAIR]['status'],'DEGRADED')
        self.assertEqual(snapshot['pair_coverage'][PAIR]['reason'],'UNCLASSIFIED_TRANSACTIONS')
        self.assertEqual(snapshot['pair_coverage'][PAIR]['unclassified'],1)

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


class RecorderRecovery(unittest.TestCase):
    def test_locked_failure_projection_does_not_kill_recorder_and_success_resets_backoff(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'tape.json'
            out.write_text(json.dumps({'pair_coverage': {PAIR: {'status': 'COMPLETE'}}}))
            with patch.object(tape, 'OUT', out), \
                    patch.object(tape, 'poll_once', side_effect=[PermissionError('locked'), {}, KeyboardInterrupt()]) as poll, \
                    patch.object(tape, 'atomic_write', side_effect=PermissionError('still locked')) as writer, \
                    patch.object(tape.time, 'monotonic', return_value=0), \
                    patch.object(tape.time, 'sleep') as sleep, \
                    patch.object(tape, 'now_ms', return_value=NOW), \
                    patch.object(tape, 'print') as log:
                with self.assertRaises(KeyboardInterrupt):
                    tape.main()
            self.assertEqual(poll.call_count, 3)
            degraded = writer.call_args.args[0]
            self.assertEqual(degraded['pair_coverage'][PAIR]['status'], 'DEGRADED')
            self.assertEqual(degraded['error'], 'PermissionError')
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, tape.POLL_SECONDS])
            self.assertTrue(any('failure projection unavailable' in call.args[0] for call in log.call_args_list))

    def test_malformed_saved_projection_cannot_break_error_backoff(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'tape.json'
            out.write_text('[]')
            with patch.object(tape, 'OUT', out), \
                    patch.object(tape, 'poll_once', side_effect=[RuntimeError('RPC unavailable'), KeyboardInterrupt()]), \
                    patch.object(tape, 'atomic_write') as writer, \
                    patch.object(tape.time, 'sleep'), patch.object(tape, 'print'):
                with self.assertRaises(KeyboardInterrupt):
                    tape.main()
            self.assertEqual(writer.call_args.args[0]['status'], 'degraded')


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
