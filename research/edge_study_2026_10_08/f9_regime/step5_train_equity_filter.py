"""Step 5 (TRAIN ONLY): equity-curve regime filter. A shadow book runs the unfiltered base strategy;
the real book only enters when the shadow trades that CLOSED in the last W minutes (exit time <= now)
averaged net50 >= threshold ('hot') or < threshold ('cold'). Past-only: a shadow trade's outcome is used
only after its exit fill time."""
import os, pickle, sys, bisect
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import f9lib as L
H = L.H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
E2 = dict(stop=-15, tp=20, hold_min=60)
E3 = dict(stop=-99, tp=None, hold_min=30)
configs = 0


def recent_mean_fn(shadow, window_min, min_n=3):
    sh = sorted(shadow, key=lambda x: x['exit_t'])
    ex = [x['exit_t'] for x in sh]
    pre = [0.0]
    for x in sh:
        pre.append(pre[-1] + x['net50'])

    def f(now):
        hi = bisect.bisect_right(ex, now)
        lo = bisect.bisect_right(ex, now - window_min * 60_000)
        n = hi - lo
        return None if n < min_n else (pre[hi] - pre[lo]) / n
    return f


def line(tr):
    if not tr:
        return 'n=0'
    sm = H.summarize(tr)
    return 'n=%3d pairs=%2d mean50=%6.2f med=%6.2f win=%4.1f pf=%s top=%.2f ci=%s' % (
        sm['n'], sm['pairs'], sm['mean_pct'], sm['median_pct'], sm['win_rate'], sm['pf'], sm['top_pair_share'], sm['ci95_mean_usd'])


for bn, base in (('RANDOM', L.sig_random(0.004)), ('MOMO', L.sig_momentum), ('DIP', L.sig_dip)):
    for en, kw in (('E2', E2), ('E3', E3)):
        shadow = H.simulate(base, **kw)   # full-span shadow; only exits <= decision time are ever read
        for w in (60, 120):
            rm = recent_mean_fn(shadow, w)
            for label in ('hot>=0', 'cold<-4'):
                if label == 'hot>=0':
                    sig = lambda P, rm=rm, base=base: base(P) and (lambda v: v is not None and v >= 0.0)(rm(P.t))
                else:
                    sig = lambda P, rm=rm, base=base: base(P) and (lambda v: v is not None and v < -4.0)(rm(P.t))
                tr = H.simulate(sig, t_to=cut, **kw)
                configs += 1
                print('  %-6s %s W=%3d %-8s %s' % (bn, en, w, label, line(tr)))
print('configs_tried step5:', configs)
