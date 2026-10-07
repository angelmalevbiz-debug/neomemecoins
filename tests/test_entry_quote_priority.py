"""Scarce quote work serves plausible costs without changing admission gates."""
import copy
import unittest

from entry_quote_priority import bounded_feed, prioritize_entry_candidates
from paper_market_feasibility import SOL_QUOTE_MINT, execution_feasibility


def coin(symbol, market_cap, *, score=80, candidate=True):
    return {'symbol': symbol, 'address': symbol + '-mint',
            'pairAddress': symbol + '-pool', 'dexId': 'pumpswap',
            'quoteTokenAddress': SOL_QUOTE_MINT,
            'priceUsd': 1.0, 'priceNative': .005,
            'marketCap': market_cap, 'liquidityUsd': 500_000,
            'score': score, 'candidate': candidate}


def order(coins, **kwargs):
    return prioritize_entry_candidates(
        coins, 1.5, is_candidate=lambda c: c['candidate'], **kwargs)


class EntryQuotePriorityTests(unittest.TestCase):
    def test_affordable_lower_score_gets_quote_slot_before_high_fee_high_score(self):
        expensive = coin('HOT', 100_000, score=98)
        affordable = coin('MATURE', 10_000_000, score=80)
        self.assertFalse(execution_feasibility(expensive, 1.5)['model_cost_feasible'])
        self.assertTrue(execution_feasibility(affordable, 1.5)['model_cost_feasible'])
        ordered = order([expensive, affordable])
        # With one remaining quote slot, the prior score ordering would spend
        # it on HOT. Queue priority now gives the plausible route a chance.
        self.assertIs(ordered[0], affordable)
        self.assertIs(ordered[1], expensive)

    def test_unknown_cost_is_retained_between_affordable_and_expensive(self):
        expensive = coin('HOT', 100_000)
        unknown = coin('UNKNOWN', 1_000_000)
        unknown.pop('quoteTokenAddress')
        affordable = coin('MATURE', 10_000_000)
        self.assertEqual(order([expensive, unknown, affordable]),
                         [affordable, unknown, expensive])

    def test_every_coin_is_retained_even_when_not_a_market_candidate(self):
        expensive = coin('HOT', 100_000)
        noncandidate = coin('OTHER', 100_000_000, candidate=False)
        unknown = coin('UNKNOWN', 0)
        affordable = coin('MATURE', 10_000_000)
        original = [expensive, noncandidate, unknown, affordable]
        ordered = order(original)
        self.assertEqual(ordered, [affordable, unknown, expensive, noncandidate])
        self.assertEqual({id(c) for c in ordered}, {id(c) for c in original})
        self.assertEqual(len(ordered), len(original))

    def test_relative_order_is_stable_within_each_bucket(self):
        candidates = [coin('B', 10_000_000, score=98),
                      coin('A', 10_000_000, score=80),
                      coin('D', 100_000, score=95),
                      coin('C', 100_000, score=79)]
        self.assertEqual(order(candidates), candidates)

    def test_market_feed_and_held_position_order_are_not_mutated(self):
        expensive = coin('HELD-HOT', 100_000)
        affordable = coin('HELD-MATURE', 10_000_000)
        feed = [expensive, affordable]
        held = [{'coin_snapshot': expensive}, {'coin_snapshot': affordable}]
        before_feed, before_held = copy.deepcopy(feed), copy.deepcopy(held)
        ordered = order(feed)
        self.assertEqual(feed, before_feed)
        self.assertEqual(held, before_held)
        self.assertIsNot(ordered, feed)
        self.assertIs(held[0]['coin_snapshot'], expensive)

    def test_model_feasibility_does_not_add_entry_permission_or_override_evidence(self):
        unsafe = coin('CHEAP-UNSAFE', 10_000_000)
        unsafe.update(safety={'status': 'fail'}, verified_flow=None)
        original = copy.deepcopy(unsafe)
        ordered = order([unsafe])
        self.assertIs(ordered[0], unsafe)
        self.assertEqual(unsafe, original)
        self.assertNotIn('allow', unsafe)
        self.assertEqual(unsafe['safety']['status'], 'fail')
        self.assertIsNone(unsafe['verified_flow'])

    def test_failed_or_malformed_estimate_is_unknown_and_never_lost(self):
        expensive = coin('HOT', 100_000)
        unknown = coin('UNKNOWN', 0)
        affordable = coin('MATURE', 10_000_000)

        def estimate(c, budget, **kwargs):
            if c is unknown:
                raise ValueError('planning data unavailable')
            return execution_feasibility(c, budget, **kwargs)

        self.assertEqual(order([expensive, unknown, affordable], feasibility=estimate),
                         [affordable, unknown, expensive])
        self.assertEqual(order([unknown], feasibility=lambda *_, **__: None), [unknown])
        self.assertEqual(order([unknown], feasibility=lambda *_, **__: {'model_cost_feasible': 1}),
                         [unknown])

    def test_main_planning_estimate_is_fee_only_before_actual_quote_buffers(self):
        candidate = coin('MATURE', 10_000_000)
        calls = []

        def estimate(c, budget, **kwargs):
            calls.append((c, budget, kwargs))
            return execution_feasibility(c, budget, **kwargs)

        self.assertEqual(order([candidate], feasibility=estimate), [candidate])
        self.assertEqual(calls, [(candidate, 1.5,
                                {'base_slippage_bps': 0, 'latency_buffer_bps': 0})])

    def test_noncandidates_do_not_consume_estimation_work(self):
        noncandidate = coin('OTHER', 10_000_000, candidate=False)

        def forbidden(*_):
            self.fail('Noncandidate should not be estimated')

        self.assertEqual(order([noncandidate], feasibility=forbidden), [noncandidate])


class BoundedFeedTests(unittest.TestCase):
    def bounded(self, coins, cap, **kwargs):
        return bounded_feed(coins, cap, 1.5,
                            is_candidate=lambda c: c['candidate'], **kwargs)

    def test_candidate_below_score_cap_is_retained_without_reordering_display(self):
        high_score = [coin(f'HOT-{i}', 100_000, score=100-i*.1)
                      for i in range(90)]
        affordable = coin('MATURE', 10_000_000, score=70)
        original = high_score + [affordable]
        selected = self.bounded(original, 90)
        self.assertEqual(len(selected), 90)
        self.assertEqual(selected, high_score[:89] + [affordable])
        self.assertIs(selected[-1], affordable)
        self.assertEqual(original, high_score + [affordable])

    def test_unknown_candidate_gets_capacity_before_modeled_expensive_candidate(self):
        expensive = coin('HOT', 100_000, score=98)
        unknown = coin('UNKNOWN', 0, score=85)
        affordable = coin('MATURE', 10_000_000, score=80)
        self.assertEqual(self.bounded([expensive, unknown, affordable], 2),
                         [unknown, affordable])

    def test_feed_within_cap_keeps_exact_order_without_estimation(self):
        original = [coin('HOT', 100_000), coin('MATURE', 10_000_000)]

        def forbidden(*_, **__):
            self.fail('A feed within its cap needs no selection estimates')

        selected = self.bounded(original, 90, feasibility=forbidden)
        self.assertEqual(selected, original)
        self.assertIsNot(selected, original)

    def test_repeated_coin_occurrences_are_preserved_without_identity_deduplication(self):
        expensive = coin('HOT', 100_000)
        affordable = coin('MATURE', 10_000_000)
        original = [expensive, affordable, affordable, expensive]
        self.assertEqual(self.bounded(original, 3),
                         [expensive, affordable, affordable])
        self.assertEqual(original, [expensive, affordable, affordable, expensive])

    def test_selection_does_not_change_external_held_position_order(self):
        expensive = coin('HELD-HOT', 100_000)
        affordable = coin('HELD-MATURE', 10_000_000)
        feed = [expensive, affordable]
        held = [{'coin_snapshot': expensive}, {'coin_snapshot': affordable}]
        before = copy.deepcopy(held)
        self.assertEqual(self.bounded(feed, 1), [affordable])
        self.assertEqual(held, before)
        self.assertIs(held[0]['coin_snapshot'], expensive)

    def test_zero_capacity_is_empty_and_keeps_inputs(self):
        original = [coin('MATURE', 10_000_000)]
        self.assertEqual(self.bounded(original, 0), [])
        self.assertEqual(len(original), 1)


if __name__ == '__main__':
    unittest.main()
