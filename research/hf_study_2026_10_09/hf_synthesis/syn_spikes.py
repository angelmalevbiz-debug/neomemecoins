"""Shape of outsized fill prints in the chosen HF books, and the effect of a two-refresh confirmation rule
(HF_PRINT_CONFIRM_V1 candidate) on all trades. Diagnostics only (no selection). PAPER research only.

Rule tested: a fill print that moved >= 5 % from the leg's decision print is accepted only if the NEXT DexScreener
refresh (next price change within 60 s) is within 2 % of it; otherwise the leg fills at that next refresh instead; if no
further refresh exists within 60 s, the leg is valued at the less favourable of the decision and fill prints."""
import sys
import syn_lib as L

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
H = L.H
d = L.S.events()
rows, cut, meta = d['rows'], d['cut'], d['meta']
t0, t1 = meta['t0'], meta['t1']
uni = L.UNIVERSES['E95']
SHARED = dict(slots=3, hold_s=120, cooldown_s=120, pacing=('roll', 3600, 50), size=25.0, heat_on=True)


def next_refresh(s, m, lag=60_000):
    ts, px = s['t'], s['price']
    k = m + 1
    while k < len(ts) and ts[k] - ts[m] <= lag:
        if px[k] != px[m]:
            return k
        k += 1
    return None


def confirmed_price(s, dec, fill, side):
    """side 'buy' or 'sell'. Returns (price, action)."""
    px = s['price']
    p0, pf = px[dec], px[fill]
    if not (p0 > 0 and pf > 0) or abs(pf / p0 - 1) < 0.05:
        return pf, 'normal'
    k = next_refresh(s, fill)
    if k is None:
        worse = max(p0, pf) if side == 'buy' else min(p0, pf)
        return worse, 'unconfirmed_worse'
    if abs(px[k] / pf - 1) <= 0.02:
        return pf, 'confirmed'
    return px[k], 'replaced'


for name, pred in (('RND', L.rnd(0.25, 'hfA')), ('QUIET', L.PREDICATES['QUIET']), ('DIP15', L.PREDICATES['NEAR15LOW'])):
    for lab, a, b in (('train', t0, cut), ('holdout', cut, t1)):
        tr, _ = L.run_book(rows, uni, pred, t_from=a, t_to=b, **SHARED)
        acts = {}
        new_gross = []
        for x in tr:
            s = L.SERIES[x['pair']]
            ts, px = s['t'], s['price']
            i = x['i']
            j = H.fill_index(s, i)
            if x['flag'] or j is None:
                new_gross.append(x['gross'])
                continue
            k = next(kk for kk in range(j + 1, len(ts)) if ts[kk] - ts[i] >= 120_000) if any(
                ts[kk] - ts[i] >= 120_000 for kk in range(j + 1, len(ts))) else None
            e = H.fill_index(s, k) if k is not None else None
            if k is None or e is None:
                new_gross.append(x['gross'])
                continue
            pe, ae = confirmed_price(s, i, j, 'buy')
            px_x, ax = confirmed_price(s, k, e, 'sell')
            acts[ae] = acts.get(ae, 0) + 1
            acts['x_' + ax] = acts.get('x_' + ax, 0) + 1
            new_gross.append(100 * (px_x / pe - 1) if pe > 0 else x['gross'])
        old = [x['gross'] for x in tr if L.fin(x['gross'])]
        new = [g for g in new_gross if L.fin(g)]
        print('%-5s %-7s n %4d mean gross %+.3f -> %+.3f | max %+.1f -> %+.1f min %+.1f -> %+.1f | actions %s' % (
            name, lab, len(tr), sum(old) / len(old), sum(new) / len(new), max(old), max(new), min(old), min(new), acts))
# shape of the HMzvsEEm prints
P = [p for p in L.SERIES if p.startswith('HMzvsEEm')][0]
s = L.SERIES[P]
ts, px, lq = s['t'], s['price'], s['liq']
jumps = [m for m in range(1, len(ts)) if px[m - 1] > 0 and abs(px[m] / px[m - 1] - 1) >= 0.2]
print('HMzvsEEm points', len(ts), 'jumps >= 20 %:', len(jumps))
for m in jumps[:12]:
    lo, hi = max(0, m - 2), min(len(ts), m + 4)
    print('  ', [(round((ts[q] - ts[m]) / 1000), round(px[q] / px[m - 1], 3), round(lq[q] / 1e6, 2)) for q in range(lo, hi)])
