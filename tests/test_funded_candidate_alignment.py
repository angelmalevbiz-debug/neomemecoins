"""Prospective funded entries share candidates without bypassing evidence/costs."""
import copy
import json
import unittest
from unittest.mock import patch

import lab_activity as activity
import promoted_entry_guard as guard
import strategy_lab as lab
from lab_dashboard_projection import compact_strategy_lab
import entry_defense as _isolation_entry_defense
import strategy_lab as _isolation_lab

_DEFENSIVE_ISOLATION = []


def _defensive_pass(*_args, **_kwargs):
    return _isolation_entry_defense.pass_decision('TEST_GATE_ISOLATION')


def setUpModule():
    """These tests isolate other entry gates. DEFENSIVE_ENTRY_LAYER_V1 (structural rug
    guard, pool loss memory, heat veto and its warm-up) has its own suite in
    tests/test_defensive_entry_layer.py, which proves every path consults it."""
    for target, name in ((_isolation_lab, 'defensive_entry_decision'),):
        isolation = patch.object(target, name, _defensive_pass)
        isolation.start()
        _DEFENSIVE_ISOLATION.append(isolation)


def tearDownModule():
    while _DEFENSIVE_ISOLATION:
        _DEFENSIVE_ISOLATION.pop().stop()


NOW = 1_800_000_000_000
MINT = 'BrUimx7KncgRNTggAdZdaX2s5XUqQyR6XMRmEg4mpump'
PAIR = '8t34p7n94man8wcmFdHedYkJaWEhA9nKGJMLZyZUzbn'


def candidate(**changes):
    return {
        'address':MINT,'pairAddress':PAIR,'symbol':'FIXTURE',
        'priceUsd':.01,'priceNative':.01/150,'quoteTokenAddress':lab.SOL_QUOTE_MINT,
        'marketCap':1_000_000,'liquidityUsd':200_000,'dexId':'raydium',
        'updatedAt':NOW,'score':91,'ageMinutes':100,
        'priceChange':{'m5':1.95,'h1':20},'volume':{'h1':100_000},
        'txns':{'m5':{'buys':20,'sells':10}},**changes,
    }


def flow():
    return {(MINT,PAIR):{'verified_flow':{
        'source':guard.FLOW_SOURCE,'coverage_status':'COMPLETE',
        'window_ms':guard.FLOW_WINDOW_MS,'address':MINT,'pairAddress':PAIR,
        'window_at':NOW,'available_at':NOW-50,'latest_event_at':NOW-100,
        'trades':4,'unique_wallets':3,'buy_usd':500,'sell_usd':100,
    }}}


class FundedCandidateAlignmentTests(unittest.TestCase):
    def setUp(self):
        books={s['id']:lab.empty_book(s) for s in lab.STRATEGIES}
        for book in books.values():
            book['position']={'keep':'existing-position'}
        self.state={'books':books}
        self.risk={'status':'pass','mint':MINT,'pair':PAIR,'checked_at':NOW}
        for mock in (
            patch.object(lab,'STATE',self.state),
            patch.object(lab,'now_ms',return_value=NOW),
            patch.object(lab.rug_guard,'check',return_value=self.risk),
            patch.object(lab.price_integrity,'check',return_value={
                'status':'pass','mint':MINT,'pair':PAIR}),
        ):
            mock.start();self.addCleanup(mock.stop)

    def enable(self,strategy_id):
        book=self.state['books'][strategy_id]
        book['position']=None
        book['history']=[{
            'trade_no':7,'closed_at':NOW-100_000,'address':'other',
            'pnl_usd':2,'entry_policy_version':guard.VERSION,'custom_legacy':True,
        }]
        book['trade_seq']=7
        return book

    def test_each_shared_family_reaches_admission_beyond_legacy_score_or_age_limit(self):
        # Every row falls outside that family's prior stricter lambda. These
        # are correctness fixtures, not market observations or return evidence.
        rows={
            'EARLY':candidate(score=86,ageMinutes=90,priceChange={'m5':.5,'h1':20}),
            'MOMENTUM':candidate(score=86,ageMinutes=450,priceChange={'m5':4,'h1':20}),
            'PRECISION':candidate(score=91,ageMinutes=300),
            'ULTRA_PRECISION':candidate(score=94,ageMinutes=200),
        }
        for strategy_id,coin in rows.items():
            with self.subTest(strategy_id=strategy_id):
                book=self.enable(strategy_id)
                before=copy.deepcopy(book)
                lab.maybe_open([coin],flow())
                position=book['position']
                self.assertIsNotNone(position)
                self.assertEqual(position['entry_policy_version'],guard.FUNDED_POLICY_VERSION)
                self.assertEqual(position['entry_evidence_guard_version'],guard.VERSION)
                self.assertEqual(position['entry_candidate_rule'],lab.promoted_candidate_config()[strategy_id])
                self.assertEqual(position['dexId'],coin['dexId'])
                self.assertEqual(position['quoteTokenAddress'],lab.SOL_QUOTE_MINT)
                projected=compact_strategy_lab(self.state)['books'][strategy_id]['position']
                self.assertEqual(projected['dexId'],coin['dexId'])
                self.assertEqual(projected['quoteTokenAddress'],lab.SOL_QUOTE_MINT)
                self.assertEqual(position['trade_no'],8)
                self.assertEqual(book['history'],before['history'])
                self.assertEqual(book['balance'],before['balance'])
                self.assertLess(position['open_pnl_usd'],0)
                self.assertGreaterEqual(position['entry_roundtrip_pnl_pct'],-1.5)
                self.assertEqual(lab.stats(book)['promoted_policy_trades'],0)

    def test_shared_candidate_cannot_replace_stale_or_wrong_pool_flow(self):
        for changes in ({'latest_event_at':NOW-12_001},{'pairAddress':'other'},
                        {'coverage_status':'DEGRADED'},{'unique_wallets':1},
                        {'buy_usd':50,'sell_usd':100}):
            with self.subTest(changes=changes):
                book=self.enable('PRECISION');before=copy.deepcopy(book)
                observed=flow();observed[(MINT,PAIR)]['verified_flow'].update(changes)
                lab.maybe_open([candidate()],observed)
                self.assertIsNone(book['position'])
                self.assertEqual(book['entry_diagnostics']['signal_candidates'],1)
                self.assertEqual(book['entry_diagnostics']['promoted_flow_rejected'],1)
                for field in ('balance','history','trade_seq','last_entry_by_address'):
                    self.assertEqual(book[field],before[field])

    def test_shared_candidate_keeps_full_fresh_exact_safety_requirement(self):
        for changes in ({'status':'review'},{'provisional_early':True},
                        {'checked_at':NOW-60_001},{'pair':'other'},{'mint':'other'}):
            with self.subTest(changes=changes):
                book=self.enable('PRECISION')
                with patch.object(lab.rug_guard,'check',return_value={**self.risk,**changes}):
                    lab.maybe_open([candidate()],flow())
                self.assertIsNone(book['position'])
                self.assertEqual(book['entry_diagnostics']['promoted_safety_rejected'],1)

    def test_shared_candidate_requires_independent_price_for_same_pool(self):
        book=self.enable('PRECISION')
        with patch.object(lab.price_integrity,'check',return_value={
                'status':'pass','mint':MINT,'pair':'another-pool'}):
            lab.maybe_open([candidate()],flow())
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['promoted_price_rejected'],1)

    def test_shared_candidate_does_not_waive_fixed_costs_when_no_size_is_affordable(self):
        book=self.enable('PRECISION');before=copy.deepcopy(book)
        c=candidate(dexId='pumpswap',marketCap=50_000,liquidityUsd=25_000)
        self.assertTrue(activity.RULES['PRECISION'].matches(activity.market_features(c)))
        lab.maybe_open([c],flow())
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['promoted_cost_rejected'],1)
        self.assertEqual(book['entry_diagnostics']['blocked_reason'],'promoted_cost_headroom_insufficient')
        feasibility=book['entry_diagnostics']['promoted_cost_feasibility']
        self.assertEqual(feasibility['checked_market_candidates'],1)
        self.assertEqual(feasibility['fixed_cost_infeasible_candidates'],1)
        self.assertGreater(feasibility['minimum_model_roundtrip_cost_pct'],1.5)
        self.assertFalse(feasibility['is_execution_quote'])
        self.assertFalse(feasibility['profitability_proven'])
        projected=compact_strategy_lab(self.state)['books']['PRECISION']['entry_diagnostics']
        self.assertEqual(projected['promoted_cost_feasibility'],feasibility)
        for field in ('balance','history','trade_seq','last_entry_by_address'):
            self.assertEqual(book[field],before[field])

    def test_cost_estimate_never_admits_candidate_with_missing_confirmed_flow(self):
        book=self.enable('PRECISION')
        lab.maybe_open([candidate()],{})
        feasibility=book['entry_diagnostics']['promoted_cost_feasibility']
        self.assertTrue(feasibility['best_candidates'][0]['model_cost_feasible'])
        self.assertIsNone(book['position'])
        self.assertEqual(book['entry_diagnostics']['blocked_reason'],'promoted_verified_flow_unavailable')

    def test_current_policy_counts_exclude_earlier_policy_without_changing_ledger(self):
        book=self.enable('PRECISION')
        book['history'].append({
            'trade_no':8,'pnl_usd':-1,'closed_at':NOW-10,
            'entry_policy_version':guard.FUNDED_POLICY_VERSION,
        })
        before=copy.deepcopy(book)
        summary=lab.stats(book)
        self.assertEqual(summary['trades'],2)
        self.assertEqual(summary['wins'],1)
        self.assertEqual(summary['promoted_policy_trades'],1)
        self.assertEqual(summary['promoted_policy_wins'],0)
        self.assertEqual(book,before)

    def test_published_candidate_config_is_exact_and_does_not_claim_profit(self):
        rules=lab.promoted_candidate_config()
        config=guard.funded_policy_config(lab.STOP_LOSS,rules)
        encoded=json.dumps(config,allow_nan=False)
        self.assertIn(guard.FUNDED_POLICY_VERSION,encoded)
        self.assertEqual(config['evidence_guard_version'],guard.VERSION)
        self.assertEqual(config['maximum_roundtrip_cost_pct'],1.5)
        self.assertEqual(config['candidate_rules']['EARLY']['age'],(2,120))
        self.assertEqual(config['candidate_rules']['PRECISION']['score'],90)
        self.assertEqual(set(config['candidate_rules']),set(lab.PROMOTED_STRATEGIES))
        self.assertFalse(config['profitability_proven'])
        self.assertTrue(config['historical_outcomes_unchanged'])
        projected=compact_strategy_lab({'activity_config':{'promoted_entry_policy':config}})
        self.assertEqual(projected['activity_config']['promoted_entry_policy'],config)


if __name__=='__main__':
    unittest.main()
