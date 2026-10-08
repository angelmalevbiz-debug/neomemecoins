"""Shared helpers for the adversarial robustness verification of
f2_meanrev / RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB (variant rug_guard_v1).  PAPER research only, read-only data.

The verified row is itself a RANDOM-entry baseline:
    hashed_coin(pair, t, p=0.005, salt='f2r1')  AND  liq >= $50k  AND  not interim_rug_risk  AND  pc1h <= -12%
    AND not rug_guard_v1 (leaderboard variant),   exits tp +20 / stop -25 (net, model) / hold 90 min, cooldown 300 s,
    PumpSwap SOL pairs, $200 notional, simulated by leaderboard/harness_final.simulate.
make_rand() below reproduces it exactly (checked trade-for-trade against leaderboard/trades/f2_meanrev.pkl).
"""
import sys
sys.dont_write_bytecode = True
import importlib.util, json, math, os, random, time

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
OUT = DEEP + '/verify_f2_meanrev_robustness_RANDOM_ctx1h_for_F2_OVERSOLD1H'
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


H = _load_module('harness_final', DEEP + '/leaderboard/harness_final.py')
sys.modules['harness'] = H

EXIT_A = dict(tp=20.0, stop=-25.0, hold_min=90.0)
CALLS = {'simulate': 0}


def make_rand(prob=0.005, salt='f2r1', pc1h_max=-12.0, liq_min=50_000.0, rug='interim', guard=True):
    """Exact re-implementation of f2 make_random(prob, salt, pc1h_max) (+ the leaderboard rug_guard_v1 AND).
    rug: 'interim' (as submitted) | 'train_only' (H.rug_risk_train_only, the holdout-blind sensitivity) | 'none'.
    pc1h_max=None drops the oversold context (universe-only random)."""
    def signal(P):
        if prob < 1.0 and not H.hashed_coin(P.static('pair'), P.t, prob, salt):
            return False
        if not (P('liq') >= liq_min):
            return False
        if rug == 'interim' and H.interim_rug_risk(P):
            return False
        if rug == 'train_only' and H.rug_risk_train_only(P):
            return False
        if pc1h_max is not None and not (P('pc1h') <= pc1h_max):
            return False
        if guard and H.rug_guard_v1(P):
            return False
        return True
    return signal


def sim(sig, **kw):
    CALLS['simulate'] += 1
    k = dict(EXIT_A)
    k.update(kw)
    return H.simulate(sig, **k)


def meta():
    return H.load()[1]


def cut_t(frac=0.6):
    m = meta()
    return m['t0'] + frac * (m['t1'] - m['t0'])


def _pair_ci(trades, key='usd50'):
    lo, hi, n = H.cluster_ci_pair(trades, key)
    return [lo, hi]


def stats(trades, key='usd50', span_h=None):
    """Compact summary (stressed calibrated net50 by default)."""
    if not trades:
        return {'n': 0}
    s = H.summarize(trades, key, span_h=span_h)
    xs = sorted((x[key] for x in trades), reverse=True)
    tot = sum(xs)
    top5 = sum(xs[:5])
    pos = sum(v for v in xs if v > 0)
    sd = math.sqrt(sum((v - tot / len(xs)) ** 2 for v in xs) / max(1, len(xs) - 1)) if len(xs) > 1 else float('nan')
    out = {k: s.get(k) for k in ('n', 'pairs', 'clusters', 'win_rate', 'mean_usd', 'mean_pct', 'median_pct', 'sum_usd',
                                 'pf', 'ci95_mean_usd', 'ci95_mean_usd_pair', 'trades_per_hour', 'top_pair_share',
                                 'exits', 'censored_end', 'vanished', 'gap_closed')}
    out['pf'] = None if out['pf'] is None else (1e9 if out['pf'] == float('inf') else out['pf'])
    out['top5_trades_usd'] = round(top5, 2)
    out['top5_share_of_gross_profit'] = round(top5 / pos, 3) if pos > 0 else None
    out['sum_without_top5_usd'] = round(tot - top5, 2)
    out['t_stat'] = round((tot / len(xs)) / (sd / math.sqrt(len(xs))), 3) if len(xs) > 1 and sd > 0 else None
    return out


def split(trades, frac=0.6):
    c = cut_t(frac)
    m = meta()
    tr = [x for x in trades if x['entry_t'] < c]
    ho = [x for x in trades if x['entry_t'] >= c]
    return tr, ho, (c - m['t0']) / 3.6e6, (m['t1'] - c) / 3.6e6


def evaluate(trades, frac=0.6, key='usd50'):
    tr, ho, sh_tr, sh_ho = split(trades, frac)
    return {'train': stats(tr, key, sh_tr), 'holdout': stats(ho, key, sh_ho)}


def mean_usd(trades, key='usd50'):
    return round(sum(x[key] for x in trades) / len(trades), 3) if trades else None


def blocks(trades, hours=3.0, key='usd50'):
    m = meta()
    out = []
    t = m['t0']
    while t < m['t1']:
        e = min(m['t1'] + 1, t + hours * 3.6e6)
        b = [x for x in trades if t <= x['entry_t'] < e]
        out.append({'from_h': round((t - m['t0']) / 3.6e6, 2), 'to_h': round((e - m['t0']) / 3.6e6, 2), 'n': len(b),
                    'pairs': len({x['pair'] for x in b}), 'mean_usd': mean_usd(b, key),
                    'sum_usd': round(sum(x[key] for x in b), 2)})
        t = e
    return out


def drop_best(trades, key='usd50'):
    """Holdout robustness: drop the single best pair (by summed $) and the single best clock hour (by summed $)."""
    if not trades:
        return {}
    bp, bh = {}, {}
    for x in trades:
        bp[x['pair']] = bp.get(x['pair'], 0.0) + x[key]
        h = int(x['entry_t'] // 3_600_000)
        bh[h] = bh.get(h, 0.0) + x[key]
    best_pair = max(bp, key=bp.get)
    best_hour = max(bh, key=bh.get)
    wo_p = [x for x in trades if x['pair'] != best_pair]
    wo_h = [x for x in trades if int(x['entry_t'] // 3_600_000) != best_hour]
    wo_both = [x for x in wo_p if int(x['entry_t'] // 3_600_000) != best_hour]
    m = meta()
    return {'best_pair': best_pair[:8], 'best_pair_sum_usd': round(bp[best_pair], 2),
            'best_hour_offset_h': round((best_hour * 3_600_000 - m['t0']) / 3.6e6, 2),
            'best_hour_sum_usd': round(bh[best_hour], 2),
            'without_best_pair': {'n': len(wo_p), 'mean_usd': mean_usd(wo_p, key)},
            'without_best_hour': {'n': len(wo_h), 'mean_usd': mean_usd(wo_h, key)},
            'without_both': {'n': len(wo_both), 'mean_usd': mean_usd(wo_both, key)}}


def short(x):
    """One-line summary of an evaluate() result."""
    h, t = x['holdout'], x['train']
    return 'train n=%s mean $%s | holdout n=%s pairs=%s mean $%s (%s%%) CI=%s win=%s' % (
        t.get('n'), t.get('mean_usd'), h.get('n'), h.get('pairs'), h.get('mean_usd'), h.get('mean_pct'),
        h.get('ci95_mean_usd'), h.get('win_rate'))


def dump(name, obj):
    with open(os.path.join(OUT, name), 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, indent=1, default=str)
