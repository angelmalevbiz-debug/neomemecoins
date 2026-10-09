"""Build tests/fixtures/lab_hf_obs_20261008.json: real scan-log observations of a few E95 pools in a short holdout
window and the decisions syn_lib (hf_synthesis) makes on exactly those observations. PAPER research only, read-only
on the research data; writes only the output path given on the command line.

Decisions: syn_lib.run_book with the pre-registered shared rules (3 slots, 120 s time exit, 120 s pool cooldown,
<= 50 orders per trailing 3600 s, 1 order per 2-s refresh bin, heat enforced, $25) on the event rows of the chosen
pools only, plus the HF_PRINT_GUARD_V1 toggle guard (syn_toggle definition, no chosen pool toggles in the window).
"""
import hashlib, json, math, os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SYN = os.path.normpath(os.path.join(HERE, '..', 'hf_synthesis'))
sys.path.insert(0, SYN)
import syn_lib as L  # noqa: E402

OUT = sys.argv[1]
W0 = int(sys.argv[2]) if len(sys.argv) > 2 else 1_791_486_000_000
MINUTES = int(sys.argv[3]) if len(sys.argv) > 3 else 25
NPOOLS = int(sys.argv[4]) if len(sys.argv) > 4 else 4
W1 = W0 + MINUTES * 60_000
PRE, POST = 16 * 60_000, 6 * 60_000
H = L.H
d = L.S.events()
rows, cut, meta = d['rows'], d['cut'], d['meta']
uni = L.UNIVERSES['E95']
SHARED = dict(slots=3, hold_s=120, cooldown_s=120, pacing=('roll', 3600, 50), size=25.0, heat_on=True)
JUMP, BACK, WIN, BLOCK = 0.15, 0.02, 600_000, 6 * L.HOUR


def toggles(pair):
    s = L.SERIES[pair]
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
    return out


counts = {}
for r in rows:
    if W0 <= r['t'] < W1 and uni(r) and not r['heat']:
        counts[r['pair']] = counts.get(r['pair'], 0) + 1
chosen = []
for pair, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
    s = L.SERIES[pair]
    ts = s['t']
    lo = next(k for k in range(len(ts)) if ts[k] >= W0 - PRE)
    hi = max(k for k in range(len(ts)) if ts[k] <= W1 + POST)
    gaps = max(ts[k] - ts[k - 1] for k in range(lo + 1, hi + 1))
    tg = [t for t in toggles(pair) if t - BLOCK <= W1 + POST and t >= W0 - PRE - BLOCK]
    if ts[lo] > W0 - PRE + 60_000 or gaps > 120_000 or tg:
        print('skip', pair[:8], 'start', ts[lo] - (W0 - PRE), 'max gap', gaps, 'toggles', len(tg))
        continue
    chosen.append(pair)
    if len(chosen) == NPOOLS:
        break
print('chosen', [p[:8] for p in chosen], [counts[p] for p in chosen])
sub = sorted((r for r in rows if r['pair'] in set(chosen)), key=lambda r: (r['t'], r['pair']))
BOOKS = (('HF_RND_E95', L.rnd(0.25, 'hfA')), ('HF_QUIET_E95', L.PREDICATES['QUIET']),
         ('HF_DIP15_E95', L.PREDICATES['NEAR15LOW']))
expected = {}
counters = {}
for name, pred in BOOKS:
    tr, c = L.run_book(sub, uni, pred, t_from=W0, t_to=W1, **SHARED)
    counters[name] = c
    out = []
    for x in tr:
        s = L.SERIES[x['pair']]
        ts, px = s['t'], s['price']
        i = x['i']
        j = H.fill_index(s, i)
        pe = px[j]
        pxe = pe * (1 + x['gross'] / 100)
        out.append({'pair': x['pair'], 'decision_at': int(x['decision_t']), 'entry_fill_at': int(x['entry_t']),
                    'exit_fill_at': int(x['exit_t']), 'reason': x['reason'], 'flag': x['flag'],
                    'entry_fill_price': pe, 'exit_fill_price': pxe,
                    'booked_pct': round(x['netc'], 6), 'net50_pct': round(x['net50'], 6),
                    'net0_pct': round(x['net0'], 6)})
    expected[name] = out
    print(name, 'trades', len(out), 'counters', c)


def num(v, digits=None):
    if v is None or not (v == v) or v in (float('inf'), float('-inf')):
        return None
    return float('%.*g' % (digits, v)) if digits else v


pools = {}
for pair in chosen:
    s = L.SERIES[pair]
    ts = s['t']
    lo = next(k for k in range(len(ts)) if ts[k] >= W0 - PRE)
    hi = max(k for k in range(len(ts)) if ts[k] <= W1 + POST)
    pts = []
    prev_t, prev_vals = None, None
    for k in range(lo, hi + 1):
        vals = [s['price'][k], s['pnative'][k], s['liq'][k], s['mcap'][k], s['v5'][k]]
        vals = [num(v) for v in vals]
        t = int(ts[k])
        dt = t - (prev_t if prev_t is not None else W0 - PRE)
        pts.append([dt] if vals == prev_vals else [dt] + vals)
        prev_t, prev_vals = t, vals
    pools[pair] = {'mint': s['mint'], 'symbol': s['sym'], 'dex': s['dex'], 'quote_sol': s['quote_sol'],
                   'created': s.get('created'), 'points': pts}
events = {}
for r in sub:
    if W0 - PRE <= r['t'] <= W1 + POST:
        events.setdefault(r['pair'], []).append([int(r['t']), bool(r['eg']), bool(r['heat'])])
syn_sha = hashlib.sha256(open(os.path.join(SYN, 'syn_lib.py'), 'rb').read()).hexdigest()
fixture = {
    'description': ('LAB_HIGH_FREQUENCY_V1 parity fixture: real DexScreener scan-log observations (obs.sqlite3 via '
                    'harness_final series.pkl, one point per engine scan) of %d E95 pools from %d min before to %d min '
                    'after a %d-minute holdout window, the research engine-guard (eg) and heat flags of every refresh '
                    'event row, and the decisions hf_synthesis/syn_lib.run_book makes on exactly these rows '
                    '(shared HF rules, salt hfA). A point is [ms since the previous point (the first: since base_ms), '
                    'priceUsd, priceNative, liquidityUsd, marketCap, volume.m5]; a 1-element point repeats every '
                    'value of the previous point.') % (len(chosen), PRE // 60_000, POST // 60_000, MINUTES),
    'source': {'syn_lib_sha256': syn_sha, 'events': 'hf_signals/events.pkl (eg via add_guard.py)',
               'series': 'research/edge_study_2026_10_08/series.pkl', 'paper_only': True},
    'base_ms': W0 - PRE, 'window': [W0, W1], 'shared_rules': {**SHARED, 'pacing': list(SHARED['pacing'])},
    'pools': pools, 'events': events, 'expected': expected,
    'counters': counters,
}
with open(OUT, 'w', encoding='utf-8') as fh:
    json.dump(fixture, fh, separators=(',', ':'))
print('wrote', OUT, os.path.getsize(OUT), 'bytes')
