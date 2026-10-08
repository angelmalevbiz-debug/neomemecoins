"""F5 flow acceleration / buyer breadth - the 3 configs pre-selected on TRAIN, plus random baselines.

All signals are past-only:
- DexScreener fields via the harness Past view P.
- On-chain tape flow via tapefeat.flow(pair, P.t, window): only swaps with block time <= P.t AND ingestion time
  (`available`) <= P.t are counted; the "new wallet" flag is fixed at each event's own ingestion time.
- Every config applies the interim rug screen (H.interim_rug_risk) and PumpSwap fee 52.5-125 bps, liquidity >= $20k,
  and requires the pool to be tape-live (an ingested swap within the last 10 minutes).
Exits (chosen on train): stop -10% net, take profit +6% net, max hold 30 min; size min($200, 0.2% of pool liquidity).
"""
import sys, os
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

_HERE = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f5_flowaccel'
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import tapefeat as TF  # noqa: E402  (past-only tape reader; builds tape_cache.pkl from tape_snapshot.sqlite3 once)


def _fin(x):
    return x == x and x not in (float('inf'), float('-inf'))


def _universe(P):
    liq = P('liq')
    if not (liq >= 20_000):
        return False
    if not (52.5 <= P.fee_bps() <= 125):
        return False
    if H.interim_rug_risk(P):
        return False
    age = TF.last_available_age_s(P.static('pair'), P.t)
    return age is not None and age <= 600


def _nochase(P):
    p0, p1 = P('price'), P.ago('price', 300)
    if not (_fin(p1) and p1 > 0 and _fin(p0)):
        return False
    r = 100 * (p0 / p1 - 1)
    return -2 <= r <= 5


def _tape_breadth(P):
    f = TF.flow(P.static('pair'), P.t, 300)
    return (f is not None and f['ub'] >= 10 and f['newb'] >= 5 and _fin(f['top_share'])
            and f['top_share'] < 0.3 and f['net_sol'] > 0)


def _ds_twin(P):
    b, s = P('b5'), P('s5')
    return _fin(b) and _fin(s) and b >= 10 and (b + s) > 0 and b / (b + s) >= 0.55


def sig_c1(P):
    return _universe(P) and _nochase(P) and _tape_breadth(P)


def sig_c2(P):
    return _universe(P) and _tape_breadth(P)


def sig_c3(P):
    return _universe(P) and _nochase(P) and _ds_twin(P)


def _size(P):
    return min(200.0, 0.002 * P('liq'))


EXITS = dict(stop=-10.0, tp=6.0, hold_min=30.0, size_fn=_size)

CONFIGS = [
    {'name': 'F5_C1_TAPE_BREADTH_NOCHASE',
     'description': 'Tape-live PumpSwap pool (fee 52.5-125 bps, liq>=$20k, rug-screened): in the last 5 min >=10 distinct '
                    'buyer wallets, >=5 first-time buyers, top buyer <30% of buy SOL, net SOL inflow > 0, and price '
                    'change over 5 min within [-2%, +5%] (do not chase). Exit -10/+6 net, 30 min max hold.',
     'signal': sig_c1, 'kwargs': dict(EXITS)},
    {'name': 'F5_C2_TAPE_BREADTH',
     'description': 'Same as C1 without the no-chase price filter (broader, more pairs).',
     'signal': sig_c2, 'kwargs': dict(EXITS)},
    {'name': 'F5_C3_DEXSCREENER_TWIN_NOCHASE',
     'description': 'Control for "does the tape add anything": same universe, no-chase filter and exits as C1, but the '
                    'flow condition uses DexScreener txn counts only (b5 >= 10 buys and buy share >= 55% in 5 min).',
     'signal': sig_c3, 'kwargs': dict(EXITS)},
]


def _rnd(prob, salt):
    return lambda P: H.hashed_coin(P.static('pair'), P.t, prob, salt)


_r1 = _rnd(0.0045, 'f5u')
_r2 = _rnd(0.007, 'f5nc')

BASELINES = [
    {'name': 'F5_RANDOM_TAPE_LIVE',
     'description': 'Random entries (hashed coin p=0.0045 per point) in the same tape-live universe, same exits/sizing (baseline for C2).',
     'signal': lambda P: _universe(P) and _r1(P), 'kwargs': dict(EXITS)},
    {'name': 'F5_RANDOM_TAPE_LIVE_NOCHASE',
     'description': 'Random entries (p=0.007) in the same tape-live universe with the same no-chase filter, same exits/sizing (baseline for C1 and C3).',
     'signal': lambda P: _universe(P) and _nochase(P) and _r2(P), 'kwargs': dict(EXITS)},
]
