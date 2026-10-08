"""F4 event extraction: attention / promotion events detected past-only, with forward net returns.

Writes events.pkl (all events with features + forward outcomes) into this folder.
Prints TRAIN-ONLY tables (entries before the 60% split). Holdout is not looked at here.
"""
import sys, os, math, time, bisect, pickle
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
HZ = (60, 180, 300, 600, 900, 1800, 3600)
WARMUP_MS = 300_000   # pairs first seen in the first 5 min of the recording were already listed: not fresh events

t0 = time.time()
series, meta = H.load()
T0, T1 = meta['t0'], meta['t1']
CUT = H.split_t(meta)


def fwd(s, i, sec, extra):
    """Net % for buy at i+1, sell at first point >= t_i+sec. Vanished pair -> last price -10% (harness rule).
    Returns (net, flag) or None when not computable / dataset ended first."""
    ts = s['t']
    n = len(ts)
    if i + 1 >= n or ts[i + 1] - ts[i] > H.MAX_ENTRY_LAG_MS:
        return None
    f = H.entry_fill(s, i + 1, 200.0, extra)
    if f is None:
        return None
    k = bisect.bisect_left(ts, ts[i] + sec * 1000, i + 2)
    if k < n:
        v = H.exit_value(s, k, f[0], extra)
        flag = ''
    else:
        last = n - 1
        if T1 - ts[last] > 600_000 and ts[last] < ts[i] + sec * 1000:
            v = H.exit_value(s, last, f[0], extra, s['price'][last] * 0.9)
            flag = 'V'
        else:
            return None
    if v is None:
        return None
    return 100 * (v - 200.0 - f[1]) / 200.0, flag


def gross(s, i, sec):
    ts = s['t']
    n = len(ts)
    if i + 1 >= n:
        return None
    k = bisect.bisect_left(ts, ts[i] + sec * 1000, i + 2)
    if k >= n:
        return None
    p0, p1 = s['price'][i + 1], s['price'][k]
    return 100 * (p1 / p0 - 1) if p0 > 0 and p1 > 0 else None


def fee_b(f):
    return 'fee<=50' if f <= 50 else ('fee55-95' if f <= 95 else 'fee100-125')


def liq_b(l):
    return 'liq>=250k' if l >= 250_000 else ('liq50-250k' if l >= 50_000 else 'liq<50k')


# mint first-seen index (past-safe: compared with event time)
mint_first = {}
for p, s in series.items():
    m = s['mint']
    if m:
        mint_first.setdefault(m, []).append((s['t'][0], p))
for v in mint_first.values():
    v.sort()


def features(s, i):
    P = H.Past(s, i)
    return {'fee': H.fee_bps(s, i), 'liq': s['liq'][i], 'mcap': s['mcap'][i], 'age': s['age'][i],
            'pc5': s['pc5'][i], 'pc1h': s['pc1h'][i], 'pc24': s['pc24'][i], 'v5': s['v5'][i], 'v1h': s['v1h'][i],
            'b5': s['b5'][i], 's5': s['s5'][i], 'b1h': s['b1h'][i], 's1h': s['s1h'][i],
            'score': s['score'][i], 'risk': s['risk'][i], 'boost': s['boost'][i], 'src': int(s['src'][i]),
            'rug': H.interim_rug_risk(P), 'same_ticker': H.other_pairs_same_ticker_before(P),
            'rt': H.roundtrip_cost_pct(s, i, 200.0), 'hp_uw5': s['hp_uw5'][i], 'vf_trades': s['vf_trades'][i],
            'nhist': i + 1}


events = []
base = []
for pair, s in series.items():
    if s['quote_sol'] != 1:
        continue
    dex = s['dex']
    ts, src, bo = s['t'], s['src'], s['boost']
    n = len(ts)
    mint = s['mint']
    seen_bits = 0
    last_sample = -1e18
    for i in range(n - 1):
        cur = int(src[i])
        types = []
        if i == 0:
            if ts[0] - T0 >= WARMUP_MS:
                lst = mint_first.get(mint, [])
                fresh_mint = not any(t0m < ts[0] and p != pair for t0m, p in lst)
                tag = 'FIRST' if fresh_mint else 'FIRSTPAIR'
                for name, m in H.SRC_BITS.items():
                    if cur & m:
                        types.append('%s_%s' % (tag, name))
                if bo[0] > 0:
                    types.append('%s_boost' % tag)
        else:
            prev = int(src[i - 1])
            for name, m in (('boosted', 1), ('boosted-latest', 2), ('latest', 4)):
                if (cur & m) and not (prev & m):
                    types.append(('ONSET1_' if not (seen_bits & m) else 'ONSETRE_') + name)
            pb = bo[i - 1] if bo[i - 1] > 0 else 0.0
            cb = bo[i] if bo[i] > 0 else 0.0
            if cb > pb:
                types.append('BOOST_UP' if pb > 0 else 'BOOST_ON')
        seen_bits |= cur
        if types:
            row = {'pair': pair, 'i': i, 't': ts[i], 'dex': dex, 'types': types, 'sym': s['sym']}
            row.update(features(s, i))
            row['f0'] = {h: fwd(s, i, h, 0.0) for h in HZ}
            row['f50'] = {h: fwd(s, i, h, 50.0) for h in HZ}
            row['g'] = {h: gross(s, i, h) for h in HZ}
            row['life_min'] = (ts[-1] - ts[i]) / 60000
            events.append(row)
        # 1/min random-time base sample (state info kept so it can be split by attention state)
        if ts[i] - last_sample >= 60_000:
            last_sample = ts[i]
            if dex == 'pumpswap':
                P = H.Past(s, i)
                base.append({'pair': pair, 't': ts[i], 'fee': H.fee_bps(s, i), 'liq': s['liq'][i], 'src': cur,
                             'boost': bo[i], 'rug': H.interim_rug_risk(P),
                             'f50': {h: fwd(s, i, h, 50.0) for h in (300, 900, 3600)},
                             'f0': {h: fwd(s, i, h, 0.0) for h in (300, 900, 3600)}})

with open(os.path.join(HERE, 'events.pkl'), 'wb') as fh:
    pickle.dump({'events': events, 'base': base, 'cut': CUT, 'T0': T0, 'T1': T1}, fh, protocol=pickle.HIGHEST_PROTOCOL)
print('events', len(events), 'base samples', len(base), 'secs', round(time.time() - t0, 1))

# ------------------------------------------------------------------ TRAIN-only tables
def stats(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return n, round(sum(xs) / n, 2), round(xs[n // 2], 2), round(100 * sum(1 for x in xs if x > 0) / n, 0)


def table(rows, label, key='f50'):
    out = []
    for h in HZ:
        vals = [r[key][h][0] for r in rows if r[key][h] is not None]
        vz = sum(1 for r in rows if r[key][h] is not None and r[key][h][1] == 'V')
        st = stats(vals)
        if st:
            out.append('%ds:n%d m%+.2f med%+.2f w%d%% v%d' % (h, st[0], st[1], st[2], st[3], vz))
    print('  ', label, '| pairs', len({r['pair'] for r in rows}), '|', ' '.join(out))


tr = [e for e in events if e['t'] < CUT]
print('\n######## TRAIN events (t < split) - counts by type and dex')
cnt = {}
for e in tr:
    for ty in e['types']:
        cnt[(ty, e['dex'])] = cnt.get((ty, e['dex']), 0) + 1
for k in sorted(cnt, key=lambda k: -cnt[k]):
    print('  ', k, cnt[k])

print('\n######## TRAIN PumpSwap events: forward NET50 % (stress), by type (all buckets), rug-screened')
types = sorted({ty for e in tr if e['dex'] == 'pumpswap' for ty in e['types']})
for ty in types:
    rows = [e for e in tr if e['dex'] == 'pumpswap' and ty in e['types'] and not e['rug']]
    if len(rows) >= 5:
        table(rows, '%-28s n=%d' % (ty, len(rows)))
print('\n  (same, rug-flagged only)')
for ty in types:
    rows = [e for e in tr if e['dex'] == 'pumpswap' and ty in e['types'] and e['rug']]
    if len(rows) >= 5:
        table(rows, '%-28s n=%d' % (ty, len(rows)))

print('\n######## TRAIN PumpSwap events by type x fee x liq (rug-screened), NET50')
for ty in types:
    for fb in ('fee<=50', 'fee55-95', 'fee100-125'):
        for lb in ('liq>=250k', 'liq50-250k', 'liq<50k'):
            rows = [e for e in tr if e['dex'] == 'pumpswap' and ty in e['types'] and not e['rug']
                    and fee_b(e['fee']) == fb and liq_b(e['liq']) == lb]
            if len(rows) >= 5:
                table(rows, '%-26s %-10s %-10s' % (ty, fb, lb))

print('\n######## TRAIN gross price change % (no costs) by type, rug-screened')
for ty in types:
    rows = [e for e in tr if e['dex'] == 'pumpswap' and ty in e['types'] and not e['rug']]
    if len(rows) >= 5:
        out = []
        for h in HZ:
            st = stats([r['g'][h] for r in rows if r['g'][h] is not None])
            if st:
                out.append('%ds:m%+.2f med%+.2f' % (h, st[1], st[2]))
        print('  ', '%-28s' % ty, ' '.join(out))

print('\n######## TRAIN base rate (1/min samples, PumpSwap, rug-screened) NET50, by attention state')
def state(b):
    s = b['src']
    if s & 3:
        return 'on_boost_list'
    if s & 4:
        return 'on_latest_list'
    return 'no_attention_flag'
btr = [b for b in base if b['t'] < CUT and not b['rug']]
for st_name in ('on_boost_list', 'on_latest_list', 'no_attention_flag'):
    for fb in ('fee<=50', 'fee55-95', 'fee100-125'):
        for lb in ('liq>=250k', 'liq50-250k', 'liq<50k'):
            rows = [b for b in btr if state(b) == st_name and fee_b(b['fee']) == fb and liq_b(b['liq']) == lb]
            if len(rows) < 30:
                continue
            out = []
            for h in (300, 900, 3600):
                st = stats([r['f50'][h][0] for r in rows if r['f50'][h] is not None])
                if st:
                    out.append('%ds:n%d m%+.2f med%+.2f w%d%%' % (h, st[0], st[1], st[2], st[3]))
            print('  ', '%-18s %-10s %-10s pairs %d' % (st_name, fb, lb, len({r['pair'] for r in rows})), ' '.join(out))
print('done', round(time.time() - t0, 1))
