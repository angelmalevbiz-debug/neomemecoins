"""Verifier B's independent re-implementation of F5_C1_TAPE_BREADTH_NOCHASE (PAPER research only; NOT a candidate).

(strategies.py in this folder was written by a parallel verifier run with the same label; this file is the second,
separately coded re-implementation. Same API: CONFIGS / BASELINES.)

Written from the rule's plain-language description, without the family's tapefeat.py / tape_cache.pkl:
  universe: PumpSwap SOL pair (harness default dexes), fee tier 52.5-125 bps, liquidity >= $20k, not H.interim_rug_risk,
            tape-live = the newest swap already INGESTED at t (available <= t) has block time within 600 s of t
  flow, last 300 s by block time, only swaps with available <= t (read-only from tape_snapshot.sqlite3):
            >= 10 distinct buyer wallets; >= 5 first-time buyer WALLETS (wallet's earliest swap on this pool known at t
            lies inside the window - evaluated at decision time); largest buyer < 30% of buy SOL; buy SOL > sell SOL
  no chase: price change vs the latest point <= t-300 s within [-2%, +5%]
  exits: stop -10% net, tp +6% net, 30 min max hold; size min($200, 0.2% of liquidity); cooldown 300 s
Under leaderboard/harness_final.py: 'orig' -> 91 trades (holdout 22, +$0.943); 'rug_guard_v1' -> 66 trades
(holdout 11 on 5 pairs, +$6.106 stressed) = the leaderboard row, trade for trade in the holdout.
Signals keep incremental per-pair state; a non-monotonic call for a pair resets that pair's state (safe to re-use).
"""
import bisect, json, sqlite3, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H  # noqa: E402

_DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
_TAPE = None


def _tape():
    global _TAPE
    if _TAPE is None:
        con = sqlite3.connect('file:%s/tape_snapshot.sqlite3?mode=ro' % _DEEP, uri=True)
        per = {}
        for pair, et, av, payload in con.execute('SELECT pair, event_time, available, payload FROM events'):
            p = json.loads(payload)
            if p.get('confirmed_swap') is not True or p.get('direction') not in ('BUY', 'SELL'):
                continue
            try:
                q = float(p.get('quote_amount'))
            except (TypeError, ValueError):
                continue
            if not (q >= 0):
                continue
            per.setdefault(pair, []).append((int(et), int(av), 1 if p['direction'] == 'BUY' else -1, q,
                                             p.get('wallet') or ''))
        con.close()
        T = {}
        for pair, v in per.items():
            v.sort(key=lambda x: (x[0], x[1]))
            byav = sorted(range(len(v)), key=lambda k: (v[k][1], v[k][0]))
            T[pair] = {'et': [x[0] for x in v], 'av': [x[1] for x in v], 'sgn': [x[2] for x in v],
                       'q': [x[3] for x in v], 'w': [x[4] for x in v], 'byav': byav,
                       'av_sorted': [v[k][1] for k in byav]}
        _TAPE = T
    return _TAPE


def _fin(x):
    return x == x and x not in (float('inf'), float('-inf'))


class _Flow:
    def __init__(self):
        self.ptr, self.wmin, self.last_t = {}, {}, {}

    def features(self, pair, t, window_s=300):
        d = _tape().get(pair)
        if d is None:
            return None
        if self.last_t.get(pair, -1) > t:
            self.ptr[pair], self.wmin[pair] = 0, {}
        self.last_t[pair] = t
        p, wm = self.ptr.get(pair, 0), self.wmin.setdefault(pair, {})
        byav, et, w, avs = d['byav'], d['et'], d['w'], d['av_sorted']
        while p < len(byav) and avs[p] <= t:
            k = byav[p]
            if wm.get(w[k]) is None or et[k] < wm[w[k]]:
                wm[w[k]] = et[k]
            p += 1
        self.ptr[pair] = p
        av = d['av']
        hi = bisect.bisect_right(et, t)
        k = hi - 1
        while k >= 0 and av[k] > t:
            k -= 1
        if k < 0:
            return None
        lo = bisect.bisect_right(et, t - window_s * 1000, 0, hi)
        ws = t - window_s * 1000
        buyers, bsol, ssol = {}, 0.0, 0.0
        for k2 in range(lo, hi):
            if av[k2] > t:
                continue
            if d['sgn'][k2] > 0:
                bsol += d['q'][k2]
                buyers[w[k2]] = buyers.get(w[k2], 0.0) + d['q'][k2]
            else:
                ssol += d['q'][k2]
        return {'age_s': (t - et[k]) / 1000.0, 'ub': len(buyers),
                'newb': sum(1 for x in buyers if wm.get(x, -1) > ws),
                'top_share': (max(buyers.values()) / bsol) if (buyers and bsol > 0) else float('nan'),
                'net_sol': bsol - ssol}


def _universe_nochase(P, flow):
    if not (P('liq') >= 20_000) or not (52.5 <= P.fee_bps() <= 125) or H.interim_rug_risk(P):
        return None
    ft = flow.features(P.static('pair'), P.t)
    if ft is None or ft['age_s'] > 600:
        return None
    p0, p1 = P('price'), P.ago('price', 300)
    if not (_fin(p0) and _fin(p1) and p1 > 0 and -2 <= 100 * (p0 / p1 - 1) <= 5):
        return None
    return ft


def _make_c1():
    flow = _Flow()

    def sig(P):
        ft = _universe_nochase(P, flow)
        return bool(ft and ft['ub'] >= 10 and ft['newb'] >= 5 and _fin(ft['top_share']) and ft['top_share'] < 0.3
                    and ft['net_sol'] > 0)
    return sig


def _make_rnd(prob, salt):
    flow = _Flow()

    def sig(P):
        return bool(_universe_nochase(P, flow)) and H.hashed_coin(P.static('pair'), P.t, prob, salt)
    return sig


def _size(P):
    return min(200.0, 0.002 * P('liq'))


EXITS = dict(stop=-10.0, tp=6.0, hold_min=30.0, size_fn=_size)

CONFIGS = [
    {'name': 'VERIFY_B_F5_C1_REIMPL_WALLET',
     'description': 'Independent re-implementation of F5_C1_TAPE_BREADTH_NOCHASE (verification only, not a candidate): '
                    'tape-live PumpSwap pool, fee 52.5-125 bps, liq >= $20k, interim rug screen; last 5 min >= 10 '
                    'distinct buyers, >= 5 first-time buyer wallets, top buyer < 30% of buy SOL, net SOL inflow > 0; '
                    '5-min price change in [-2%, +5%]; exits -10/+6 net, 30 min.',
     'signal': _make_c1(), 'kwargs': dict(EXITS)},
]

BASELINES = [
    {'name': 'VERIFY_B_F5_C1_RANDOM_SAME_UNIVERSE',
     'description': 'Random entries (hashed coin p=0.007/point, salt vA) in the same tape-live, no-chase universe, '
                    'same exits and sizing.',
     'signal': _make_rnd(0.007, 'vA'), 'kwargs': dict(EXITS)},
]
