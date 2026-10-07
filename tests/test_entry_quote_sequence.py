"""Offline concurrency and exact refusal evidence for three-leg PAPER quotes."""
import copy
import json
import multiprocessing
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import engine_execution as execution
import honest_quote_transport as transport

MINT='A'*44
PAIR='B'*44
NOW=1_800_000_000_000


def raw_quote(inp,out,amount):
    return {'inputMint':inp,'outputMint':out,'inAmount':str(amount),
            'outAmount':'100000000','otherAmountThreshold':'99000000',
            'swapMode':'ExactIn','priceImpactPct':'0.001','slippageBps':100,
            'contextSlot':100,'routePlan':[{'swapInfo':{
                'ammKey':PAIR,'inputMint':inp,'outputMint':out,
                'inAmount':str(amount),'outAmount':'100000000'}}]}


class FakeHttp:
    def __init__(self):self.calls=[]
    def get(self,_url,**kwargs):
        self.calls.append(kwargs)
        params=kwargs['params']
        data=raw_quote(params['inputMint'],params['outputMint'],int(params['amount']))
        class Response:
            status_code=200
            def raise_for_status(self):pass
            def json(self):return copy.deepcopy(data)
        return Response()


def child_requests(directory,result,claim_only=False):
    """A separate process must observe the same lease without shared globals."""
    root=Path(directory)
    transport.ROOT=root
    transport.LOCK=root/'shared.lock'
    transport.STAMP=root/'shared-stamp.txt'
    transport.COOLDOWN=root/'shared-backoff.json'
    transport.INTERVAL=0
    http=FakeHttp()
    transport.LOCAL.http=http
    with transport.entry_sequence() as admitted:
        claim_error=transport.last_error()
    if claim_only:
        result.put({'admitted':admitted,'error':claim_error})
        return
    refused=[]
    for purpose in ['background','entry']:
        value=transport.quote(transport.USDC,MINT,123,purpose=purpose)
        refused.append((value,transport.last_error()))
    exit_quote=transport.quote(MINT,transport.USDC,456,purpose='exit')
    result.put({'admitted':admitted,'error':claim_error,'refused':refused,
                'exit':bool(exit_quote),'http_calls':len(http.calls)})


class EntryReservationTests(unittest.TestCase):
    def configure(self,root):
        return patch.multiple(transport,ROOT=root,LOCK=root/'shared.lock',
                              STAMP=root/'shared-stamp.txt',
                              COOLDOWN=root/'shared-backoff.json',INTERVAL=0)

    def child(self,directory,claim_only=False):
        context=multiprocessing.get_context('spawn')
        result=context.Queue()
        process=context.Process(target=child_requests,args=(directory,result,claim_only))
        process.start()
        process.join(8)
        if process.is_alive():
            process.terminate();process.join(3)
            self.fail('Cross-process reservation did not defer promptly')
        self.assertEqual(process.exitcode,0)
        data=result.get(timeout=2)
        result.close()
        return data

    def test_other_process_defers_entries_and_background_but_can_exit(self):
        with tempfile.TemporaryDirectory() as directory,self.configure(Path(directory)):
            with transport.entry_sequence() as admitted:
                self.assertTrue(admitted)
                data=self.child(directory)
                self.assertFalse(data['admitted'])
                self.assertEqual(data['error']['code'],'ENTRY_SEQUENCE_BUSY')
                for quote,error in data['refused']:
                    self.assertIsNone(quote)
                    self.assertEqual(error['code'],'ENTRY_SEQUENCE_BUSY')
                self.assertTrue(data['exit'])
                self.assertEqual(data['http_calls'],1)
                self.assertTrue((Path(directory)/'quote-entry-sequence.json').exists())
            self.assertFalse((Path(directory)/'quote-entry-sequence.json').exists())
            self.assertTrue(self.child(directory,claim_only=True)['admitted'])

    def test_owner_keeps_quota_and_background_cannot_jump_between_legs(self):
        with tempfile.TemporaryDirectory() as directory,self.configure(Path(directory)):
            http=FakeHttp()
            with patch.object(transport.LOCAL,'http',http,create=True):
                with transport.entry_sequence() as admitted:
                    self.assertTrue(admitted)
                    self.assertIsNotNone(transport.quote(transport.USDC,MINT,200))
                    self.assertIsNone(transport.quote(transport.USDC,MINT,201,purpose='background'))
                    self.assertEqual(transport.last_error()['code'],'ENTRY_SEQUENCE_BUSY')
                    self.assertIsNotNone(transport.quote(MINT,transport.USDC,202,purpose='entry'))
                    self.assertIsNotNone(transport.quote(transport.USDC,MINT,203))
                self.assertEqual(len(http.calls),3)

    def test_real_three_leg_preflight_retains_shared_interval_and_fresh_final(self):
        class Clock:
            ms=transport.now_ms()
            elapsed=0.
            def sleep(self,seconds):
                self.ms+=round(seconds*1000)
                self.elapsed+=seconds
        clock=Clock()
        class PreflightHttp(FakeHttp):
            def get(self,url,**kwargs):
                self.calls.append({'at':clock.ms,**kwargs})
                # An unrelated probe arriving mid-bundle cannot spend quota,
                # even in the owner's process and thread.
                assert transport.quote(transport.USDC,'C'*44,10,purpose='background') is None
                assert transport.last_error()['code']=='ENTRY_SEQUENCE_BUSY'
                params=kwargs['params']
                data=raw_quote(params['inputMint'],params['outputMint'],int(params['amount']))
                if params['inputMint']==MINT:
                    data.update(outAmount='199000000',otherAmountThreshold='197010000')
                clock.sleep(.01)
                class Response:
                    status_code=200
                    def raise_for_status(self):pass
                    def json(self):return copy.deepcopy(data)
                return Response()
        with tempfile.TemporaryDirectory() as directory,self.configure(Path(directory)), \
                patch.object(transport,'INTERVAL',2.10), \
                patch.object(transport.time,'time',side_effect=lambda:clock.ms/1000), \
                patch.object(transport.time,'monotonic',side_effect=lambda:clock.elapsed), \
                patch.object(transport.time,'sleep',side_effect=clock.sleep), \
                patch.object(execution,'SIMULATED_DELAY_MS',250):
            http=PreflightHttp()
            with patch.object(transport.LOCAL,'http',http,create=True):
                entry,preview=execution.prepare_entry(MINT,PAIR,200)
            self.assertEqual(len(http.calls),3)
            self.assertTrue(all(b['at']-a['at']>=2100
                                for a,b in zip(http.calls,http.calls[1:])))
            self.assertLessEqual(entry['quoted_at']-preview['quoted_at'],4000)
            self.assertEqual(clock.ms-entry['quoted_at'],250)
            self.assertEqual(entry['token_raw_amount'],99_900_000)
            self.assertEqual(execution.last_preparation_error(),{})

    def test_crashed_or_expired_owner_cannot_hold_or_reclaim_the_quota(self):
        with tempfile.TemporaryDirectory() as directory,self.configure(Path(directory)):
            path=Path(directory)/'quote-entry-sequence.json'
            path.write_text(json.dumps({'token':'crashed-owner','expires_at':transport.now_ms()-1}))
            self.assertTrue(self.child(directory,claim_only=True)['admitted'])
            with transport.entry_sequence() as admitted:
                self.assertTrue(admitted)
                expires=transport.LOCAL.entry_sequence_expires_at
                with patch.object(transport,'now_ms',return_value=expires):
                    self.assertIsNone(transport.quote(transport.USDC,MINT,123))
                    self.assertEqual(transport.last_error()['code'],'ENTRY_SEQUENCE_EXPIRED')

    def test_pending_exit_or_unreadable_lease_refuses_entry_without_http(self):
        with tempfile.TemporaryDirectory() as directory,self.configure(Path(directory)):
            root=Path(directory)
            priority=root/'quote-exit-priority';priority.mkdir()
            (priority/'pending.json').write_text('{}')
            with transport.entry_sequence() as admitted:self.assertFalse(admitted)
            self.assertEqual(transport.last_error()['code'],'EXIT_PRIORITY_PENDING')
            (priority/'pending.json').unlink()
            (root/'quote-entry-sequence.json').write_text('not-json')
            with transport.entry_sequence() as admitted:self.assertFalse(admitted)
            self.assertEqual(transport.last_error()['code'],'ENTRY_SEQUENCE_STATE_UNAVAILABLE')

    def test_failed_preflight_releases_reservation(self):
        with tempfile.TemporaryDirectory() as directory,self.configure(Path(directory)), \
                patch.object(execution,'entry_quote',return_value=None):
            self.assertIsNone(execution.prepare_entry(MINT,PAIR,200))
            self.assertFalse((Path(directory)/'quote-entry-sequence.json').exists())
            self.assertEqual(execution.last_preparation_error()['stage'],'initial_buy')
            self.assertEqual(execution.last_preparation_error()['code'],'QUOTE_UNAVAILABLE')


class PreparationFailureTests(unittest.TestCase):
    def fixtures(self):
        first={'token_raw_amount':100000,'input_usdc_raw':200000000,
               'quoted_at':NOW-6000,'context_slot':100,'raw_quote':{}}
        final={**first,'quoted_at':NOW,'context_slot':112}
        sale={'quoted_at':NOW-2100,'provider_expected_usdc':198,
              'expected_usdc':197.8,'floor_usdc':196,'raw_quote':{}}
        return first,sale,final

    def test_each_existing_consistency_threshold_reports_its_actual_failure(self):
        cases=[('PREFLIGHT_QUANTITY_DRIFT',lambda f,s,q:q.update(token_raw_amount=99000)),
               ('FINAL_QUOTE_STALE',lambda f,s,q:q.update(quoted_at=NOW-751)),
               ('PREFLIGHT_PREVIEW_STALE',lambda f,s,q:s.update(quoted_at=NOW-4360)),
               ('PREFLIGHT_SLOT_DRIFT',lambda f,s,q:q.update(context_slot=126)),
               ('PREFLIGHT_POSITIVE_RETURN',lambda f,s,q:s.update(provider_expected_usdc=206))]
        for code,mutate in cases:
            with self.subTest(code=code):
                first,sale,final=self.fixtures();mutate(first,sale,final)
                self.assertFalse(execution.consistent_preflight(first,sale,final,NOW))
                with patch.object(transport,'entry_sequence',return_value=nullcontext(True)), \
                        patch.object(execution,'stamp',return_value=NOW), \
                        patch.object(execution,'entry_quote',side_effect=[first,final]), \
                        patch.object(execution,'exit_quote',return_value=sale):
                    self.assertIsNone(execution.prepare_entry(MINT,PAIR,200))
                error=execution.last_preparation_error()
                self.assertEqual((error['stage'],error['code']),('consistency',code))
                if code=='PREFLIGHT_PREVIEW_STALE':self.assertEqual(error['preview_age_ms'],4360)

    def test_transport_failure_is_retained_at_the_exact_preflight_leg(self):
        with patch.object(transport,'entry_sequence',return_value=nullcontext(True)), \
                patch.object(transport,'quote',return_value=None), \
                patch.object(transport,'last_error',return_value={'code':'RATE_LIMITED','retry_at':NOW+15000}):
            self.assertIsNone(execution.prepare_entry(MINT,PAIR,200))
        error=execution.last_preparation_error()
        self.assertEqual((error['stage'],error['code']),('initial_buy','RATE_LIMITED'))
        self.assertEqual(error['retry_at'],NOW+15000)

    def test_exact_pool_route_failure_is_distinct_from_provider_failure(self):
        quote=raw_quote(transport.USDC,MINT,200000000)
        quote['_received_at']=NOW
        quote['routePlan'][0]['swapInfo']['ammKey']='C'*44
        with patch.object(transport,'entry_sequence',return_value=nullcontext(True)), \
                patch.object(transport,'quote',return_value=quote), \
                patch.object(execution,'stamp',return_value=NOW):
            self.assertIsNone(execution.prepare_entry(MINT,PAIR,200))
        self.assertEqual(execution.last_preparation_error()['code'],'ENTRY_POOL_MISMATCH')

    def test_success_clears_previous_failure_and_preserves_original_evidence(self):
        first,sale,final=self.fixtures()
        with patch.object(transport,'entry_sequence',return_value=nullcontext(True)), \
                patch.object(execution,'stamp',return_value=NOW), \
                patch.object(execution,'entry_quote',side_effect=[None,first,final]), \
                patch.object(execution,'exit_quote',return_value=sale):
            self.assertIsNone(execution.prepare_entry(MINT,PAIR,200))
            entry,preview=execution.prepare_entry(MINT,PAIR,200)
        self.assertEqual(execution.last_preparation_error(),{})
        self.assertTrue(preview['is_preflight_estimate'])
        self.assertEqual(entry['preflight_buy_quote'],first['raw_quote'])
        self.assertEqual(entry['preflight_sell_quote'],sale['raw_quote'])
        self.assertEqual(entry['preflight_quantity_adjustment'],1)

    def test_nonfinite_preview_cannot_be_a_successful_consistency_check(self):
        first,sale,final=self.fixtures()
        sale['provider_expected_usdc']=float('nan')
        self.assertEqual(execution.preflight_failure(first,sale,final,NOW)['code'],'PREFLIGHT_INVALID')


if __name__=='__main__':unittest.main()
