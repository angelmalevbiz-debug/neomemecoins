"""Read-only fake HTTP through the real shared transport and account clients."""
import copy
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import astra6_brain as astra
import engine_execution as main
import honest_quote_transport as transport

MINT='HKZDfZnkHZxd9agRDNPyDv4iT6LmAurJnpRtj9wpump'
PAIR='867dKvaCcyRrUDP66bqNXbujXfjaxsNB9wTpc4CmBdAD'
USDC=transport.USDC
NOW=1_800_000_000_000


class Clock:
    def __init__(self): self.ms=transport.now_ms(); self.elapsed=0.
    def sleep(self,seconds): self.ms+=round(seconds*1000); self.elapsed+=seconds


def response_quote(inp,out,raw,result,slippage,received=NOW):
    return {'inputMint':inp,'outputMint':out,'inAmount':str(raw),
        'outAmount':str(result),'otherAmountThreshold':str(result*995//1000),
        'swapMode':'ExactIn','priceImpactPct':'0.001','slippageBps':slippage,
        'contextSlot':123,'routePlan':[{'swapInfo':{'ammKey':PAIR,
            'inputMint':inp,'outputMint':out,'inAmount':str(raw),'outAmount':str(result)}}],
        '_received_at':received,'_http_ms':10,'_cache_hit':False}


class Response:
    def __init__(self,status,data): self.status_code=status; self.data=data; self.headers={'Retry-After':'15'}
    def raise_for_status(self): pass
    def json(self): return copy.deepcopy(self.data)


class Http:
    def __init__(self,clock,root,status=200):
        self.clock=clock; self.root=root; self.status=status; self.calls=[]; self.sales=0
    def get(self,url,**kwargs):
        params=kwargs['params']
        self.calls.append({'at':self.clock.ms,'params':dict(params),'url':url,
            'exit_markers':len(list((self.root/'quote-exit-priority').glob('*.json'))),
            'timeout':kwargs.get('timeout')})
        self.clock.sleep(.01)
        if params['inputMint']==MINT:
            self.sales+=1
            result=74_750_000 if self.sales==1 else 68_000_000
        else: result=int(params['amount'])
        return Response(self.status,response_quote(params['inputMint'],params['outputMint'],
            int(params['amount']),result,params['slippageBps']))


class SharedAccountQuotes(unittest.TestCase):
    def setup_transport(self,stack,root,http,clock):
        stack.enter_context(patch.multiple(transport,ROOT=root,LOCK=root/'shared.lock',
            STAMP=root/'shared-stamp.txt',COOLDOWN=root/'shared-backoff.json',INTERVAL=2.10))
        stack.enter_context(patch.object(transport.LOCAL,'http',http,create=True))
        stack.enter_context(patch.object(transport.time,'time',side_effect=lambda:clock.ms/1000))
        stack.enter_context(patch.object(transport.time,'monotonic',side_effect=lambda:clock.elapsed))
        stack.enter_context(patch.object(transport.time,'sleep',side_effect=clock.sleep))
        stack.enter_context(patch.object(main,'SIMULATED_DELAY_MS',0))
        original_write=transport._write
        def write_at_virtual_time(path,obj):
            original_write(path,obj)
            # Keep marker expiration in the same clock domain as request waits.
            os.utime(path,(clock.ms/1000,clock.ms/1000))
        stack.enter_context(patch.object(transport,'_write',side_effect=write_at_virtual_time))

    def test_main_and_custom_astra_share_budget_and_exit_cannot_reuse_old_price(self):
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            root=Path(directory)/'quotes'; root.mkdir()
            account=Path(directory)/'custom-astra-ledger'
            stack.enter_context(patch.object(astra,'ROOT',account))
            clock=Clock(); http=Http(clock,root); self.setup_transport(stack,root,http,clock)
            client=astra.Client()
            client.http.get=Mock(side_effect=AssertionError('Astra must use the shared HTTP adapter'))
            stack.enter_context(patch.object(client,'mint',return_value={'decimals':6,'rent_sol':.0015}))
            bought=main.entry_quote(MINT,PAIR,75)
            self.assertIsNotNone(bought)
            buy,_=client.buy({'address':MINT,'pairAddress':PAIR},75)
            raw=astra.simulated_raw(buy)
            self.assertEqual(raw,bought['token_raw_amount'])
            preview=client.sell(MINT,PAIR,raw,'entry')
            clock.sleep(.1)
            cached=client.sell(MINT,PAIR,raw,'entry')
            self.assertTrue(cached['_cache_hit'])
            self.assertEqual(cached['_at'],preview['_at'])
            self.assertEqual(cached['_at'],cached['_received_at'])
            self.assertLess(cached['_at'],astra.now_ms())
            self.assertEqual(len(http.calls),3)
            # A cached favorable preview remains within the transport cache TTL,
            # but an actual held-position sale must get post-decision evidence.
            exited=client.sell(MINT,PAIR,raw)
            self.assertGreater(exited['_at'],preview['_at'])
            self.assertFalse(exited['_cache_hit'])
            self.assertEqual(exited['outAmount'],'68000000')
            self.assertLess(astra.proceeds(exited,.012)['net_proceeds_usd'],69)
            self.assertEqual(http.calls[-1]['exit_markers'],1)
            self.assertEqual([c['exit_markers'] for c in http.calls[:3]],[0,0,0])
            primary_exit=main.exit_quote(MINT,raw,PAIR,force=True)
            self.assertIsNotNone(primary_exit)
            self.assertEqual(http.calls[-1]['exit_markers'],1)
            self.assertTrue(all(b['at']-a['at']>=2100 for a,b in zip(http.calls,http.calls[1:])))
            self.assertEqual(client.requests,3) # Two sales and one buy; cache reuse is not HTTP.
            self.assertFalse(account.exists())
            client.http.get.assert_not_called()

    def test_adapter_failure_is_explicit_for_astra_and_main(self):
        for status,code in [(401,'AUTHENTICATION_REQUIRED'),(403,'ACCESS_DENIED'),(429,'RATE_LIMITED')]:
            with self.subTest(status=status),tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
                root=Path(directory); clock=Clock(); http=Http(clock,root,status)
                self.setup_transport(stack,root,http,clock); client=astra.Client()
                with self.assertRaisesRegex(astra.QuoteError,code): client.sell(MINT,PAIR,1)
                self.assertEqual(client.last_quote_error['code'],code)
                self.assertEqual(client.errors,1)
                self.assertEqual(client.requests,1)
                self.assertIsNone(main.exit_quote(MINT,1,PAIR,force=True))
                if status==429:
                    self.assertEqual(len(http.calls),1) # Shared cooldown forbids another HTTP request.
                    self.assertGreater(json.loads((root/'shared-backoff.json').read_text())['until'],clock.ms/1000)
                    self.assertEqual(transport.last_error()['code'],'QUOTE_BUDGET_TIMEOUT')
                else:
                    self.assertEqual(len(http.calls),2)
                    self.assertEqual(transport.last_error()['code'],code)

    def test_sale_passes_exit_priority_and_minimum_receipt_to_same_adapter_as_main(self):
        def quoted(inp,out,raw,**kwargs):
            return response_quote(inp,out,raw,75_000_000,kwargs['slippage_bps'])
        with patch.object(transport,'quote',side_effect=quoted) as shared,patch.object(astra,'now_ms',return_value=NOW),patch.object(main,'stamp',return_value=NOW),patch.object(main,'SIMULATED_DELAY_MS',0):
            client=astra.Client(); client.sell(MINT,PAIR,74_925_000)
            main.exit_quote(MINT,74_925_000,PAIR,force=True)
            self.assertEqual(len(shared.call_args_list),2)
            for call in shared.call_args_list:
                self.assertEqual(call.kwargs['purpose'],'exit')
                self.assertEqual(call.kwargs['min_received_at'],NOW)
            self.assertEqual(shared.call_args_list[0].kwargs['slippage_bps'],50)
            self.assertEqual(shared.call_args_list[1].kwargs['slippage_bps'],main.SLIPPAGE_BPS)

    def test_sale_rejects_provider_that_returns_predecision_cache_despite_lower_bound(self):
        old=response_quote(MINT,astra.USDC,100,75_000_000,50,received=NOW-100)
        old['_cache_hit']=True
        with patch.object(transport,'quote',return_value=old),patch.object(astra,'now_ms',return_value=NOW):
            client=astra.Client()
            with self.assertRaisesRegex(astra.QuoteError,'STALE_EXIT_QUOTE'): client.sell(MINT,PAIR,100)
            self.assertEqual(client.last_quote_error['code'],'STALE_EXIT_QUOTE')

    def test_background_reference_keeps_original_received_time(self):
        old=response_quote(astra.SOL,astra.USDC,1_000_000_000,120_000_000,50,received=NOW-500)
        old['_cache_hit']=True
        with patch.object(transport,'quote',return_value=old) as shared,patch.object(astra,'now_ms',return_value=NOW):
            client=astra.Client(); client.rpc_at=NOW; client.warm()
            self.assertEqual(client.sol_at,NOW-500)
            self.assertEqual(client.sol_usd,120)
            self.assertEqual(shared.call_args.kwargs['purpose'],'background')
            self.assertEqual(client.requests,0)

    def test_training_background_quote_has_bounded_http_time(self):
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            root=Path(directory); clock=Clock(); http=Http(clock,root)
            self.setup_transport(stack,root,http,clock)
            data=transport.quote(USDC,MINT,20_000_000,purpose='background')
            self.assertIsNotNone(data)
            self.assertEqual(http.calls[0]['timeout'],(.35,.65))

    def test_multilingual_state_survives_restart_as_utf8(self):
        book=astra.empty_book(); book['notes']='🚀 Кирилица 日本語'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'astra.json'
            astra.write(path,{'book':book})
            self.assertIn(book['notes'],path.read_bytes().decode('utf-8'))
            self.assertEqual(astra.read(path),{'book':book})
            restarted=astra.Brain(path)
            try: self.assertEqual(restarted.book,book)
            finally: restarted.executor.shutdown()


if __name__=='__main__': unittest.main()
