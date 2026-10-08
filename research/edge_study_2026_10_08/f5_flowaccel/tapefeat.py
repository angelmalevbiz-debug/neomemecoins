"""Past-only on-chain tape flow features (shared by the sample builder and strategies.py).

An event is usable at decision time t only when its ingestion time `available` <= t AND its block time <= t.
Read-only over tape_snapshot.sqlite3; caches a compact copy in this folder (tape_events.pkl).
"""
import bisect, json, os, pickle, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
TAPE_DB = os.path.join(os.path.dirname(HERE), 'tape_snapshot.sqlite3')
CACHE = os.path.join(HERE, 'tape_cache.pkl')
_T = None


def _build():
    con = sqlite3.connect('file:%s?mode=ro' % TAPE_DB.replace('\\', '/'), uri=True)
    wid, per = {}, {}
    for pair, et, av, payload in con.execute('SELECT pair, event_time, available, payload FROM events'):
        p = json.loads(payload)
        if not p.get('confirmed_swap'):
            continue
        d = p.get('direction')
        sgn = 1 if d == 'BUY' else (-1 if d == 'SELL' else 0)
        if not sgn:
            continue
        try:
            q = float(p.get('quote_amount'))
        except (TypeError, ValueError):
            continue
        if not (q >= 0):
            continue
        w = p.get('wallet') or ''
        if w not in wid:
            wid[w] = len(wid)
        per.setdefault(pair, []).append((et, av, sgn, q, wid[w]))
    con.close()
    out = {}
    for pair, v in per.items():
        v.sort()
        # strictly past-only "new wallet" flag: event e counts as a first appearance of its wallet on this
        # pair when no event of that wallet with an EARLIER block time had been ingested by e's own
        # ingestion time (a later backfill cannot change the flag, so no future information leaks in).
        order = sorted(range(len(v)), key=lambda k: (v[k][1], v[k][0]))
        seen_min = {}
        newflag = [0] * len(v)
        for k in order:
            et, av, sgn, q, w = v[k]
            m = seen_min.get(w)
            newflag[k] = 1 if (m is None or m >= et) else 0
            if m is None or et < m:
                seen_min[w] = et
        out[pair] = {'et': [x[0] for x in v], 'av': [x[1] for x in v], 'sgn': [x[2] for x in v],
                     'q': [x[3] for x in v], 'w': [x[4] for x in v], 'new': newflag}
    with open(CACHE, 'wb') as fh:
        pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return out


def tape():
    global _T
    if _T is None:
        if os.path.exists(CACHE):
            with open(CACHE, 'rb') as fh:
                _T = pickle.load(fh)
        else:
            _T = _build()
    return _T


def flow(pair, t, window_s):
    """Flow over block times (t - window, t], counting only events already ingested at t. None if the pair has no tape."""
    d = tape().get(pair)
    if d is None:
        return None
    et, av = d['et'], d['av']
    hi = bisect.bisect_right(et, t)
    lo = bisect.bisect_right(et, t - window_s * 1000, 0, hi)
    nb = ns = 0
    bsol = ssol = 0.0
    buyers, sellers, newb = {}, set(), 0
    for k in range(lo, hi):
        if av[k] > t:
            continue
        q = d['q'][k]
        if d['sgn'][k] > 0:
            nb += 1
            bsol += q
            w = d['w'][k]
            buyers[w] = buyers.get(w, 0.0) + q
            newb += d['new'][k]
        else:
            ns += 1
            ssol += q
            sellers.add(d['w'][k])
    top = max(buyers.values()) if buyers else 0.0
    return {'nb': nb, 'ns': ns, 'bsol': bsol, 'ssol': ssol, 'ub': len(buyers), 'us': len(sellers),
            'newb': newb, 'top_share': (top / bsol) if bsol > 0 else float('nan'),
            'net_sol': bsol - ssol}


def last_available_age_s(pair, t):
    """Seconds since the newest ingested event (block time) at t, or None when the pair has no tape."""
    d = tape().get(pair)
    if d is None:
        return None
    et, av = d['et'], d['av']
    hi = bisect.bisect_right(et, t)
    k = hi - 1
    # walk back a little to find an event already ingested
    steps = 0
    while k >= 0 and av[k] > t and steps < 200:
        k -= 1
        steps += 1
    if k < 0 or av[k] > t:
        return None
    return (t - et[k]) / 1000.0
