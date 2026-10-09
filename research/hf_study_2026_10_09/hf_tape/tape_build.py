"""hf_tape: read-only compact cache of the 2026-10-09 tape snapshot (confirmed SOL-quoted swaps) + coverage stats.
PAPER research only. Writes tape_hf.pkl in this folder."""
import json, os, pickle, sqlite3, sys, time
from collections import Counter, defaultdict

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
DEEP = os.path.normpath(os.path.join(HERE, '..', 'research', 'edge_study_2026_10_08'))
TAPE_DB = os.path.join(DEEP, 'tape_snapshot.sqlite3').replace('\\', '/')
OUT = os.path.join(HERE, 'tape_hf.pkl')
WSOL = 'So11111111111111111111111111111111111111112'


def build():
    con = sqlite3.connect('file:%s?mode=ro' % TAPE_DB, uri=True)
    wid = {}
    per = defaultdict(list)
    st = Counter()
    flags = Counter()
    for pair, et, av, payload in con.execute('SELECT pair, event_time, available, payload FROM events'):
        st['rows'] += 1
        try:
            d = json.loads(payload)
        except Exception:
            st['bad_json'] += 1
            continue
        if not d.get('confirmed_swap'):
            st['unconfirmed'] += 1
            continue
        dr = (d.get('direction') or '').upper()
        if dr not in ('BUY', 'SELL'):
            st['nodir'] += 1
            continue
        if d.get('quote_asset') != WSOL:
            st['non_wsol'] += 1
            continue
        try:
            q = float(d.get('quote_amount'))
            tok = float(d.get('token_amount'))
        except (TypeError, ValueError):
            st['bad_amt'] += 1
            continue
        if not (q > 0 and tok > 0):
            st['zero_amt'] += 1
            continue
        fl = d.get('quality_flags') or []
        for f in fl:
            flags[f] += 1
        actor = 1 if 'SWAP_ACTOR_NOT_TRANSACTION_SIGNER' in fl else 0
        w = d.get('wallet') or ''
        if w not in wid:
            wid[w] = len(wid)
        per[pair].append((int(et), int(av), 1 if dr == 'BUY' else -1, q, tok, wid[w],
                          int(d.get('slot') or 0), int(d.get('event_index') or 0), actor))
        st['kept'] += 1
    meta = {}
    for pair, mint, cs, lp, reason, md in con.execute(
            'SELECT pair, mint, complete_since, last_poll, reason, metadata FROM pairs'):
        try:
            m = json.loads(md)
        except Exception:
            m = {}
        meta[pair] = {'mint': mint, 'complete_since': cs, 'last_poll': lp, 'reason': reason,
                      'sym': m.get('symbol'), 'dex': m.get('dexId')}
    con.close()
    tape = {}
    for pair, rows in per.items():
        rows.sort(key=lambda r: (r[0], r[6], r[7]))
        tape[pair] = {k: [r[n] for r in rows] for n, k in enumerate(
            ('et', 'av', 'sgn', 'q', 'tok', 'w', 'slot', 'ix', 'actor'))}
    out = {'tape': tape, 'nwallets': len(wid), 'stats': dict(st), 'flags': dict(flags), 'meta': meta}
    with open(OUT, 'wb') as fh:
        pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return out


def q(xs, ps=(0.1, 0.5, 0.9, 0.99)):
    xs = sorted(xs)
    return [round(xs[min(len(xs) - 1, int(p * len(xs)))], 2) for p in ps] if xs else []


if __name__ == '__main__':
    t0 = time.time()
    d = build()
    T = d['tape']
    print('built pairs', len(T), 'events', sum(len(v['et']) for v in T.values()), 'wallets', d['nwallets'],
          round(time.time() - t0, 1), 's')
    print('stats', d['stats'])
    print('flags', d['flags'])
    allet = [e for v in T.values() for e in v['et']]
    allav = [a for v in T.values() for a in v['av']]
    print('et range', min(allet), max(allet), 'av range', min(allav), max(allav))
    # per-hour events and pools by AVAILABLE time; lag by hour
    by_h = defaultdict(lambda: [0, set(), []])
    for pair, v in T.items():
        for et, av in zip(v['et'], v['av']):
            h = int(av // 3_600_000)
            by_h[h][0] += 1
            by_h[h][1].add(pair)
            by_h[h][2].append((av - et) / 1000)
    for h in sorted(by_h):
        n, ps, lags = by_h[h]
        print('hour %s events %6d pools %3d lag_s p10/50/90/99 %s' % (
            time.strftime('%m-%d %H', time.gmtime(h * 3600)), n, len(ps), q(lags)))
