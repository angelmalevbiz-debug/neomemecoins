"""Independent re-implementation of F5_C1_TAPE_BREADTH_NOCHASE from its plain-language description.

Written without reusing f5_flowaccel/tapefeat.py. Reads tape_snapshot.sqlite3 read-only and builds its own
per-pair structures. Two "first-time buyer" definitions:
  'wallet' (independent reading of the description): distinct buyer wallets in (t-300s, t] that have NO event on this
            pair with block time <= t-300s among events already ingested (available <= t - lag).
  'event'  (same semantics as the original, re-coded): buy events in the window whose wallet had no event with a
            strictly earlier block time ingested at or before the event's own ingestion time.
lag_ms: extra decision latency for tape data (an event is usable only when available + lag_ms <= t).
"""
import bisect, json, sqlite3

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
WSOL = 'So11111111111111111111111111111111111111112'
_T = None
STATS = {}


def load_tape():
    global _T
    if _T is not None:
        return _T
    con = sqlite3.connect('file:%s?mode=ro' % (DEEP + '/tape_snapshot.sqlite3'), uri=True)
    per = {}
    skipped_non_sol = 0
    for pair, et, av, payload in con.execute('SELECT pair, event_time, available, payload FROM events'):
        p = json.loads(payload)
        if not p.get('confirmed_swap'):
            continue
        d = p.get('direction')
        if d not in ('BUY', 'SELL'):
            continue
        if p.get('quote_asset') != WSOL:
            skipped_non_sol += 1
            continue
        try:
            q = float(p.get('quote_amount'))
        except (TypeError, ValueError):
            continue
        if not q >= 0:
            continue
        per.setdefault(pair, []).append((int(et), int(av), 1 if d == 'BUY' else -1, q, p.get('wallet') or ''))
    con.close()
    out = {}
    for pair, v in per.items():
        v.sort()
        et = [x[0] for x in v]
        av = [x[1] for x in v]
        # tape-live: newest block time among events ingested by t  (et <= av always holds in this tape)
        order = sorted(range(len(v)), key=lambda k: av[k])
        avs = [av[k] for k in order]
        mx, avmax = -1, []
        for k in order:
            mx = max(mx, et[k])
            avmax.append(mx)
        # per-wallet et-sorted lists with prefix-min of availability
        wl = {}
        for k, x in enumerate(v):
            wl.setdefault(x[4], []).append((x[0], x[1]))
        wst = {}
        for w, lst in wl.items():
            lst.sort()
            wet = [a for a, _ in lst]
            pm, m = [], None
            for _, b in lst:
                m = b if m is None else min(m, b)
                pm.append(m)
            wst[w] = (wet, pm)
        # 'event' semantics new flag: no event of the wallet with strictly earlier et and av <= own av
        newev = []
        for x in v:
            wet, pm = wst[x[4]]
            j = bisect.bisect_left(wet, x[0])
            newev.append(1 if (j == 0 or pm[j - 1] > x[1]) else 0)
        out[pair] = {'et': et, 'av': av, 'sgn': [x[2] for x in v], 'q': [x[3] for x in v], 'w': [x[4] for x in v],
                     'avs': avs, 'avmax': avmax, 'wst': wst, 'newev': newev}
    STATS['skipped_non_sol'] = skipped_non_sol
    STATS['pairs'] = len(out)
    _T = out
    return out


def tape_age_s(pair, t, lag_ms=0):
    d = load_tape().get(pair)
    if d is None:
        return None
    k = bisect.bisect_right(d['avs'], t - lag_ms)
    if k == 0:
        return None
    return (t - d['avmax'][k - 1]) / 1000.0


def flow(pair, t, window_s=300, lag_ms=0, newdef='wallet'):
    d = load_tape().get(pair)
    if d is None:
        return None
    tk = t - lag_ms
    lo = t - window_s * 1000
    et, av = d['et'], d['av']
    a = bisect.bisect_right(et, lo)
    b = bisect.bisect_right(et, t)
    buyers, sellers = {}, set()
    bsol = ssol = 0.0
    newb_ev = 0
    for k in range(a, b):
        if av[k] > tk:
            continue
        q = d['q'][k]
        w = d['w'][k]
        if d['sgn'][k] > 0:
            bsol += q
            buyers[w] = buyers.get(w, 0.0) + q
            newb_ev += d['newev'][k]
        else:
            ssol += q
            sellers.add(w)
    if newdef == 'event':
        newb = newb_ev
    else:
        newb = 0
        for w in buyers:
            wet, pm = d['wst'][w]
            j = bisect.bisect_right(wet, lo)   # events with et <= lo
            if j == 0 or pm[j - 1] > tk:
                newb += 1
    top = max(buyers.values()) / bsol if (buyers and bsol > 0) else float('nan')
    return {'ub': len(buyers), 'us': len(sellers), 'bsol': bsol, 'ssol': ssol, 'net': bsol - ssol,
            'newb': newb, 'top': top}


def make_signal(H, newdef='wallet', lag_ms=0, rug='interim', ub_min=10, newb_min=5, top_max=0.3,
                nochase=(-2.0, 5.0), live_s=600, extra=None):
    """rug: 'interim' (H.interim_rug_risk, as the original), 'none', 'train_only' (H.rug_risk_train_only)."""
    def sig(P):
        liq = P('liq')
        if not (liq >= 20_000):
            return False
        f = P.fee_bps()
        if not (52.5 <= f <= 125):
            return False
        if rug == 'interim' and H.interim_rug_risk(P):
            return False
        if rug == 'train_only' and H.rug_risk_train_only(P):
            return False
        pair, t = P.static('pair'), P.t
        age = tape_age_s(pair, t, lag_ms)
        if age is None or age > live_s:
            return False
        if nochase is not None:
            p0, p1 = P('price'), P.ago('price', 300)
            if not (p1 == p1 and p1 > 0 and p0 == p0 and p0 > 0):
                return False
            r = 100.0 * (p0 / p1 - 1.0)
            if not (nochase[0] <= r <= nochase[1]):
                return False
        fl = flow(pair, t, 300, lag_ms, newdef)
        if fl is None:
            return False
        if not (fl['ub'] >= ub_min and fl['newb'] >= newb_min and fl['top'] == fl['top'] and fl['top'] < top_max
                and fl['net'] > 0):
            return False
        if extra is not None and not extra(P):
            return False
        return True
    return sig


def universe_signal(H, lag_ms=0, nochase=(-2.0, 5.0)):
    def u(P):
        liq = P('liq')
        if not (liq >= 20_000):
            return False
        if not (52.5 <= P.fee_bps() <= 125):
            return False
        if H.interim_rug_risk(P):
            return False
        age = tape_age_s(P.static('pair'), P.t, lag_ms)
        if age is None or age > 600:
            return False
        if nochase is not None:
            p0, p1 = P('price'), P.ago('price', 300)
            if not (p1 == p1 and p1 > 0 and p0 == p0 and p0 > 0):
                return False
            r = 100.0 * (p0 / p1 - 1.0)
            if not (nochase[0] <= r <= nochase[1]):
                return False
        return True
    return u


def size_fn(P):
    return min(200.0, 0.002 * P('liq'))


EXITS = dict(stop=-10.0, tp=6.0, hold_min=30.0, size_fn=size_fn)
