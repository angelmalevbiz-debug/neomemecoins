"""Print-integrity rules for the HF books (diagnostics on the already-chosen configs; no selection). PAPER only.

HF_PRINT_GUARD_V1 candidate:
 (a) toggle-pool guard: a pool is not eligible for 6 h after a TOGGLE, i.e. a refresh step |dp| >= 15 % whose
     price comes back within 2 % of the pre-step price within 600 s (known, past-only, at the return point);
 (b) outlier-fill valuation: a fill print >= 15 % away from its leg's decision print is valued at the less favourable
     of the two prints (buy: higher, sell: lower) and flagged.
"""
import sys
import syn_lib as L

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
H = L.H
d = L.S.events()
rows, cut, meta = d['rows'], d['cut'], d['meta']
t0, t1 = meta['t0'], meta['t1']
uni = L.UNIVERSES['E95']
SHARED = dict(slots=3, hold_s=120, cooldown_s=120, pacing=('roll', 3600, 50), size=25.0, heat_on=True)
JUMP, BACK, WIN, BLOCK = 0.15, 0.02, 600_000, 6 * L.HOUR

# toggle events per pair (time the toggle becomes known = the return point)
need = {r['pair'] for r in rows if uni(r)}
known = {}
for p in need:
    s = L.SERIES[p]
    ts, px = s['t'], s['price']
    out = []
    for m in range(1, len(ts)):
        a, b = px[m - 1], px[m]
        if not (a > 0 and b > 0) or abs(b / a - 1) < JUMP:
            continue
        q = m + 1
        while q < len(ts) and ts[q] - ts[m] <= WIN:
            if abs(px[q] / a - 1) <= BACK:
                out.append(ts[q])
                break
            q += 1
    if out:
        known[p] = out
print('pairs with toggles', len(known), {L.ab(p): len(v) for p, v in sorted(known.items(), key=lambda kv: -len(kv[1]))[:10]})


def toggled(r):
    ts = known.get(r['pair'])
    if not ts:
        return False
    return any(t <= r['t'] < t + BLOCK for t in ts)


uni2 = lambda r: uni(r) and not toggled(r)


def value(tr):
    """(b) outlier-fill valuation re-priced on the trade's own legs (gross only; costs unchanged)."""
    out = []
    for x in tr:
        if x['flag']:
            out.append(x['net50'])
            continue
        s = L.SERIES[x['pair']]
        ts, px = s['t'], s['price']
        i = x['i']
        j = H.fill_index(s, i)
        k = next((kk for kk in range(j + 1, len(ts)) if ts[kk] - ts[i] >= 120_000), None)
        e = H.fill_index(s, k) if k is not None else None
        if e is None:
            out.append(x['net50'])
            continue
        pe = px[j] if abs(px[j] / px[i] - 1) < JUMP else max(px[i], px[j])
        pxx = px[e] if abs(px[e] / px[k] - 1) < JUMP else min(px[k], px[e])
        g_old = px[e] / px[j]
        g_new = pxx / pe
        out.append(100 * ((1 + x['net50'] / 100) * g_new / g_old - 1))
    return out


for name, pred in (('RND', L.rnd(0.25, 'hfA')), ('QUIET', L.PREDICATES['QUIET']), ('DIP15', L.PREDICATES['NEAR15LOW'])):
    for lab, a, b in (('train', t0, cut), ('holdout', cut, t1)):
        base, _ = L.run_book(rows, uni, pred, t_from=a, t_to=b, **SHARED)
        guard, _ = L.run_book(rows, uni2, pred, t_from=a, t_to=b, **SHARED)
        v = value(guard)
        hrs = (b - a) / L.HOUR
        print('%-5s %-7s base n %4d tph %.1f net50 %+.3f min %+.1f max %+.1f | guard(a) n %4d tph %.1f net50 %+.3f min %+.1f max %+.1f | (a)+(b) net50 %+.3f min %+.1f max %+.1f' % (
            name, lab, len(base), len(base) / hrs, L.mean(x['net50'] for x in base), min(x['net50'] for x in base),
            max(x['net50'] for x in base), len(guard), len(guard) / hrs, L.mean(x['net50'] for x in guard),
            min(x['net50'] for x in guard), max(x['net50'] for x in guard), L.mean(v), min(v), max(v)))
