"""Read-only loader for the engine's on-chain swap tape (tape_snapshot.sqlite3).

Builds a compact cache (tape.pkl in this folder) of decoded swap events:
    pair -> dict of parallel lists sorted by event_time:
        et (event_time ms, block time), av (available ms = when the engine had it),
        side (+1 BUY / -1 SELL), wallet (int id), sol (quote SOL), tok (token amount)
plus wallet id table and per-pair signature coverage stats.
"""
import json, os, pickle, sqlite3, sys, time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DEEP = os.path.dirname(HERE)
TAPE_DB = os.path.join(DEEP, 'tape_snapshot.sqlite3')
CACHE = os.path.join(HERE, 'tape.pkl')
WSOL = 'So11111111111111111111111111111111111111112'


def build():
    con = sqlite3.connect('file:%s?mode=ro' % TAPE_DB.replace('\\', '/'), uri=True)
    wid = {}
    per = defaultdict(list)
    dirs, quotes, flags, progs = Counter(), Counter(), Counter(), Counter()
    bad = 0
    for eid, sig, pair, et, av, payload in con.execute(
            'SELECT event_id, signature, pair, event_time, available, payload FROM events'):
        try:
            d = json.loads(payload)
        except Exception:
            bad += 1
            continue
        dr = (d.get('direction') or '').upper()
        dirs[dr] += 1
        quotes[d.get('quote_asset') == WSOL] += 1
        progs[d.get('program_id')] += 1
        for f in d.get('quality_flags') or []:
            flags[f] += 1
        if dr not in ('BUY', 'SELL'):
            continue
        w = d.get('wallet') or ''
        if w not in wid:
            wid[w] = len(wid)
        try:
            sol = float(d.get('quote_amount') or 0.0)
            tok = float(d.get('token_amount') or 0.0)
        except (TypeError, ValueError):
            bad += 1
            continue
        if d.get('quote_asset') != WSOL:
            sol = float('nan')
        per[pair].append((int(et), int(av), 1 if dr == 'BUY' else -1, wid[w], sol, tok,
                          int(d.get('slot') or 0), int(d.get('event_index') or 0)))
    sigcov = {}
    for pair, state, n, lo, hi in con.execute(
            'SELECT pair, state, COUNT(*), MIN(event_time), MAX(event_time) FROM signatures GROUP BY pair, state'):
        sigcov.setdefault(pair, {})[state] = (n, lo, hi)
    pairs_meta = {}
    for pair, mint, cs, lp, reason, meta in con.execute(
            'SELECT pair, mint, complete_since, last_poll, reason, metadata FROM pairs'):
        try:
            m = json.loads(meta)
        except Exception:
            m = {}
        pairs_meta[pair] = {'mint': mint, 'complete_since': cs, 'last_poll': lp, 'reason': reason,
                            'symbol': m.get('symbol'), 'dex': m.get('dexId')}
    con.close()
    tape = {}
    for pair, rows in per.items():
        rows.sort(key=lambda r: (r[0], r[6], r[7]))
        tape[pair] = {'et': [r[0] for r in rows], 'av': [r[1] for r in rows], 'side': [r[2] for r in rows],
                      'w': [r[3] for r in rows], 'sol': [r[4] for r in rows], 'tok': [r[5] for r in rows]}
    out = {'tape': tape, 'wallets': list(wid.keys()), 'sigcov': sigcov, 'pairs_meta': pairs_meta,
           'stats': {'dirs': dict(dirs), 'quote_is_wsol': dict(quotes), 'flags': dict(flags),
                     'progs': dict(progs), 'bad': bad}}
    with open(CACHE, 'wb') as fh:
        pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return out


_T = None


def load():
    global _T
    if _T is None:
        if not os.path.exists(CACHE):
            build()
        with open(CACHE, 'rb') as fh:
            _T = pickle.load(fh)
    return _T


if __name__ == '__main__':
    t0 = time.time()
    d = build()
    print('built', len(d['tape']), 'pairs', sum(len(v['et']) for v in d['tape'].values()), 'events',
          len(d['wallets']), 'wallets', round(time.time() - t0, 1), 's')
    print(d['stats'])
