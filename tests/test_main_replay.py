import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from main_replay import MainReplay
import engine_execution as execution
import promoted_entry_guard as promoted_guard

A,B='A'*44,'B'*44
NOW=1000000


def observation(at):
    return {'id':str(at),'available_at':at,'observed_at':at,
        'coin':{'address':A,'pairAddress':B,'symbol':'FIXTURE','priceUsd':2,'priceNative':.02,
                'liquidityUsd':200000,'marketCap':1000000,'ageMinutes':60,'score':95,
                'priceChange':{'m5':5,'h1':8},'txns':{'m5':{'buys':10,'sells':3}},
                'volume':{'h1':50000},'signals':[],'updatedAt':at},
        'flow':{'quality':'COMPLETE','fresh':True,'latest_at':at,'trades':4,
                'buy_sell_usd_ratio':3,'unique_wallets':3,'buyer_wallets':3,
                'buy_usd':300,'sell_usd':100,'max_sell_usd':50,
                'verified_flow':{'source':promoted_guard.FLOW_SOURCE,
                    'coverage_status':'COMPLETE','window_ms':promoted_guard.FLOW_WINDOW_MS,
                    'address':A,'pairAddress':B,'window_at':at,'latest_event_at':at-100,
                    'available_at':at-50,'trades':4,'unique_wallets':3,
                    'buy_usd':300,'sell_usd':100}},
        'safety':{'evidence':{'status':'pass','mint':A,'pair':B,'checked_at':at,
                    'metrics':{'decimals':6,'sol_usd':100,'token_account_rent_lamports':1650000}}},
        'source':{'price_evidence':{'status':'pass','mint':A,'pair':B,'reference_received_at':at},'execution_evidence':{}}}


def raw_quote(inp,out,amount,output,at):
    return {'inputMint':inp,'outputMint':out,'inAmount':str(amount),'outAmount':str(output),
            'otherAmountThreshold':str(output-1000),'swapMode':'ExactIn','_received_at':at,
            'routePlan':[{'swapInfo':{'inputMint':inp,'outputMint':out,'ammKey':B}}]}


def production_preflight(final_output=99_600_000):
    """Exercise the actual production preparation, on synthetic raw quotes."""
    usdc=execution.USDC
    def buy(output,at,slot):
        raw=raw_quote(usdc,A,200_000_000,output,at)
        raw.update(otherAmountThreshold=str(output*99//100),contextSlot=slot,
                   _simulated_fill_at=at+50)
        return {'input_usdc_raw':200_000_000,'token_raw_expected':output,
                'token_raw_amount':output*(10000-execution.BUFFER_BPS)//10000,
                'token_raw_floor':int(raw['otherAmountThreshold']),
                'price_impact_pct':.1,'quoted_at':at,'simulated_fill_at':at+50,
                'assumed_buffer_bps':execution.BUFFER_BPS,'context_slot':slot,'raw_quote':raw}
    first=buy(100_000_000,NOW-700,100)
    final=buy(final_output,NOW-200,102)
    sale_raw=raw_quote(A,usdc,first['token_raw_amount'],199_000_000,NOW-450)
    sale_raw.update(otherAmountThreshold='198000000',_simulated_fill_at=NOW-400)
    sale={'provider_expected_usdc':199.,'expected_usdc':199*(1-execution.BUFFER_BPS/10000),
          'floor_usdc':198.,'quoted_at':NOW-450,'simulated_fill_at':NOW-400,
          'token_input_raw':first['token_raw_amount'],'raw_quote':sale_raw}
    # Capture the function before MainReplay patches its symbol. Its globals
    # still resolve to controlled quote adapters for this correctness fixture.
    prepare=execution.prepare_entry
    with patch.object(execution,'entry_quote',side_effect=[copy.deepcopy(first),copy.deepcopy(final)]), \
            patch.object(execution,'exit_quote',return_value=copy.deepcopy(sale)), \
            patch.object(execution,'stamp',return_value=NOW):
        result=prepare(A,B,200)
    assert result is not None
    return result


class MainReplayTests(unittest.TestCase):
    def test_actual_production_preflight_adjustment_replays_final_fill_not_initial(self):
        entry,sale=production_preflight()
        self.assertNotEqual(int(sale['raw_quote']['inAmount']),entry['token_raw_amount'])
        self.assertAlmostEqual(entry['preflight_quantity_adjustment'],.996)
        with tempfile.TemporaryDirectory() as tmp:
            first=observation(NOW)
            first['coin']['updatedAt']=NOW-900
            first['source']['execution_evidence']={'entry':entry,'exit':sale}
            last=observation(NOW+1000)
            last['source']['execution_evidence']['mark']={
                'token_input_raw':entry['token_raw_amount'],'net_proceeds_usd':160,
                'gross_proceeds_usd':160.03,'network_fee_usd':.03,'dex_fee_usd':0,
                'fill_price':1.6,'impact_pct':.2,'quoted_at':NOW+1000,'from_cache':False,
                'execution_source':'SYNTHETIC_TEST_ONLY',
                'raw_quote':raw_quote(A,execution.USDC,entry['token_raw_amount'],160030000,NOW+1000)}
            with MainReplay(Path(tmp)) as replay:
                result=replay.replay([first,last])
                self.assertEqual(result['stats']['closed_trades'],1)
                trade=result['history'][0]
                self.assertEqual(trade['jupiter_token_raw_amount'],entry['token_raw_amount'])
                self.assertLess(trade['jupiter_token_raw_amount'],int(entry['preflight_buy_quote']['outAmount']))
                self.assertLess(trade['pnl_usd'],-40)

    def test_preflight_quantity_increase_never_increases_preview(self):
        entry,sale=production_preflight(final_output=100_200_000)
        self.assertEqual(entry['preflight_quantity_adjustment'],1)
        with tempfile.TemporaryDirectory() as tmp:
            with MainReplay(Path(tmp)) as replay:
                replay.row=observation(NOW);replay.now=NOW
                replay.row['source']['execution_evidence']={'entry':entry,'exit':sale}
                checked=replay.prepare(A,B,200)
                self.assertIsNotNone(checked)
                self.assertAlmostEqual(checked[1]['expected_usdc'],199*(1-execution.BUFFER_BPS/10000))

    def test_recorded_jupiter_tiebreak_without_gecko_timestamp_requires_matching_quote(self):
        entry,sale=production_preflight()
        with tempfile.TemporaryDirectory() as tmp:
            with MainReplay(Path(tmp)) as replay:
                replay.row=observation(NOW);replay.now=NOW
                replay.row['source']['execution_evidence']={'entry':entry,'exit':sale}
                proof=replay.row['source']['price_evidence']
                proof.update(reference_received_at=None,jupiter_tiebreak=True,observed_price=2,
                             jupiter_entry_price=200/(entry['token_raw_amount']/1e6))
                self.assertEqual(replay.price(None)['status'],'pass')
                proof['jupiter_entry_price']+=1
                self.assertEqual(replay.price(None)['status'],'unavailable')
                proof['jupiter_entry_price']=200/(entry['token_raw_amount']/1e6)
                entry['raw_quote']['_received_at']=NOW+1
                self.assertEqual(replay.price(None)['status'],'unavailable')

    def test_preflight_tamper_drift_future_or_chronology_never_books(self):
        original=production_preflight()
        cases={
            'inflated_adjustment':lambda e,s:e.update(preflight_quantity_adjustment=1),
            'inflated_preview':lambda e,s:s.update(expected_usdc=s['expected_usdc']+1),
            'unadjusted_floor':lambda e,s:s.update(floor_usdc=198),
            'future_initial':lambda e,s:e['preflight_buy_quote'].update(_received_at=NOW+1),
            'sale_before_initial_landed':lambda e,s:e['preflight_buy_quote'].update(_simulated_fill_at=NOW-400),
            'sale_lands_after_final_quote':lambda e,s:s['raw_quote'].update(_simulated_fill_at=NOW-100),
            'future_final_landing':lambda e,s:e['raw_quote'].update(_simulated_fill_at=NOW+1),
            'preflight_input_mutated':lambda e,s:s['raw_quote'].update(inAmount='100000000'),
            'missing_first':lambda e,s:e.pop('preflight_buy_quote'),
            'wrong_initial_pool':lambda e,s:e['preflight_buy_quote']['routePlan'][0]['swapInfo'].update(ammKey='C'*44),
            'too_much_final_drift':lambda e,s:e.update(token_raw_amount=90_000_000),
        }
        with tempfile.TemporaryDirectory() as tmp:
            with MainReplay(Path(tmp)) as replay:
                for name, mutate in cases.items():
                    with self.subTest(case=name):
                        entry,sale=copy.deepcopy(original)
                        mutate(entry,sale)
                        replay.row=observation(NOW);replay.now=NOW
                        replay.row['source']['execution_evidence']={'entry':entry,'exit':sale}
                        self.assertIsNone(replay.prepare(A,B,200))
                self.assertFalse(replay.market.STATE.positions)

    def test_actual_path_entry_exit_restart_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            first=observation(NOW)
            usdc='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
            first['source']['execution_evidence']={
                'entry':{'token_raw_expected':100000000,'token_raw_amount':100000000,
                         'input_usdc_raw':200000000,'price_impact_pct':.2,'quoted_at':NOW,
                         'raw_quote':raw_quote(usdc,A,200000000,100000000,NOW)},
                'exit':{'expected_usdc':199,'floor_usdc':198,'quoted_at':NOW,
                        'raw_quote':raw_quote(A,usdc,100000000,199000000,NOW)}}
            last=observation(NOW+1000)
            last['source']['execution_evidence']['mark']={
                'token_input_raw':100000000,'net_proceeds_usd':160,'gross_proceeds_usd':160.03,
                'network_fee_usd':.03,'dex_fee_usd':0,'fill_price':1.6,'impact_pct':.2,
                'slippage_pct':.1,'latency_pct':0,'quoted_at':NOW+1000,'from_cache':False,
                'execution_source':'SYNTHETIC_TEST_ONLY',
                'raw_quote':raw_quote(A,usdc,100000000,160030000,NOW+1000)}
            with MainReplay(Path(tmp)) as replay:
                result=replay.replay([first,last])
                self.assertEqual(result['stats']['closed_trades'],1)
                self.assertLess(result['history'][0]['pnl_usd'],-40)
                restored=replay.market.State()
                self.assertEqual(round(restored.demo_balance_usd,2),result['stats']['demo_balance_usd'])
                self.assertEqual(len(restored.history),1)

    def test_missing_quote_no_fake_fill_and_future_availability_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with MainReplay(Path(tmp)) as replay:
                row=observation(NOW)
                replay.ingest(row)
                self.assertFalse(replay.market.STATE.positions)
                row=observation(NOW+1)
                row['observed_at']=NOW+2
                self.assertFalse(replay.ingest(row))
                self.assertEqual(replay.unusable,1)

    def test_foreign_mint_missing_raw_or_future_quote_never_liquidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            with MainReplay(Path(tmp)) as replay:
                usdc=replay.market.paper_quotes.USDC
                position={'address':A,'pairAddress':B,'jupiter_token_raw_amount':100000000}
                q={'token_input_raw':100000000,'quoted_at':NOW,
                   'raw_quote':raw_quote(A,usdc,100000000,160000000,NOW)}
                replay.row=observation(NOW);replay.now=NOW
                replay.row['source']['execution_evidence']={'mark':q}
                self.assertIsNotNone(replay.mark(position,None,0))
                replay.row['coin']['address']='C'*44
                self.assertIsNone(replay.mark(position,None,0))
                replay.row['coin']['address']=A
                q['raw_quote']['_received_at']=NOW+1
                self.assertIsNone(replay.mark(position,None,0))
                q['raw_quote']['_received_at']=NOW
                del q['token_input_raw']
                self.assertIsNone(replay.mark(position,None,0))

    def test_unknown_quality_and_future_price_evidence_stay_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            with MainReplay(Path(tmp)) as replay:
                replay.row=observation(NOW);replay.now=NOW
                del replay.row['flow']['quality']
                self.assertNotEqual(replay.flow().get('quality'),'COMPLETE')
                replay.row['source']['price_evidence']['reference_received_at']=NOW+1
                self.assertEqual(replay.price(None)['status'],'unavailable')


if __name__=='__main__':
    unittest.main()
