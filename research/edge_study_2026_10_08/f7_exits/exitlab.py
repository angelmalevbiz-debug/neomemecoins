"""F7 exit-policy lab over cached event paths (built by build_paths.py).

A policy's trigger index is the minimum of the first-passage indexes of its components (stop, fixed TP,
time stop, net trailing stop, %-of-peak trailing stop, breakeven-after-arm, stale-time exit). That is
exactly H.simulate's trigger (all conditions are OR-ed at every point; priority only changes the label).
The fill happens at the NEXT series point (H.simulate semantics, incl. VANISHED -10% / END handling).
Partial take-profit = 50/50 average of two policies' outcomes at full notional (conservative on impact).
"""
import math, pickle, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

EVENTS = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits/events.pkl'
INF = 10 ** 9

STOPS = (-3, -5, -8, -12, -20, -35)          # plus None
TPS = (3, 5, 10, 20, 40, 80)                 # plus None
HOLDS = (5, 15, 30, 60, 120, 240)
TRAILS = tuple((a, w) for a in (3, 5, 10, 20, 40) for w in (2, 4, 7, 12, 20))
PTRAILS = tuple((a, g) for a in (5, 10, 20) for g in (0.25, 0.5))
BES = tuple((a, b) for a in (3, 6, 10, 20) for b in (0.5, 2.0))
STALES = tuple((m, x) for m in (5, 10, 20, 40) for x in (-3.0, 0.0, 2.0))


def load_events():
    with open(EVENTS, 'rb') as fh:
        return pickle.load(fh)


def first_passages(ev):
    """Per-event first-passage path positions for every component level."""
    nets, dts = ev['nets'], ev['dts']
    n = len(nets)
    fp = {}
    # stops (descending levels)
    lv = sorted(STOPS, reverse=True)
    out = [INF] * len(lv)
    q = 0
    for p in range(n):
        x = nets[p]
        while q < len(lv) and x <= lv[q]:
            out[q] = p
            q += 1
        if q == len(lv):
            break
    for l, v in zip(lv, out):
        fp[('S', l)] = v
    # TP / arm levels (ascending); include arm levels used by trails / BE
    lv = sorted(set(TPS) | {a for a, _ in TRAILS} | {a for a, _ in PTRAILS} | {a for a, _ in BES})
    out = [INF] * len(lv)
    q = 0
    for p in range(n):
        x = nets[p]
        while q < len(lv) and x >= lv[q]:
            out[q] = p
            q += 1
        if q == len(lv):
            break
    for l, v in zip(lv, out):
        fp[('T', l)] = v
    # holds
    lv = sorted(set(HOLDS) | {m for m, _ in STALES})
    out = [INF] * len(lv)
    q = 0
    for p in range(n):
        x = dts[p]
        while q < len(lv) and x >= lv[q] * 60:
            out[q] = p
            q += 1
        if q == len(lv):
            break
    for l, v in zip(lv, out):
        fp[('H', l)] = v
    # running peak (only needed after arming)
    peak = [0.0] * n
    pk = -1e18
    for p in range(n):
        x = nets[p]
        if x > pk:
            pk = x
        peak[p] = pk
    for a, w in TRAILS:
        r = INF
        for p in range(fp[('T', a)], n) if fp[('T', a)] < INF else ():
            if nets[p] <= peak[p] - w:
                r = p
                break
        fp[('TR', a, w)] = r
    for a, g in PTRAILS:
        r = INF
        for p in range(fp[('T', a)], n) if fp[('T', a)] < INF else ():
            if nets[p] <= peak[p] * (1 - g):
                r = p
                break
        fp[('PT', a, g)] = r
    for a, b in BES:
        r = INF
        for p in range(fp[('T', a)], n) if fp[('T', a)] < INF else ():
            if nets[p] <= b:
                r = p
                break
        fp[('BE', a, b)] = r
    for m, x0 in STALES:
        r = INF
        for p in range(fp[('H', m)], n) if fp[('H', m)] < INF else ():
            if nets[p] < x0:
                r = p
                break
        fp[('ST', m, x0)] = r
    return fp


# ---- feature-based exits (past-only values at the evaluated point k)
PC5S = tuple((x, m) for x in (-5.0, -2.0, 0.0) for m in (2, 10))
SDOMS = tuple((r, m) for r in (1.0, 1.5) for m in (2, 10))
APC5S = tuple((a, x) for a in (5, 10, 20) for x in (-3.0, 0.0))
KDROPS = (0.05, 0.15)
LDROPS = (0.2, 0.4)


def pool_invariant(s, k):
    """~sqrt(x*y) of a constant-product pool in USD-ish units: liq * sqrt(pnative) / price."""
    p, pn, l = s['price'][k], s['pnative'][k], s['liq'][k]
    return l * math.sqrt(pn) / p if (p > 0 and pn > 0 and l > 0) else float('nan')


def feature_passages(ev, s, fp):
    nets, dts, ks = ev['nets'], ev['dts'], ev['ks']
    n = len(nets)
    pc5, b5, s5, liq = s['pc5'], s['b5'], s['s5'], s['liq']
    j = ev['j']
    for x, m in PC5S:
        r = INF
        for p in range(n):
            if dts[p] >= m * 60 and pc5[ks[p]] <= x:
                r = p
                break
        fp[('PC5', x, m)] = r
    for rr, m in SDOMS:
        r = INF
        for p in range(n):
            k = ks[p]
            if dts[p] >= m * 60 and b5[k] + s5[k] >= 10 and s5[k] >= rr * b5[k]:
                r = p
                break
        fp[('SDOM', rr, m)] = r
    for a, x in APC5S:
        r = INF
        st = fp[('T', a)]
        if st < INF:
            for p in range(st, n):
                if pc5[ks[p]] <= x:
                    r = p
                    break
        fp[('APC5', a, x)] = r
    i0 = pool_invariant(s, j)
    for d in KDROPS:
        r = INF
        if i0 == i0:
            for p in range(n):
                v = pool_invariant(s, ks[p])
                if v == v and v < (1 - d) * i0:
                    r = p
                    break
        fp[('KDROP', d)] = r
    l0 = liq[j]
    for d in LDROPS:
        r = INF
        for p in range(n):
            if liq[ks[p]] < (1 - d) * l0:
                r = p
                break
        fp[('LDROP', d)] = r
    return fp


def feature_grid():
    g = {}
    feats = ([('PC5', x, m) for x, m in PC5S] + [('SDOM', r, m) for r, m in SDOMS] +
             [('APC5', a, x) for a, x in APC5S] + [('KDROP', d) for d in KDROPS] + [('LDROP', d) for d in LDROPS])
    for st in (None, -12, -20, -35):
        for tp in (20, 40, None):
            for h in (120, 240):
                for f in feats:
                    c = []
                    if st is not None:
                        c.append(('S', st))
                    if tp is not None:
                        c.append(('T', tp))
                    c.append(('H', h))
                    c.append(f)
                    g['FEAT-%s s%s t%s h%s %s' % (f[0], st, tp, h, '/'.join(str(v) for v in f[1:]))] = tuple(c)
    return g


class Lab:
    def __init__(self, events):
        self.series, self.meta = H.load()
        self.t_end = self.meta['t1']
        self.cut = H.split_t(self.meta)
        self.events = events
        self.fps = [feature_passages(e, self.series[e['pair']], first_passages(e)) for e in events]
        self.fill_cache = [dict() for _ in events]

    def fill(self, idx, p):
        """(net0, net50, reason_flag) when exiting event idx with trigger at path position p (INF = none)."""
        c = self.fill_cache[idx]
        r = c.get(p)
        if r is not None:
            return r
        ev = self.events[idx]
        s = self.series[ev['pair']]
        n = len(s['t'])
        if p < INF:
            k = ev['ks'][p]
            trig = True
        else:
            k = n - 1
            trig = False
        if trig and k + 1 < n:
            e, flag, px = k + 1, '', s['price'][k + 1]
        else:
            e = k if trig else n - 1
            if self.t_end - s['t'][e] > 600_000:
                flag, px = 'VANISHED', s['price'][e] * (1 - H.VANISH_HAIRCUT_PCT / 100)
            else:
                flag, px = 'END', s['price'][e]
        size, netfee = ev['size'], ev['netfee']
        v0 = H.exit_value(s, e, ev['qty'], 0.0, px)
        v50 = H.exit_value(s, e, ev['qty50'], H.STRESS_BPS, px)
        r = (100 * ((v0 or 0.0) - size - netfee) / size, 100 * ((v50 or 0.0) - size - netfee) / size, flag,
             (s['t'][e] - ev['t']) / 1000)
        c[p] = r
        return r

    def trigger(self, idx, pol):
        fp = self.fps[idx]
        m = INF
        for comp in pol:
            v = fp[comp]
            if v < m:
                m = v
        return m

    def run_nonoverlap(self, pol, idxs, cooldown_s=300):
        """H.simulate-like position semantics: per pair, an event is taken only when its decision time is at
        least cooldown_s after the previous taken trade's exit fill. idxs must be in time order.
        Returns list of (idx, net0, net50)."""
        busy = {}
        out = []
        partial = bool(pol) and pol[0] == 'PARTIAL'
        for i in idxs:
            ev = self.events[i]
            s = self.series[ev['pair']]
            if s['t'][ev['i']] < busy.get(ev['pair'], -1e18):
                continue
            if partial:
                ra = self.fill(i, self.trigger(i, pol[1]))
                rb = self.fill(i, self.trigger(i, pol[2]))
                r0, r50, hold = 0.5 * (ra[0] + rb[0]), 0.5 * (ra[1] + rb[1]), max(ra[3], rb[3])
            else:
                r = self.fill(i, self.trigger(i, pol))
                r0, r50, hold = r[0], r[1], r[3]
            busy[ev['pair']] = ev['t'] + hold * 1000 + cooldown_s * 1000
            out.append((i, r0, r50))
        return out

    def run(self, pol, idxs):
        """pol = tuple of component keys, or ('PARTIAL', polA, polB). Returns list of (net0, net50)."""
        res = []
        if pol and pol[0] == 'PARTIAL':
            _, a, b = pol
            for i in idxs:
                ra = self.fill(i, self.trigger(i, a))
                rb = self.fill(i, self.trigger(i, b))
                res.append((0.5 * (ra[0] + rb[0]), 0.5 * (ra[1] + rb[1])))
            return res
        for i in idxs:
            r = self.fill(i, self.trigger(i, pol))
            res.append((r[0], r[1]))
        return res


def policy_grid():
    """Named exit-policy grid. Each value: tuple of component keys or ('PARTIAL', a, b)."""
    S = [None] + list(STOPS)
    T = [None] + list(TPS)
    g = {}

    def comps(stop, tp, hold, *extra):
        c = []
        if stop is not None:
            c.append(('S', stop))
        if tp is not None:
            c.append(('T', tp))
        c.append(('H', hold))
        c.extend(extra)
        return tuple(c)

    for st in S:
        for tp in T:
            for h in HOLDS:
                g['FIX s%s t%s h%s' % (st, tp, h)] = comps(st, tp, h)
        for h in (30, 60, 120, 240):
            for a, w in TRAILS:
                g['TRAIL s%s a%s w%s h%s' % (st, a, w, h)] = comps(st, None, h, ('TR', a, w))
            for a, gg in PTRAILS:
                g['PTRAIL s%s a%s g%s h%s' % (st, a, gg, h)] = comps(st, None, h, ('PT', a, gg))
            for tp in (10, 20, 40, None):
                for a, b in BES:
                    if tp is not None and a >= tp:
                        continue
                    g['BE s%s t%s a%s b%s h%s' % (st, tp, a, b, h)] = comps(st, tp, h, ('BE', a, b))
    for st in (-5, -8, -12, -20, None):
        for tp in (10, 20, 40, None):
            for h in (60, 120, 240):
                for m, x0 in STALES:
                    g['STALE s%s t%s m%s x%s h%s' % (st, tp, m, x0, h)] = comps(st, tp, h, ('ST', m, x0))
            pass
        for t1 in (5, 10, 20):
            for h in (60, 120, 240):
                for a, w in ((5, 4), (10, 7), (20, 12), (40, 20)):
                    g['PARTIAL s%s t1_%s a%s w%s h%s' % (st, t1, a, w, h)] = (
                        'PARTIAL', comps(st, t1, h), comps(st, None, h, ('TR', a, w)))
                g['PARTIAL s%s t1_%s hold h%s' % (st, t1, h)] = ('PARTIAL', comps(st, t1, h), comps(st, None, h))
    return g


def family(name):
    return name.split(' ')[0]


def stats(res):
    if not res:
        return {'n': 0}
    x50 = [r[1] for r in res]
    x0 = [r[0] for r in res]
    n = len(res)
    g = sum(v for v in x50 if v > 0)
    l = -sum(v for v in x50 if v < 0)
    sx = sorted(x50)
    return {'n': n, 'mean50': sum(x50) / n, 'mean0': sum(x0) / n, 'med50': sx[n // 2],
            'win': 100 * sum(1 for v in x50 if v > 0) / n, 'pf': (g / l) if l > 0 else float('inf')}
