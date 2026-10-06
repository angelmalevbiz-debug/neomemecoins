import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

import brainstorm_engine as b


class BrainstormEngineTests(unittest.TestCase):
    def setUp(self):
        self.enabled = mock.patch.object(b, 'ENABLED', True)
        self.threshold = mock.patch.object(b, 'MIN_CONFIDENCE', 0.70)
        self.matches = mock.patch.object(b, 'MIN_MATCHES', 3)
        self.families = mock.patch.object(b, 'MIN_FAMILIES', 2)
        self.lab = mock.patch.object(b, '_lab_stats', return_value={})
        for patcher in (self.enabled, self.threshold, self.matches, self.families, self.lab):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def strong_coin():
        return {
            'score': 97,
            'liquidityUsd': 500_000,
            'marketCap': 2_000_000,
            'ageMinutes': 30,
            'priceChange': {'m5': 8},
            'txns': {'m5': {'buys': 30, 'sells': 10}},
        }

    @staticmethod
    def strong_matches():
        return [
            'FLOW_ELITE', 'MOMENTUM', 'LIQUIDITY', 'REVERSAL',
            'ULTRA_PRECISION', 'BREAKOUT', 'EARLY_FLOW', 'CONFLUENCE_MAX',
        ]

    def test_strong_multi_brain_consensus_can_pass(self):
        decision = b.evaluate_pre(
            self.strong_coin(), self.strong_matches(), [],
            safety={'status': 'pass'}, validation={'status': 'pass'}, now_ms=1000,
        )
        self.assertTrue(decision['allow'])
        self.assertGreaterEqual(decision['confidence'], 0.70)
        self.assertGreaterEqual(decision['family_count'], 4)
        self.assertEqual(decision['version'], b.VERSION)

    def test_low_consensus_is_vetoed_even_for_good_market(self):
        decision = b.evaluate_pre(
            self.strong_coin(), ['MOMENTUM'], [],
            safety={'status': 'pass'}, validation={'status': 'pass'}, now_ms=1000,
        )
        self.assertFalse(decision['allow'])
        self.assertIn('insufficient_strategy_consensus', decision['vetoes'])
        self.assertIn('insufficient_strategy_diversity', decision['vetoes'])

    def test_future_results_are_never_used_for_learning(self):
        history = [
            {'closed_at': 900, 'pnl_usd': -1, 'brainstorm': {'version': b.VERSION}, 'strategy_matches': ['MOMENTUM']},
            {'closed_at': 1100, 'pnl_usd': 10, 'brainstorm': {'version': b.VERSION}, 'strategy_matches': ['MOMENTUM']},
        ]
        actual = b._actual_stats(history, ['MOMENTUM'], 1000)
        self.assertEqual(actual['sample'], 1)
        self.assertEqual(actual['wins'], 0)
        self.assertEqual(actual['losses'], 1)

    def test_bad_execution_can_veto_good_pre_quote_signal(self):
        pre = b.evaluate_pre(
            self.strong_coin(), self.strong_matches(), [],
            safety={'status': 'pass'}, validation={'status': 'pass'}, now_ms=1000,
        )
        self.assertTrue(pre['allow'])
        post = b.evaluate_post(
            pre, immediate_roundtrip_pct=-2.70,
            worst_case_roundtrip_pct=-3.95, impact_pct=1.70,
        )
        self.assertFalse(post['allow'])
        self.assertIn('weak_execution_quality', post['vetoes'])

    def test_disabled_layer_is_noop(self):
        with mock.patch.object(b, 'ENABLED', False):
            decision = b.evaluate_pre({}, [], [], safety={}, validation={}, now_ms=1)
            self.assertTrue(decision['allow'])
            self.assertFalse(decision['enabled'])


    def test_bad_recent_run_raises_dynamic_threshold(self):
        history=[]
        # Recent BRAINSTORM outcomes are deliberately poor; threshold must tighten.
        for i in range(16):
            history.append({
                'closed_at': 2000-i, 'pnl_usd': -1 if i < 12 else 1,
                'brainstorm': {'version': b.VERSION}, 'brainstorm_version': b.VERSION,
                'brainstorm_confidence': 0.75, 'strategy_matches': self.strong_matches(),
            })
        adaptive=b._drift_and_threshold(history, 3000)
        self.assertGreater(adaptive['threshold'], 0.70)
        self.assertGreater(adaptive['penalty'], 0)

    def test_breakout_regime_is_detected(self):
        coin=self.strong_coin()
        coin['priceChange']['h1']=25
        coin['volume']={'h1': 900_000}
        regime=b._regime(coin, {'buy_sell_usd_ratio': 2.0})
        self.assertEqual(regime['name'], 'breakout_trend')

    def test_chaotic_regime_requires_extra_consensus(self):
        coin=self.strong_coin()
        coin['priceChange']={'m5': 31, 'h1': 130}
        decision=b.evaluate_pre(
            coin, ['MOMENTUM','FLOW_ELITE','ULTRA_PRECISION'], [],
            safety={'status':'pass'}, validation={'status':'pass'}, now_ms=1000,
        )
        self.assertFalse(decision['allow'])
        self.assertIn('chaotic_regime_needs_extra_consensus', decision['vetoes'])

    def test_strong_early_scout_can_act_before_slow_strategy_consensus(self):
        coin=self.strong_coin()
        coin['marketCap']=30_000
        coin['liquidityUsd']=15_000
        coin['priceChange']['m5']=4
        scout={'enabled':True,'allow':True,'score':0.88,'confirmations':7}
        decision=b.evaluate_pre(
            coin,['EARLY_SCOUT'],[],safety={'status':'pass'},validation={'status':'pass'},
            flow={'buy_sell_usd_ratio':3.0,'trades':10},scout=scout,now_ms=1000,
        )
        self.assertTrue(decision['allow'])
        self.assertTrue(decision['early_scout_strong'])
        self.assertNotIn('insufficient_strategy_consensus',decision['vetoes'])

    def test_early_scout_never_bypasses_safety_gate(self):
        scout={'enabled':True,'allow':True,'score':0.95,'confirmations':8}
        decision=b.evaluate_pre(
            self.strong_coin(),['EARLY_SCOUT'],[],safety={'status':'review'},validation={'status':'pass'},
            flow={'buy_sell_usd_ratio':4.0,'trades':15},scout=scout,now_ms=1000,
        )
        self.assertFalse(decision['allow'])
        self.assertIn('safety_not_passed',decision['vetoes'])

    def test_weak_scout_does_not_bypass_consensus(self):
        scout={'enabled':True,'allow':False,'score':0.60,'confirmations':3}
        decision=b.evaluate_pre(
            self.strong_coin(),['EARLY_SCOUT'],[],safety={'status':'pass'},validation={'status':'pass'},
            flow={'buy_sell_usd_ratio':2.0,'trades':8},scout=scout,now_ms=1000,
        )
        self.assertFalse(decision['allow'])
        self.assertIn('insufficient_strategy_consensus',decision['vetoes'])


if __name__ == '__main__':
    unittest.main()
