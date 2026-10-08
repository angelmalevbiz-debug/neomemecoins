"""Adversarial robustness verifier for f2_meanrev / RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB (variant rug_guard_v1).

VERDICT: REFUTED. This module therefore submits NO configuration (CONFIGS is empty on purpose).
The verified row is itself a random-entry baseline. Its positive holdout (+$5.97/trade, n=20) is one lucky coin
draw: across 100 other salts of the identical procedure the holdout mean is -$1.54 (33% positive, the row sits at the
96th percentile), the train mean is -$5.27 (14% positive), and entering whenever eligible (p=1) gives
holdout -$8.14 / train -$10.72. See REPORT.md.

BASELINES reproduce the verified row (salt 'f2r1', trade-for-trade identical to leaderboard/trades/f2_meanrev.pkl
under leaderboard/harness_final.py) and five alternative salts of the same procedure, so an integrator can re-run
the seed comparison. They are random entries and must never be treated as candidates.
"""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

EXIT_A = dict(tp=20.0, stop=-25.0, hold_min=90.0)
_GUARD = getattr(H, 'rug_guard_v1', None)   # present in leaderboard/harness_final.py; absent in harness v1


def make_rand(prob=0.005, salt='f2r1', pc1h_max=-12.0, liq_min=50_000.0, guard=True):
    """f2 make_random(prob, salt, pc1h_max) + the leaderboard's AND not rug_guard_v1 (when the harness has it)."""
    def signal(P):
        if prob < 1.0 and not H.hashed_coin(P.static('pair'), P.t, prob, salt):
            return False
        if not (P('liq') >= liq_min):
            return False
        if H.interim_rug_risk(P):
            return False
        if pc1h_max is not None and not (P('pc1h') <= pc1h_max):
            return False
        if guard and _GUARD is not None and H.rug_guard_v1(P):
            return False
        return True
    return signal


CONFIGS = []   # nothing qualifies: the verified row is a random baseline with no out-of-sample edge

VERIFIED_ROW = {
    'name': 'RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB (rug_guard_v1)',
    'description': 'Random entries (hashed coin p=0.005, salt f2r1) in PumpSwap liq>=$50k, interim rug screen, '
                   'rug_guard_v1, pc1h<=-12%; exits +20% / -25% net / 90 min. REFUTED (see REPORT.md).',
    'signal': make_rand(salt='f2r1'),
    'kwargs': dict(EXIT_A),
}

BASELINES = [dict(VERIFIED_ROW)] + [
    {'name': 'RANDOM_ctx1h_OVERSOLD1H_salt_%s' % s,
     'description': 'Same random procedure as the verified row, coin salt %r (seed check).' % s,
     'signal': make_rand(salt=s),
     'kwargs': dict(EXIT_A)}
    for s in ('vA', 'vB', 'vC', 'vD', 'vE')
]
