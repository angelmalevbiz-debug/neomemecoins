"""Kaplan-Meier survival of early-life PumpSwap pools (full span, descriptive).
Event 'death' = price < 50% of the reference price OR liquidity < 20% of the reference liquidity.
Censoring = the pool left the scanner feed (no further points) or the dataset ended.
Also: cumulative incidence of reaching 2x the reference price before death (fat tail), and the
fraction of pools that are already invisible (censored) at each horizon - the data-coverage problem."""
import sys, bisect
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
T1 = meta['t1']
HZ = (5, 15, 30, 60, 120, 240)


def track(s, i0):
    """Return (time_to_event_min, kind) with kind in {'death', 'double', 'censor'}; first event wins.
    Also time to death ignoring doubles (for KM of death)."""
    ts, p, l = s['t'], s['price'], s['liq']
    p0, l0 = p[i0], l[i0]
    t_double = None
    for k in range(i0 + 1, len(ts)):
        dt = (ts[k] - ts[i0]) / 60000
        if (p[k] < 0.5 * p0) or (l[k] < 0.2 * l0):
            return dt, 'death', t_double
        if t_double is None and p[k] >= 2 * p0:
            t_double = dt
    return (ts[-1] - ts[i0]) / 60000, 'censor', t_double


def km(events):
    """events: list of (t, died bool). Returns survival at each horizon and number at risk."""
    ev = sorted(events)
    n = len(ev)
    S, at_risk, out = 1.0, n, {}
    idx = 0
    for h in HZ:
        while idx < n and ev[idx][0] <= h:
            t, d = ev[idx]
            if d:
                S *= (1 - 1 / at_risk) if at_risk > 0 else 1
            at_risk -= 1
            idx += 1
        out[h] = (round(100 * S, 1), at_risk)
    return out


def report(label, refs):
    evs, dbl = [], []
    for s, i0 in refs:
        t, kind, td = track(s, i0)
        evs.append((t, kind == 'death'))
        dbl.append(td)
    n = len(evs)
    if n == 0:
        print(label, 'n=0')
        return
    k = km(evs)
    print('%-58s n=%4d  KM survival%% (at-risk): %s' % (label, n, '  '.join('%dm:%s(%d)' % (h, k[h][0], k[h][1]) for h in HZ)))
    print('%-58s        observed death by: %s | reached 2x (observed) by: %s | still visible: %s' % (
        '', '  '.join('%dm:%.0f%%' % (h, 100 * sum(1 for t, d in evs if d and t <= h) / n) for h in HZ),
        '  '.join('%dm:%.0f%%' % (h, 100 * sum(1 for td in dbl if td is not None and td <= h) / n) for h in HZ),
        '  '.join('%dm:%.0f%%' % (h, 100 * sum(1 for s, i0 in refs if s['t'][-1] - s['t'][i0] >= h * 60000) / n) for h in HZ)))


pump = [(p, s) for p, s in series.items() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1]
# (a) graduates at first observation
grads = [(s, 0) for p, s in pump if s['age'][0] < 5]
report('(a) first sight, age<5 min', grads)
for lo, hi in ((0, 15e3), (15e3, 25e3), (25e3, 60e3), (60e3, 250e3), (250e3, 1e12)):
    report('    liq0 in [%dk,%dk)' % (lo / 1e3, hi / 1e3), [(s, 0) for s, i in grads if lo <= s['liq'][0] < hi])
for flag, nm in ((True, 'pump-suffix mint'), (False, 'non-pump mint')):
    report('    ' + nm, [(s, 0) for s, i in grads if (s['mint'] or '').endswith('pump') == flag])


# (b) persistent young: first point where the pool has been in the feed >= 10 min, age < 240
def first_persistent(s, secs=600, max_age=240):
    ts = s['t']
    for i in range(len(ts)):
        if ts[i] - ts[0] >= secs * 1000:
            return i if s['age'][i] < max_age else None
    return None


pers = []
for p, s in pump:
    if not (s['age'][0] < 240):
        continue
    i = first_persistent(s)
    if i is not None:
        pers.append((s, i))
report('(b) in feed >=10 min, age<240', pers)
for lo, hi in ((0, 15e3), (15e3, 25e3), (25e3, 60e3), (60e3, 250e3), (250e3, 1e12)):
    report('    liq in [%dk,%dk)' % (lo / 1e3, hi / 1e3), [(s, i) for s, i in pers if lo <= s['liq'][i] < hi])
pers_rug = [(s, i) for s, i in pers if H.interim_rug_risk(H.Past(s, i))]
report('    interim_rug_risk at reference', pers_rug)
report('    passes interim screen', [(s, i) for s, i in pers if not H.interim_rug_risk(H.Past(s, i))])
# (c) persistent >= 30 min
pers30 = []
for p, s in pump:
    if not (s['age'][0] < 240):
        continue
    i = first_persistent(s, 1800)
    if i is not None:
        pers30.append((s, i))
report('(c) in feed >=30 min, age<240', pers30)
# (d) high-liquidity fresh launches (non-graduate shape): liq >= 100k at age < 30
hl = [(s, 0) for p, s in pump if s['age'][0] < 30 and s['liq'][0] >= 100e3]
report('(d) first sight age<30 with liq>=100k', hl)
for s, i in hl:
    ts = s['t']
    lmax = max(s['liq'])
    kmax = max(range(len(ts)), key=lambda k: s['liq'][k])
    drained = s['liq'][-1] < 0.2 * lmax
    print('      %s %-10s mint_pump=%s liq0=%.0fk lm0=%.2f mcap0=%.0fk dur=%.0fm price_max/0=%.2f price_last/0=%.2f drained=%s reuse=%d' % (
        s['pair'][:8], (s['sym'] or '')[:10], (s['mint'] or '').endswith('pump'), s['liq'][0] / 1e3,
        s['liq'][0] / s['mcap'][0] if s['mcap'][0] > 0 else -1, s['mcap'][0] / 1e3, (ts[-1] - ts[0]) / 60000,
        max(s['price']) / s['price'][0], s['price'][-1] / s['price'][0], drained, H.other_pairs_same_ticker_before(H.Past(s, 0))))
