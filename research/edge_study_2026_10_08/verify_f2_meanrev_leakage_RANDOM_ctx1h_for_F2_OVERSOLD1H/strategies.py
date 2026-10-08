"""Verifier module for leaderboard row RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB (f2_meanrev baseline, orig).

NOT a candidate and not a recommendation. CONFIGS[0] is an independent re-implementation of the row's rule from its
plain description (signal reads only the Past view); BASELINES are the expectation it scatters around:
  - the same rule entering at EVERY eligible point (population, no randomness),
  - the same rule with two other hash salts.
Under leaderboard/harness_final.py the population row is about -4.2 %/trade on holdout and -5.7 % on train (net50,
calibrated); 397 of 400 alternative salts have a holdout mean below the row's +3.72 % (mean of means -1.09 %).
"""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

EXIT_A = dict(tp=20.0, stop=-25.0, hold_min=90.0)


def _eligible(P):
    """liq >= $50k, interim rug screen passes, DexScreener pc1h <= -12% (all read at the decision point only)."""
    if not (P('liq') >= 50_000):
        return False
    mc, liq, age = P('mcap'), P('liq'), P('age')
    young = not (age >= 14 * 1440)
    if mc >= 20e6 and liq > 0 and liq / mc < 0.02 and young:
        return False
    if young and H.other_pairs_same_ticker_before(P) >= 1:
        return False
    return P('pc1h') <= -12.0


def make_random(prob, salt):
    def signal(P):
        return H.hashed_coin(P.static('pair'), P.t, prob, salt) and _eligible(P)
    return signal


CONFIGS = [
    {'name': 'VERIFY_REIMPL_RANDOM_ctx1h_f2r1',
     'description': 'Independent re-implementation of RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB: hashed coin p=0.005 salt '
                    'f2r1, PumpSwap liq>=50k, interim rug screen, pc1h<=-12%; exit +20/-25/90. Reproduces the '
                    'leaderboard trades exactly (51/51, net50 diff 0). A random-entry row: NOT a candidate.',
     'signal': make_random(0.005, 'f2r1'),
     'kwargs': dict(EXIT_A)},
]

BASELINES = [
    {'name': 'VERIFY_POPULATION_ctx1h_every_eligible_point',
     'description': 'Same universe and exits, entering at every eligible point (cooldown 300 s, one position per pair).',
     'signal': _eligible,
     'kwargs': dict(EXIT_A)},
    {'name': 'VERIFY_RANDOM_ctx1h_salt_v000',
     'description': 'Same rule, hash salt v000.',
     'signal': make_random(0.005, 'v000'),
     'kwargs': dict(EXIT_A)},
    {'name': 'VERIFY_RANDOM_ctx1h_salt_v001',
     'description': 'Same rule, hash salt v001.',
     'signal': make_random(0.005, 'v001'),
     'kwargs': dict(EXIT_A)},
]
