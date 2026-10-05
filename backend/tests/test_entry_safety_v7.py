import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import engine_rug_guard as rug
import pair_price_integrity as price

MINT='F4635H9HFAzYjpyM3coGMtW8pMmEYRtwrgz5mgfHpump'
PAIR='8DcTcUgENxjLiUr2YkR2BJHcpWj1epd16dtsWm2wJNkg'
SOL='So11111111111111111111111111111111111111112'


def account():
    return {
        'owner':'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
        'data':{'parsed':{'type':'mint','info':{
            'isInitialized':True,'supply':'1000000000','mintAuthority':None,
            'freezeAuthority':None,'decimals':6,'extensions':[],
        }}},
    }


def report(insiders=0, lp=100.0, score=1.0, top=(2.0,1.8,1.6,1.4,1.2)):
    holders=[
        {'address':f'holder{i}','owner':f'owner{i}','pct':pct}
        for i,pct in enumerate(top)
    ]
    return {
        'mint':MINT,'rugged':False,'risks':[],'graphInsidersDetected':insiders,
        'score':score,'score_normalised':score,'detectedAt':'2026-10-05T00:00:00Z',
        'markets':[{'pubkey':PAIR,'mintA':MINT,'mintB':SOL,
                    'lp':{'lpLockedPct':lp,'quoteMint':SOL,'quotePrice':120.0}}],
        'topHolders':holders,
    }


class RugGuardV2Tests(unittest.TestCase):
    def test_small_clean_insider_cluster_is_not_automatic_block(self):
        result=rug.assess(MINT,PAIR,account(),report(insiders=6))
        self.assertEqual(result['status'],'pass')
        self.assertEqual(result['metrics']['reported_linked_insiders_count'],6)

    def test_extreme_cluster_stays_blocked(self):
        result=rug.assess(MINT,PAIR,account(),report(insiders=444))
        self.assertEqual(result['status'],'blocked')
        self.assertIn('reported_linked_insiders',result['reasons'])

    def test_medium_cluster_needs_exceptionally_clean_report(self):
        clean=rug.assess(MINT,PAIR,account(),report(insiders=20,lp=100,score=1))
        self.assertEqual(clean['status'],'pass')
        weak=rug.assess(MINT,PAIR,account(),report(insiders=20,lp=98,score=1))
        self.assertEqual(weak['status'],'blocked')
        self.assertIn('reported_linked_insiders',weak['reasons'])


class PriceCrosscheckV2Tests(unittest.TestCase):
    def ref(self, reference):
        return {'mint':MINT,'pair':PAIR,'received_at':1_000_000,'price_usd':reference}

    def coin(self, observed):
        return {'address':MINT,'pairAddress':PAIR,'priceUsd':observed}

    def test_normal_divergence_passes(self):
        result=price.validate(self.coin(1.04),self.ref(1.0),1_000_100)
        self.assertEqual(result['status'],'pass')

    def test_moderate_divergence_requests_jupiter_review(self):
        result=price.validate(self.coin(1.06),self.ref(1.0),1_000_100)
        self.assertEqual(result['status'],'review')
        self.assertEqual(result['reason'],'price_source_disagreement_needs_jupiter')

    def test_large_divergence_stays_blocked(self):
        result=price.validate(self.coin(1.09),self.ref(1.0),1_000_100)
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['reason'],'price_source_disagreement')

    def test_jupiter_can_confirm_observed_exact_pool_price(self):
        review=price.validate(self.coin(1.06),self.ref(1.0),1_000_100)
        result=price.jupiter_tiebreak(review,1.08)
        self.assertEqual(result['status'],'pass')
        self.assertTrue(result['jupiter_tiebreak'])

    def test_jupiter_tiebreak_fails_when_far_from_observed_price(self):
        review=price.validate(self.coin(1.06),self.ref(1.0),1_000_100)
        result=price.jupiter_tiebreak(review,1.10)
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['reason'],'price_tiebreak_failed')


if __name__=='__main__':
    unittest.main()
