"""Audit step 11: v2 regression tests.
(a) v2 with FILL_RULE='next_point', FEED_GAP_MS=None, TIME_ORDERED=False must reproduce v1 trades exactly, except trades
    whose exit valuation touched a liquidity==0 point (F6).
(b) v2 TIME_ORDERED True vs False must give identical trades for stateless signals.
(c) forward_net in v1-mode equals v1 wherever v1 returns a value.
(d) a cross-pair stateful signal sees no future in v2 (calls are time-ordered)."""
import sys, time, bisect
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
sys.path.insert(0, DEEP)
sys.path.insert(0, DEEP + '/audit')
import harness as H1
import harness_v2 as H2
from smoke_common import cost_first, rnd

H2._LOADED = H1.load()   # share the same loaded series objects
series, meta = H1.load()


def key(x):
    return (x['pair'], x['entry_t'], x['exit_t'], x['reason'], x['flag'], round(x['net0'], 9), round(x['net50'], 9))


def ex_fn(P, pos):
    return 'PC5' if P('pc5') < -15 else None


cfgs = [
    ('random_all', lambda H: dict(signal=lambda P: rnd(0.002)(P), stop=-5, tp=10, hold_min=60)),
    ('cf_guard', lambda H: dict(signal=lambda P: cost_first(P) and not H.interim_rug_risk(P) and rnd(0.01)(P), stop=-5, tp=10,
                                hold_min=60, size_fn=lambda P: min(200.0, P('liq') * 0.001))),
    ('mid_trail_exitfn', lambda H: dict(signal=lambda P: 50 < P.fee_bps() <= 125 and P('liq') >= 50_000 and rnd(0.004)(P),
                                        stop=-8, tp=None, trail_arm=4, trail=3, hold_min=120, exit_fn=ex_fn, cooldown_s=60)),
]


def liq0_touch(x):
    s = series[x['pair']]
    a = bisect.bisect_left(s['t'], x['entry_t']); b = bisect.bisect_left(s['t'], x['exit_t'])
    return any(s['liq'][k] == 0 for k in range(a, b + 1))


for name, mk in cfgs:
    t0 = time.time()
    a = H1.simulate(**mk(H1))
    t1 = time.time()
    H2.FILL_RULE, H2.FEED_GAP_MS, H2.TIME_ORDERED = 'next_point', None, False
    b = H2.simulate(**mk(H2))
    ka = sorted(key(x) for x in a); kb = sorted(key(x) for x in b)
    diff_a = [x for x in a if key(x) not in set(kb)]
    explained = sum(1 for x in diff_a if liq0_touch(x))
    print('(a) %-18s v1 n %d (%.1fs) | v2-in-v1-mode n %d | identical %s | differing %d, explained by liq==0 (F6) %d' % (
        name, len(a), t1 - t0, len(b), ka == kb, len(diff_a), explained))
    H2.FILL_RULE, H2.FEED_GAP_MS = 'next_refresh', 600_000
    t2 = time.time()
    H2.TIME_ORDERED = False
    c = H2.simulate(**mk(H2))
    t3 = time.time()
    H2.TIME_ORDERED = True
    d = H2.simulate(**mk(H2))
    t4 = time.time()
    print('(b) %-18s v2 pair-ordered n %d (%.1fs) vs time-ordered n %d (%.1fs) identical %s' % (
        name, len(c), t3 - t2, len(d), t4 - t3, sorted(key(x) for x in c) == sorted(key(x) for x in d)))

# (c) forward_net
H2.FILL_RULE, H2.FEED_GAP_MS = 'next_point', None
bad = cnt = extra = 0
for p, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    for i in range(0, len(s['t']) - 1, 97):
        for hz in (300, 3600):
            v1 = H1.forward_net(s, i, hz)
            v2 = H2.forward_net(s, i, hz)
            if v1 is not None:
                cnt += 1
                if v2 is None or abs(v1 - v2) > 1e-9:
                    bad += 1
            elif v2 is not None:
                extra += 1
print('(c) forward_net v1-mode: compared %d, mismatches %d, extra values for vanished pairs (F3) %d' % (cnt, bad, extra))
H2.FILL_RULE, H2.FEED_GAP_MS, H2.TIME_ORDERED = 'next_refresh', 600_000, True

# (d) cross-pair state: a signal that records the max time it has seen; any call earlier than that is a future leak
for label, H in (('v1', H1), ('v2', H2)):
    st = {'maxt': -1, 'violations': 0, 'calls': 0}

    def sig(P, st=st):
        st['calls'] += 1
        if P.t < st['maxt']:
            st['violations'] += 1
        st['maxt'] = max(st['maxt'], P.t)
        return False
    H.simulate(sig, pairs=set(list(series)[:300]))
    print('(d) %s cross-pair state: %d of %d signal calls happened after a call at a later time' % (label, st['violations'], st['calls']))
