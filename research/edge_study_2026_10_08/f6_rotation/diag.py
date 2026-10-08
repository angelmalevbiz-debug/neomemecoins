"""F6 diagnostics (descriptive only, nothing is selected from this): cost vs gross decomposition of the
eligible universe and of the selected configs' trades; paths of the biggest holdout losers."""
import sys, os, json
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
import strategies as S
from panel import load_panel, ok

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
d = load_panel()
U = lambda r: (not r['rug']) and r['liq'] >= 50_000 and ok(r['rt']) and r['rt'] <= 4.0
for label, lo, hi in (('train', 0, cut), ('holdout', cut, 1e20)):
    rows = [r for r in d['rows'] if lo <= r['T'] < hi and U(r)]
    for h in (900, 3600):
        g, c, n0, n5 = [], [], [], []
        for r in rows:
            fw = r['fw'][h]
            if fw is None:
                continue
            g.append(fw[0] + r['rt'])
            c.append(r['rt'])
            n0.append(fw[0])
            n5.append(fw[1])
        m = lambda v: round(sum(v) / len(v), 2)
        print('U50 %s h%d: n %d  gross~ %.2f  modeled RT cost %.2f  net0 %.2f  net50 %.2f' % (label, h, len(g), m(g), m(c), m(n0), m(n5)))
    # fee mix
    fb = {}
    for r in rows:
        k = 'fee<=50' if r['fee'] <= 50 else ('fee55-95' if r['fee'] <= 95 else 'fee100-125')
        fb[k] = fb.get(k, 0) + 1
    print('   fee mix', fb)
for cfg in S.CONFIGS:
    tr = cfg['run'](H)
    for label, sub in (('train', [x for x in tr if x['entry_t'] < cut]), ('holdout', [x for x in tr if x['entry_t'] >= cut])):
        if not sub:
            continue
        rt = sum(x['rt_cost'] or 0 for x in sub) / len(sub)
        n0 = sum(x['net0'] for x in sub) / len(sub)
        print('%s %s n %d avg RT cost %.2f  net0 %.2f  gross~ %.2f  avg fee %.0f bps  avg liq $%.0fk' % (
            cfg['name'], label, len(sub), rt, n0, n0 + rt, sum(x['fee_bps'] for x in sub) / len(sub), sum(x['liq'] for x in sub) / len(sub) / 1e3))
# paths of big losers
for pre in ('HMzvsEEm', '4JAnKFdd', '69fyvgoT'):
    s = next(v for k, v in series.items() if k.startswith(pre))
    ts = s['t']
    import bisect
    a = bisect.bisect_left(ts, cut)
    pts = [a + (len(ts) - 1 - a) * q // 8 for q in range(9)] if a < len(ts) else []
    print(s['sym'], pre, 'age_d at cut %.1f' % (s['age'][min(a, len(ts) - 1)] / 1440),
          'holdout path (h from cut, price idx, liq $k, fee):',
          [(round((ts[k] - cut) / 3.6e6, 1), round(s['price'][k] / s['price'][a] * 100), round(s['liq'][k] / 1e3), H.fee_bps(s, k)) for k in pts])
