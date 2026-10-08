"""Audit step 14: trade-level realism vs the on-chain tape. For trades on tape-covered pools, compare the harness gross
price ratio (exit fill / entry fill, DexScreener prices) with the tape ratio mid(trigger + 2 s) / mid(decision + 2 s),
mid = sqrt(last BUY print * last SELL print) within 20 s. Positive 'optimism' = harness return above the tape return."""
import bisect, collections, math, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
sys.path.insert(0, DEEP); sys.path.insert(0, DEEP + '/audit')
import harness as H1
import harness_v2 as H2
from tapelib import load_tape
from smoke_common import rnd

H2._LOADED = H1.load()
series, meta = H1.load()
ps = {p for p, s in series.items() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1}
tape = load_tape(ps, min_events=100)


class Mid:
    def __init__(self, tp):
        ts, px, buy, qa = tp
        self.bt = [t for t, b in zip(ts, buy) if b]; self.bp = [p for p, b in zip(px, buy) if b]
        self.st = [t for t, b in zip(ts, buy) if not b]; self.sp = [p for p, b in zip(px, buy) if not b]

    def at(self, t, win=20_000):
        a = bisect.bisect_right(self.bt, t) - 1
        b = bisect.bisect_right(self.st, t) - 1
        if a < 0 or b < 0 or t - self.bt[a] > win or t - self.st[b] > win:
            return None
        return math.sqrt(self.bp[a] * self.sp[b])


mids = {p: Mid(tp) for p, tp in tape.items()}


def mom(H, up, thr=1.03):
    def sig(P):
        if not (P('liq') >= 30_000):
            return False
        a, b = P('price'), P.ago('price', 60)
        if not (a > 0 and b > 0):
            return False
        return a / b > thr if up else a / b < 1 / thr
    return sig


cfgs = [
    ('random (tape pools)', lambda H: dict(signal=lambda P: rnd(0.02)(P), stop=-5, tp=10, hold_min=60, pairs=set(tape))),
    ('momentum +3%/60s (tape pools)', lambda H: dict(signal=mom(H, True), stop=-5, tp=10, hold_min=60, pairs=set(tape))),
    ('dip -3%/60s (tape pools)', lambda H: dict(signal=mom(H, False), stop=-5, tp=10, hold_min=60, pairs=set(tape))),
    ('momentum tight -3/+3/15 (tape pools)', lambda H: dict(signal=mom(H, True), stop=-3, tp=3, hold_min=15, pairs=set(tape))),
]
for name, mk in cfgs:
    print('==', name)
    for label, H in (('v1', H1), ('v2', H2)):
        tr = H.simulate(**mk(H))
        rows = collections.defaultdict(list)
        for x in tr:
            if x['flag']:
                continue
            s = series[x['pair']]
            ts = s['t']
            j = bisect.bisect_left(ts, x['entry_t']); e = bisect.bisect_left(ts, x['exit_t'])
            if label == 'v1':
                dec_t, trig_t = ts[j - 1], ts[e - 1]
            else:
                dec_t, trig_t = x['decision_t'], x['trigger_t']
            M = mids[x['pair']]
            a, b = M.at(dec_t + 2000), M.at(trig_t + 2000)
            if not (a and b):
                continue
            h = math.log(s['price'][e] / s['price'][j]) * 100
            t = math.log(b / a) * 100
            rows[x['reason']].append(h - t)
            rows['ALL'].append(h - t)
            rows['ALL_abs'].append(abs(h - t))
        for r in ('ALL', 'STOP', 'TP', 'HOLD'):
            xs = sorted(rows.get(r, []))
            if not xs:
                continue
            extra = ' mean|diff| %.2f%%' % (sum(rows['ALL_abs']) / len(rows['ALL_abs'])) if r == 'ALL' else ''
            print('  %s %-5s tape-checked n %4d of %4d | optimism mean %+6.2f%% median %+6.2f%%%s' % (
                label, r, len(xs), len(tr), sum(xs) / len(xs), xs[len(xs) // 2], extra))
