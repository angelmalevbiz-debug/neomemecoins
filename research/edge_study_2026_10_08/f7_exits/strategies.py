"""F7 exit engineering - the (at most 3) configs pre-selected on TRAIN, plus count-matched random baselines.

Selection (train only, H.simulate position semantics): 60,714 (signal x universe x exit) configs were scored on
train. Only 26 had mean net50 > 0 with n >= 30 and >= 8 pairs - all momentum entries in the hi20 universe with
a tight (2-pt) net trailing stop - and none survived the ex-top-pair check. They are pre-registered here so the
holdout can answer the F7 hypothesis honestly; they are NOT expected to qualify.

Every config is rug-screened (H.interim_rug_risk inside utag) and uses PumpSwap SOL pools only (harness default).
"""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

NO_STOP = -1000.0   # harness needs a numeric stop; -1000 % never triggers


# ------------------------------------------------------------------ universes / signals (past-only)
def size_rule(P):
    """$200, scaled down in thin pools so modeled impact stays <= 0.4 %/leg (N <= 0.2 % of liquidity)."""
    liq = P('liq')
    return min(200.0, 0.002 * liq) if liq > 0 else 0.0


def utag(P):
    """F7 universe tag at the decision point; None = outside. Always rug-screened."""
    liq = P('liq')
    if not (liq >= 20_000):
        return None
    if H.interim_rug_risk(P):
        return None
    fee = P.fee_bps()
    if fee <= 50:
        if liq >= 250_000:
            size = min(200.0, liq * 0.001)
            rt = P.rt_cost_pct(size)
            if rt is not None and rt <= 1.2:
                return 'cf'
        return 'lowfee' if liq >= 50_000 else 'other20'
    if fee <= 95:
        return 'mid' if liq >= 50_000 else 'other20'
    return 'hi' if liq >= 50_000 else 'hi20'


ALL50 = ('cf', 'lowfee', 'mid', 'hi')


def sig_mom(P):
    """A-priori momentum: DexScreener 5m change >= +3 %, 1h change >= +10 %, more 5m buys than sells."""
    return P('pc5') >= 3 and P('pc1h') >= 10 and P('b5') > P('s5')


def mom_hi20(P):
    """Momentum in rug-screened high-fee (100-125 bps) PumpSwap pools with $20k-50k liquidity."""
    return sig_mom(P) and utag(P) == 'hi20'


def rnd_in(tags, prob, salt):
    def f(P):
        return utag(P) in tags and H.hashed_coin(P.static('pair'), P.t, prob, salt)
    return f


def breakeven_after_arm(arm, floor):
    def f(P, pos):
        return 'BE' if pos['peak'] >= arm and pos['net'] <= floor else None
    return f


# ------------------------------------------------------------------ exits
EXIT_TRAIL_TIGHT = dict(stop=-35.0, tp=None, trail_arm=3.0, trail=2.0, hold_min=240, size_fn=size_rule, cooldown_s=300)
EXIT_TAIL40 = dict(stop=-35.0, tp=None, trail_arm=40.0, trail=2.0, hold_min=240, size_fn=size_rule, cooldown_s=300)
EXIT_BE = dict(stop=NO_STOP, tp=20.0, hold_min=120, exit_fn=breakeven_after_arm(3.0, 0.5), size_fn=size_rule,
               cooldown_s=300)

CONFIGS = [
    {'name': 'F7_MOM_HI20_TRAIL3x2',
     'description': 'Momentum (pc5>=3, pc1h>=10, b5>s5) in rug-screened hi-fee $20-50k PumpSwap pools; '
                    'stop -35 net, net trailing stop armed at +3 with 2-pt width, 240 min time stop. '
                    'Train-best of 60,714 exit/universe/signal configs under exact harness semantics.',
     'signal': mom_hi20, 'kwargs': EXIT_TRAIL_TIGHT},
    {'name': 'F7_MOM_HI20_TAIL40',
     'description': 'Same entries; fat-tail capture: stop -35, trail armed only at +40 net (2-pt width), '
                    '240 min time stop. The F7 hypothesis exit (let the right tail run).',
     'signal': mom_hi20, 'kwargs': EXIT_TAIL40},
    {'name': 'F7_RANDOM_ALL50_BEST_EXIT',
     'description': 'Exit-only ceiling: RANDOM entries in rug-screened PumpSwap pools with liq >= $50k (all fee tiers) '
                    'with the train-best exit for that universe (no stop, TP +20, breakeven-after-+3 at +0.5, 120 min). '
                    'Tests whether an exit policy alone can make expectancy positive.',
     'signal': rnd_in(ALL50, 0.01, 'f7'), 'kwargs': EXIT_BE},
]

# Count-matched random-entry baselines (same universe, same exits). Probabilities calibrated on TRAIN trade
# counts only (calibrate.py: config train n 109 / 92 / 182 vs baseline 103 / 86 / 178), never on performance.
BASELINES = [
    {'name': 'BASE_RND_HI20_TRAIL3x2', 'description': 'Random entries in the hi20 universe, exits of F7_MOM_HI20_TRAIL3x2.',
     'signal': rnd_in(('hi20',), 0.01, 'f7base'), 'kwargs': EXIT_TRAIL_TIGHT},
    {'name': 'BASE_RND_HI20_TAIL40', 'description': 'Random entries in the hi20 universe, exits of F7_MOM_HI20_TAIL40.',
     'signal': rnd_in(('hi20',), 0.02, 'f7base'), 'kwargs': EXIT_TAIL40},
    {'name': 'BASE_RND_ALL50_BEST_EXIT', 'description': 'Random entries (other salt) in the all50 universe, exits of F7_RANDOM_ALL50_BEST_EXIT.',
     'signal': rnd_in(ALL50, 0.01, 'f7base'), 'kwargs': EXIT_BE},
]
