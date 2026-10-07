"""Market branches must remain observable, bounded and evidence independent."""
import copy
import json
import math
import unittest
from dataclasses import asdict
from unittest.mock import patch

import funded_market_candidates as funded
import lab_activity as activity


def established_candidate(**changes):
    return {
        'address': 'A' * 32, 'pairAddress': 'B' * 32,
        'dexId': 'pumpswap', 'quoteTokenAddress': funded.SOL_QUOTE_MINT,
        'liquidityUsd': 500_000, 'marketCap': 20_000_000,
        'ageMinutes': 2880, 'score': 1,
        'priceChange': {'m5': 2, 'h1': 10},
        'volume': {'h1': 50_000}, 'txns': {'m5': {'buys': 30, 'sells': 10}},
        **changes,
    }


def branches(strategy_id, coin, **kwargs):
    return funded.matched_branches(strategy_id, coin, activity.market_features(coin), **kwargs)


class FundedMarketCandidatesTests(unittest.TestCase):
    def test_established_branches_do_not_use_young_discovery_score(self):
        coin = established_candidate()
        for strategy_id, branch_id in (
                ('MOMENTUM', 'ESTABLISHED_LIQUID_MOMENTUM'),
                ('PRECISION', 'ESTABLISHED_LIQUID_PRECISION')):
            with self.subTest(strategy_id=strategy_id):
                self.assertFalse(activity.RULES[strategy_id].matches(activity.market_features(coin)))
                self.assertEqual(branches(strategy_id, coin), [branch_id])
                self.assertEqual(branches(strategy_id, coin, require_flow=False), [branch_id])
        self.assertEqual(branches('EARLY', coin), [])
        self.assertEqual(branches('ULTRA_PRECISION', coin), [])
        self.assertEqual(branches('SCALPER', coin), [])

    def test_missing_or_invalid_raw_fields_cannot_match_from_default_features(self):
        paths = [
            ('liquidityUsd',), ('marketCap',), ('ageMinutes',),
            ('priceChange', 'm5'), ('priceChange', 'h1'), ('volume', 'h1'),
            ('txns', 'm5', 'buys'), ('txns', 'm5', 'sells'),
        ]
        for path in paths:
            for value in (None, True, 'bad', math.nan, math.inf, -math.inf):
                with self.subTest(path=path, value=value):
                    coin = established_candidate()
                    target = coin
                    for part in path[:-1]:
                        target = target[part]
                    target[path[-1]] = value
                    features = activity.market_features(established_candidate())
                    # Supplying plausible derived features cannot conceal raw
                    # missing data in an established branch.
                    for strategy_id in ('MOMENTUM', 'PRECISION'):
                        self.assertEqual(funded.matched_branches(strategy_id, coin, features), [])
        for field in ('priceChange', 'volume', 'txns'):
            for invalid in (None, [], 'bad'):
                coin = established_candidate(**{field: invalid})
                self.assertEqual(funded.matched_branches('MOMENTUM', coin,
                                 activity.market_features(established_candidate())), [])

    def test_absent_market_cap_can_use_explicit_fdv_but_invalid_cap_cannot(self):
        coin = established_candidate(fdv=20_000_000)
        coin.pop('marketCap')
        self.assertEqual(branches('MOMENTUM', coin), ['ESTABLISHED_LIQUID_MOMENTUM'])
        for invalid in (0, -1, math.nan, 'bad'):
            coin['marketCap'] = invalid
            self.assertEqual(branches('MOMENTUM', coin), [])

    def test_supported_dex_and_consistent_identified_sol_quote_are_required(self):
        changes = (
            {'dexId': 'raydium'}, {'dexId': None},
            {'quoteTokenAddress': None}, {'quoteTokenAddress': 'another-asset'},
            {'quoteToken': 'bad'}, {'quoteToken': {'address': 'another-asset'}},
        )
        for change in changes:
            with self.subTest(change=change):
                coin = established_candidate(**change)
                self.assertEqual(branches('MOMENTUM', coin), [])
        coin = established_candidate(quoteToken={'address': funded.SOL_QUOTE_MINT})
        self.assertEqual(branches('MOMENTUM', coin), ['ESTABLISHED_LIQUID_MOMENTUM'])

    def test_physical_envelope_rejects_weak_negative_excessive_and_churning_markets(self):
        changes = (
            {'liquidityUsd': 199_999}, {'ageMinutes': 1439},
            {'ageMinutes': 10_000_001}, {'marketCap': 40_000_000},
            {'priceChange': {'m5': -.5, 'h1': 10}},
            {'priceChange': {'m5': 15.01, 'h1': 10}},
            {'priceChange': {'m5': 2, 'h1': -.01}},
            {'priceChange': {'m5': 2, 'h1': 40.01}},
            {'volume': {'h1': 14_999}}, {'volume': {'h1': 1_500_001}},
            {'volume': {'h1': -1}},
            {'txns': {'m5': {'buys': 11, 'sells': 10}}},
            {'txns': {'m5': {'buys': 0, 'sells': 0}}},
            {'txns': {'m5': {'buys': -1, 'sells': 0}}},
            {'txns': {'m5': {'buys': 10, 'sells': -1}}},
            {'txns': {'m5': {'buys': 30.5, 'sells': 10}}},
        )
        for change in changes:
            with self.subTest(change=change):
                self.assertEqual(branches('MOMENTUM', established_candidate(**change)), [])

    def test_precision_has_the_declared_stricter_market_envelope(self):
        changes = (
            {'liquidityUsd': 299_999, 'marketCap': 10_000_000},
            {'priceChange': {'m5': 8.01, 'h1': 10}},
            {'priceChange': {'m5': 2, 'h1': 20.01}},
            {'txns': {'m5': {'buys': 14, 'sells': 10}}},
            {'volume': {'h1': 750_001}},
        )
        for change in changes:
            with self.subTest(change=change):
                coin = established_candidate(**change)
                self.assertEqual(branches('PRECISION', coin), [])
                self.assertEqual(branches('MOMENTUM', coin), ['ESTABLISHED_LIQUID_MOMENTUM'])

    def test_inclusive_physical_boundary_values_match(self):
        for strategy_id, liquidity, ratio in (('MOMENTUM', 200_000, 1.2),
                                               ('PRECISION', 300_000, 1.5)):
            coin = established_candidate(
                liquidityUsd=liquidity, marketCap=liquidity / .015,
                ageMinutes=1440, priceChange={'m5': .5, 'h1': 0},
                volume={'h1': liquidity * .03},
                txns={'m5': {'buys': int(ratio * 10), 'sells': 10}})
            self.assertEqual(branches(strategy_id, coin),
                             [funded.ESTABLISHED_BRANCHES[strategy_id]['id']])

    def test_original_activity_rule_objects_and_matching_behavior_are_unchanged(self):
        originals = dict(activity.RULES)
        coin = established_candidate(score=100, ageMinutes=30, marketCap=1_000_000,
                                     liquidityUsd=200_000,
                                     priceChange={'m5': 5, 'h1': 10})
        features = activity.market_features(coin)
        for strategy_id in funded.FUNDED_STRATEGIES:
            before = copy.deepcopy(features)
            self.assertTrue(originals[strategy_id].matches(features))
            self.assertEqual(funded.matched_branches(strategy_id, coin, features), [strategy_id])
            self.assertIs(activity.RULES[strategy_id], originals[strategy_id])
            self.assertEqual(features, before)
        # The scheduler's require_flow=False must reach the original method.
        with patch.object(activity.EntryRule, 'matches', return_value=False) as match:
            funded.matched_branches('EARLY', coin, features, require_flow=False)
            match.assert_called_once_with(features, require_flow=False)

    def test_config_preserves_legacy_fields_and_declares_each_branch(self):
        config = funded.candidate_config()
        self.assertEqual(set(config), set(funded.FUNDED_STRATEGIES))
        self.assertEqual(funded.VERSION, 'FUNDED_MARKET_BRANCHES_V1')
        for strategy_id, row in config.items():
            original = {name: (None if isinstance(value, float) and not math.isfinite(value)
                               else value)
                        for name, value in asdict(activity.RULES[strategy_id]).items()}
            self.assertEqual({key: row[key] for key in original}, original)
            self.assertEqual(row['candidate_branches'][0]['id'], strategy_id)
            self.assertEqual(row['candidate_branches'][0]['constraints'], original)
            self.assertTrue(row['candidate_branches'][0]['score_is_admission_gate'])
            if strategy_id in ('MOMENTUM', 'PRECISION'):
                branch = row['candidate_branches'][1]
                self.assertFalse(branch['score_is_admission_gate'])
                self.assertNotIn('score', branch['constraints'])
                self.assertTrue(branch['constraints']['requires_finite_raw_fields'])
            else:
                self.assertEqual(len(row['candidate_branches']), 1)
        json.dumps(config, allow_nan=False)
        config['MOMENTUM']['candidate_branches'][1]['constraints']['liquidity'] = 0
        self.assertEqual(funded.candidate_config()['MOMENTUM']['candidate_branches'][1]
                         ['constraints']['liquidity'], 200_000)


if __name__ == '__main__':
    unittest.main()
