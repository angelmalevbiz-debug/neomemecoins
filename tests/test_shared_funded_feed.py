"""Shared-feed coverage mechanics, not evidence of profitable trading."""
import copy
import os
import unittest
from unittest.mock import patch

import market_monitor as monitor
import funded_active_paper as active
from entry_quote_priority import bounded_feed
from test_funded_active_paper import coin, NOW


class SharedFundedFeedTests(unittest.TestCase):
    def setUp(self):
        flags=patch.dict(os.environ,{active.ENV:'1',active.QUALITY_ENV:'1',
            'NEO_ENGINE_MODE':'PAPER','NEO_EXECUTION_MODE':'PAPER'})
        flags.start();self.addCleanup(flags.stop)

    def test_independent_momentum_market_candidate_survives_main_rejection(self):
        c=coin(score=10,ageMinutes=15_000,priceChange={'m5':0,'h1':0})
        self.assertTrue(active.matches('MOMENTUM',c))
        self.assertFalse(active.matches('EARLY',c))
        with patch.object(monitor,'market_candidate',return_value=False) as main:
            self.assertTrue(monitor.shared_feed_candidate(c,NOW,'registry'))
            main.assert_called_once_with(c,NOW,'registry')

    def test_each_funded_strategy_can_supply_retention_without_main_signal(self):
        for sid in active.RULES:
            with patch.object(monitor,'market_candidate',return_value=False),\
                 patch.object(active,'matches',side_effect=lambda strategy,c:strategy==sid):
                self.assertTrue(monitor.shared_feed_candidate(coin(),NOW))

    def test_main_candidates_are_retained_without_expanding_lab(self):
        with patch.object(monitor,'market_candidate',return_value=True),\
             patch.object(active,'matches',side_effect=AssertionError('no Lab checks needed')):
            self.assertTrue(monitor.shared_feed_candidate(coin(),NOW))

    def test_disabled_quality_and_live_modes_never_expand_main_universe(self):
        for flags in ({active.ENV:'0'},{active.QUALITY_ENV:'0'},
                      {'NEO_ENGINE_MODE':'LIVE'},{'NEO_EXECUTION_MODE':'LIVE'}):
            with patch.dict(os.environ,flags),patch.object(monitor,'market_candidate',return_value=False):
                self.assertFalse(monitor.shared_feed_candidate(coin(),NOW))

    def test_valid_funded_candidate_below_ninety_score_rows_is_retained_without_mutation(self):
        high=[coin(score=100,priceChange={'m5':-100,'h1':-100}) for _ in range(90)]
        scalp=coin(score=10,ageMinutes=15_000,priceChange={'m5':0,'h1':0})
        feed=[*high,scalp];before=copy.deepcopy(feed)
        with patch.object(monitor,'market_candidate',return_value=False):
            selected=bounded_feed(feed,90,1.5,
                is_candidate=lambda c:monitor.shared_feed_candidate(c,NOW),
                feasibility=lambda *a,**k:{'model_cost_feasible':True})
        self.assertEqual(selected,[*high[:89],scalp])
        self.assertEqual(feed,before)

    def test_nonmatching_market_does_not_gain_retention_by_missing_a_signal(self):
        c=coin(priceChange={'m5':-10,'h1':-50})
        with patch.object(monitor,'market_candidate',return_value=False):
            self.assertFalse(monitor.shared_feed_candidate(c,NOW))


if __name__=='__main__':unittest.main()
