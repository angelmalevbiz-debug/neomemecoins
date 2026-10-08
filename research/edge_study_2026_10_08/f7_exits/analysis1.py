"""F7 step 1 (TRAIN ONLY): validate the path lab against H.simulate, then map MFE/MAE and tail shares."""
import sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import exitlab as X
from common import UNIVERSES

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
t0 = time.time()
data = X.load_events()
evs_by_sig = data['events']
series, meta = H.load()
cut = H.split_t(meta)

# ---------------- 1. validation vs H.simulate (FIX -5/+10/60 on random events, full span - mechanics only)
rnd = evs_by_sig['rnd']
lab = X.Lab(rnd)
want = {(e['pair'], e['i']) for e in rnd}
tr = H.simulate(lambda P: (P.static('pair'), P.i) in want, stop=-5, tp=10, hold_min=60, cooldown_s=0,
                size_fn=lambda P: min(200.0, 0.002 * P('liq')), dexes=('pumpswap',))
by = {(x['pair'], x['entry_t']): x for x in tr}
pol = (('S', -5), ('T', 10), ('H', 60))
m = d0 = d50 = 0
worst = 0.0
for idx, e in enumerate(rnd):
    x = by.get((e['pair'], e['t']))
    if x is None:
        continue
    r = lab.fill(idx, lab.trigger(idx, pol))
    m += 1
    worst = max(worst, abs(r[0] - x['net0']), abs(r[1] - x['net50']))
print('validation: simulate trades', len(tr), 'matched events', m, 'max abs diff (pct pts)', round(worst, 6),
      'secs', round(time.time() - t0, 1))


# ---------------- 2. MFE / MAE map (TRAIN events only)
def q(xs, f):
    return xs[min(len(xs) - 1, int(f * len(xs)))]


def mfe_map(name, evs):
    tr_idx = [i for i, e in enumerate(evs) if e['t'] < cut]
    if len(tr_idx) < 20:
        print(name, 'n', len(tr_idx), 'too few')
        return
    for hz in (60, 240):
        mfe, mae, fin, mfe_first = [], [], [], 0
        reach = {x: 0 for x in (2, 5, 10, 20, 50, 100)}
        dd = {x: 0 for x in (-5, -10, -20, -50)}
        up10_before_dn5 = 0
        for i in tr_idx:
            e = evs[i]
            nets, dts = e['nets'], e['dts']
            hi, lo, last = -1e9, 1e9, None
            t_up = t_dn = None
            for p in range(len(nets)):
                if dts[p] > hz * 60:
                    break
                v = nets[p]
                hi, lo, last = max(hi, v), min(lo, v), v
                if t_up is None and v >= 10:
                    t_up = p
                if t_dn is None and v <= -5:
                    t_dn = p
            if last is None:
                continue
            mfe.append(hi)
            mae.append(lo)
            fin.append(last)
            for x in reach:
                reach[x] += hi >= x
            for x in dd:
                dd[x] += lo <= x
            if t_up is not None and (t_dn is None or t_up < t_dn):
                up10_before_dn5 += 1
        n = len(mfe)
        mfe.sort(); mae.sort(); fin.sort()
        print('  %s hz%dm n=%d pairs=%d | MFE p50 %.1f p75 %.1f p90 %.1f p95 %.1f p99 %.1f | MAE p50 %.1f p25 %.1f p10 %.1f | '
              'final mean %.2f p50 %.2f p10 %.1f p90 %.1f' % (
                  name, hz, n, len({evs[i]['pair'] for i in tr_idx}), q(mfe, .5), q(mfe, .75), q(mfe, .9), q(mfe, .95), q(mfe, .99),
                  q(mae, .5), q(mae, .25), q(mae, .1), sum(fin) / n, q(fin, .5), q(fin, .1), q(fin, .9)))
        print('     P(MFE>=x):', {x: round(100 * c / n, 1) for x, c in reach.items()},
              ' P(MAE<=x):', {x: round(100 * c / n, 1) for x, c in dd.items()},
              ' P(+10 before -5): %.1f%%' % (100 * up10_before_dn5 / n))


def tail_share(name, lab, evs, pol, label):
    tr_idx = [i for i, e in enumerate(evs) if e['t'] < cut]
    if len(tr_idx) < 20:
        return
    res = lab.run(pol, tr_idx)
    usd = sorted(((r[1] * evs[i]['size'] / 100), evs[i]['pair']) for r, i in zip(res, tr_idx))
    tot = sum(u for u, _ in usd)
    k = max(1, int(round(0.05 * len(usd))))
    top = usd[-k:]
    gains = sum(u for u, _ in usd if u > 0)
    print('   tail %-22s %-12s n=%d sum$50=%.1f mean%%50=%.2f | top5%% (%d trades, %d pairs) sum$=%.1f = %.0f%% of gains | rest sum$=%.1f' % (
        name, label, len(usd), tot, sum(r[1] for r in res) / len(res), k, len({p for _, p in top}),
        sum(u for u, _ in top), 100 * sum(u for u, _ in top) / gains if gains > 0 else 0, tot - sum(u for u, _ in top)))


for sig, evs in evs_by_sig.items():
    print('==== signal', sig)
    lab = X.Lab(evs)
    for uname, tags in UNIVERSES.items():
        sub_idx = [i for i, e in enumerate(evs) if e['tag'] in tags]
        sub = [evs[i] for i in sub_idx]
        mfe_map('%s/%s' % (sig, uname), sub)
        for pol, label in (((('H', 60),), 'hold60'), ((('H', 240),), 'hold240'),
                           ((('S', -5), ('T', 10), ('H', 60)), 'fix-5/+10/60')):
            # tail share uses the parent lab (indices into evs)
            tr_idx = [i for i in sub_idx if evs[i]['t'] < cut]
            if len(tr_idx) < 20:
                continue
            res = lab.run(pol, tr_idx)
            usd = sorted((r[1] * evs[i]['size'] / 100, evs[i]['pair']) for r, i in zip(res, tr_idx))
            tot = sum(u for u, _ in usd)
            k = max(1, int(round(0.05 * len(usd))))
            top = usd[-k:]
            gains = sum(u for u, _ in usd if u > 0)
            print('   tail %-10s %-13s n=%d sum$50=%.1f mean%%50=%.2f | top5%% (%d tr, %d pairs) sum$=%.1f (%.0f%% of gross gains) | rest sum$=%.1f' % (
                uname, label, len(usd), tot, sum(r[1] for r in res) / len(res), k, len({p for _, p in top}),
                sum(u for u, _ in top), 100 * sum(u for u, _ in top) / gains if gains > 0 else 0, tot - sum(u for u, _ in top)))
print('secs', round(time.time() - t0, 1))
