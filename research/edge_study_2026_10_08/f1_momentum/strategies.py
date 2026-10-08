"""F1 momentum / breakout-continuation family: the configs pre-selected on TRAIN (max 3) and their random baselines.

Every signal uses only the harness Past view P (past-only). PumpSwap/SOL-quote only (H.simulate defaults).
None of these is a CANDIDATE: all three lose on holdout (net50 mean -16.0 / -11.4 / -6.1 %) and lose to their
random baselines (-0.9 / -3.6 / -2.6 %). Numbers in final_eval.json. They are kept so the integrator can reproduce
the result. 352 configurations were evaluated on train (grid1.jsonl, grid2.jsonl) before these 3 were fixed.
"""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H


def _ret(P, seconds):
    """% price change over the last `seconds` (past-only), NaN when unavailable."""
    now, then = P('price'), P.ago('price', seconds)
    if now > 0 and then > 0:
        return 100.0 * (now / then - 1.0)
    return float('nan')


def _bsh5(P):
    b, s = P('b5'), P('s5')
    return b / (b + s) if b + s > 0 else float('nan')


def _vacc(P):
    v5, v1h = P('v5'), P('v1h')
    return v5 / (v1h / 12.0) if v1h > 0 and v5 >= 0 else float('nan')


def hi30(P):
    """High-fee PumpSwap pools (fee tier >= 100 bps, i.e. market cap < ~9.8k SOL), liquidity >= $30k, rug-screened."""
    return P('liq') >= 30_000 and P.fee_bps() >= 100 and not H.interim_rug_risk(P)


def hi50(P):
    return P('liq') >= 50_000 and P.fee_bps() >= 100 and not H.interim_rug_risk(P)


def sig_trend_pullback_35_4(P):
    """Trend-pullback: up >= 35% over the last hour (DexScreener pc1h) and down >= 4% over the last 5 minutes."""
    return P('pc1h') >= 35 and _ret(P, 300) <= -4 and hi30(P)


def sig_trend_pullback_20_4(P):
    return P('pc1h') >= 20 and _ret(P, 300) <= -4 and hi30(P)


def sig_spike8(P):
    """Classic momentum burst: +8% in 5 min, buyers >= 55% of 5-min txns, 5-min volume >= the 1h average pace."""
    return _ret(P, 300) >= 8 and _bsh5(P) >= 0.55 and _vacc(P) >= 1.0 and hi50(P)


E9 = dict(stop=-8, tp=15, hold_min=60)
E3 = dict(stop=-8, tp=40, trail_arm=15, trail=8, hold_min=120)
E6 = dict(stop=-5, tp=20, hold_min=15)

CONFIGS = [
    {'name': 'F1_TREND_PULLBACK_HI30_PC1H35_R5M-4_S8_TP15_H60',
     'description': 'HI-fee PumpSwap (fee>=100bps, liq>=$30k, rug-screened): pc1h>=+35% and last-5-min return <=-4%; '
                    'stop -8 / tp +15 / 60 min (net, model costs).',
     'signal': sig_trend_pullback_35_4, 'kwargs': dict(E9)},
    {'name': 'F1_TREND_PULLBACK_HI30_PC1H20_R5M-4_S8_TP40_TRAIL15/8_H120',
     'description': 'Same universe: pc1h>=+20% and last-5-min return <=-4%; stop -8 / tp +40 / trail arm 15 give-back 8 / 120 min.',
     'signal': sig_trend_pullback_20_4, 'kwargs': dict(E3)},
    {'name': 'F1_SPIKE_HI50_R5M+8_BUY55_VACC1_S5_TP20_H15',
     'description': 'Pure momentum reference (best pure-momentum config on train, still negative): HI-fee, liq>=$50k, '
                    '+8% in 5 min, buy share >=55%, 5-min volume >= 1h pace; stop -5 / tp +20 / 15 min.',
     'signal': sig_spike8, 'kwargs': dict(E6)},
]


def _rnd(universe, prob, salt):
    return lambda P: universe(P) and H.hashed_coin(P.static('pair'), P.t, prob, salt)


# probabilities calibrated on TRAIN entry counts only (similar trade count to the matching config)
BASELINES = [
    {'name': 'RANDOM_HI30_S8_TP15_H60', 'description': 'Random entries in the HI30 rug-screened universe, exits of config 1.',
     'signal': _rnd(hi30, 0.0012, 'f1a'), 'kwargs': dict(E9)},
    {'name': 'RANDOM_HI30_S8_TP40_TRAIL15/8_H120', 'description': 'Random entries in the HI30 rug-screened universe, exits of config 2.',
     'signal': _rnd(hi30, 0.0012, 'f1b'), 'kwargs': dict(E3)},
    {'name': 'RANDOM_HI50_S5_TP20_H15', 'description': 'Random entries in the HI50 rug-screened universe, exits of config 3.',
     'signal': _rnd(hi50, 0.0010, 'f1c'), 'kwargs': dict(E6)},
]
