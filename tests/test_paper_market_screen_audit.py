import copy
import math
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
import funded_active_paper as active
from lab_dashboard_projection import compact_strategy_lab


def original_matches(sid, coin):
    """Characterization of the pre-audit predicate, independent of the new helper."""
    rule=active.RULES.get(sid)
    if not rule or str(coin.get('dexId') or '').lower()!='pumpswap': return False
    quote=coin.get('quoteToken')
    if coin.get('quoteTokenAddress')!=active.SOL or (quote is not None and (
            not isinstance(quote,dict) or quote.get('address',active.SOL)!=active.SOL)): return False
    tx=(coin.get('txns') or {}).get('m5') or {}
    changes,volume=coin.get('priceChange') or {},coin.get('volume') or {}
    cap=coin.get('marketCap')
    if cap is None: cap=coin.get('fdv')
    values=[active.finite(v,math.nan) for v in (coin.get('ageMinutes'),coin.get('liquidityUsd'),cap,
        changes.get('m5'),changes.get('h1'),tx.get('buys'),tx.get('sells'),volume.get('h1'))]
    if not all(math.isfinite(v) for v in values): return False
    age,liq,cap,move,hour,buys,sells,turnover=values
    if cap<=0 or liq<=0 or min(buys,sells,turnover)<0 or buys!=int(buys) or sells!=int(sells): return False
    return (rule['age'][0]<=age<=rule['age'][1] and liq>=rule['liquidity']
        and rule['move'][0]<=move<=rule['move'][1] and rule['hour'][0]<=hour<=rule['hour'][1]
        and buys/max(sells,1)>=rule['buy_sell'] and rule['liquidity_cap']<=liq/cap<1
        and rule['turnover'][0]<=turnover/liq<=rule['turnover'][1])


def fixture():
    return dict(symbol='TEST',address='mint',pairAddress='pool',dexId='pumpswap',
        quoteTokenAddress=active.SOL,ageMinutes=30,liquidityUsd=400000,marketCap=10000000,
        priceChange=dict(m5=2,h1=5),txns=dict(m5=dict(buys=60,sells=20)),volume=dict(h1=60000))


class MarketScreenAuditTests(unittest.TestCase):
    def test_same_admission_on_2000_generated_market_rows_per_strategy(self):
        rng=random.Random(1337)
        values=[None,True,False,'bad','3.5',-1,0,.5,1,2,5,10,15,20,40,60,120,
                100000,150000,250000,400000,10000000,float('nan'),float('inf')]
        for _ in range(2000):
            coin=fixture()
            for _ in range(rng.randrange(1,4)):
                field=rng.choice(['ageMinutes','liquidityUsd','marketCap','m5','h1','buys','sells','turnover','dex','quote'])
                value=rng.choice(values)
                if field in ('m5','h1'): coin['priceChange'][field]=value
                elif field in ('buys','sells'): coin['txns']['m5'][field]=value
                elif field=='turnover': coin['volume']['h1']=value
                elif field=='dex': coin['dexId']=rng.choice(['pumpswap','PUMPSWAP','raydium',None])
                elif field=='quote': coin['quoteToken']=rng.choice([None,{},[],dict(address=active.SOL),dict(address='other')])
                else: coin[field]=value
            for sid in active.RULES:
                self.assertEqual(active.matches(sid,coin),original_matches(sid,coin),(sid,coin))

    def test_all_constraints_explain_without_losing_other_failures(self):
        coin=fixture();coin.update(ageMinutes=1000,liquidityUsd=10000)
        self.assertEqual(active.market_rejections('EARLY',coin),['market_age','market_liquidity','market_liquidity_cap'])
        for sid in active.RULES: self.assertEqual(active.market_rejections(sid,fixture()),[])

    def test_malformed_nested_data_fails_closed_instead_of_crashing_scan(self):
        for field in ('txns','priceChange','volume'):
            coin=fixture();coin[field]=['malformed']
            self.assertFalse(active.matches('MOMENTUM',coin))
            self.assertIn('market_input_invalid',active.market_rejections('MOMENTUM',coin))
        coin=fixture();coin['txns']['m5']='bad'
        self.assertFalse(active.matches('MOMENTUM',coin))
        self.assertFalse(active.matches('UNKNOWN',fixture()))
        self.assertFalse(active.matches('MOMENTUM',None))

    def test_primary_counts_reconcile_and_overlapping_constraints_do_not(self):
        summary=active.new_market_screen()
        for _ in range(10): active.record_market_screen(summary,fixture(),['market_age','market_liquidity'])
        active.record_market_screen(summary,fixture(),[])
        self.assertEqual(summary['evaluated'],11)
        self.assertEqual(summary['passed']+summary['rejected'],11)
        self.assertEqual(sum(summary['primary_rejections'].values()),10)
        self.assertEqual(sum(summary['failed_constraints'].values()),20)
        self.assertEqual(len(summary['examples']),4)
        self.assertFalse(summary['is_entry_authorization'])
        projected=compact_strategy_lab({'books':{'EARLY':{'entry_diagnostics':{'market_screen':summary}}}})
        self.assertEqual(projected['books']['EARLY']['entry_diagnostics']['market_screen'],summary)

    def test_screen_does_not_mutate_coin(self):
        coin=fixture();before=copy.deepcopy(coin)
        active.market_rejections('EARLY',coin)
        self.assertEqual(coin,before)


if __name__=='__main__': unittest.main()
