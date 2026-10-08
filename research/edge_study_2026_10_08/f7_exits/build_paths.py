"""F7: build entry events + their modeled-net paths (harness semantics) for exit-policy research.

Event = point i where the pair is in an F7 universe and the signal fires, at least SPACING_S after the
previous event of the same (pair, signal). Entry fills at i+1 (skipped if lag > 60 s), exactly like
H.simulate. The path holds, for every later point k with a valid exit model (up to the first point at
or past HORIZON_MIN), the modeled net % used by H.simulate for exit triggers. Fills are computed later
at k+1 with H.exit_value (net0 and net50), so exits keep the harness latency/gap semantics.
"""
import array, pickle, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
from common import utag, size_rule, SIGNALS

SPACING_S = 600
HORIZON_MIN = 240
OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits/events.pkl'
KEEP = ('cf', 'lowfee', 'mid', 'hi', 'hi20')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    series, meta = H.load()
    t0 = time.time()
    events = {name: [] for name in SIGNALS}
    npts = 0
    for pair, s in series.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        ts, n = s['t'], len(s['t'])
        last = {name: -1e18 for name in SIGNALS}
        for i in range(n - 1):
            ti = ts[i]
            if all(ti - last[nm] < SPACING_S * 1000 for nm in SIGNALS):
                continue
            P = H.Past(s, i)
            tag = utag(P)
            if tag not in KEEP:
                continue
            j = i + 1
            if ts[j] - ti > H.MAX_ENTRY_LAG_MS:
                continue
            size = size_rule(P)
            if not size > 0:
                continue
            fired = [nm for nm, fn in SIGNALS.items() if ti - last[nm] >= SPACING_S * 1000 and fn(P)]
            if not fired:
                continue
            fill = H.entry_fill(s, j, size)
            fill50 = H.entry_fill(s, j, size, H.STRESS_BPS)
            if fill is None or fill50 is None:
                continue
            qty, netfee = fill
            ks, dts, nets = array.array('l'), array.array('d'), array.array('d')
            tj = ts[j]
            for k in range(j + 1, n):
                v = H.exit_value(s, k, qty)
                if v is None:
                    continue
                dt = (ts[k] - tj) / 1000
                ks.append(k)
                dts.append(dt)
                nets.append(100 * (v - size - netfee) / size)
                if dt >= HORIZON_MIN * 60:
                    break
            npts += len(ks)
            ev = {'pair': pair, 'sym': s['sym'], 'i': i, 'j': j, 't': tj, 'tag': tag, 'fee': H.fee_bps(s, j),
                  'liq': s['liq'][j], 'size': size, 'qty': qty, 'qty50': fill50[0], 'netfee': netfee,
                  'ks': ks, 'dts': dts, 'nets': nets}
            for nm in fired:
                last[nm] = ti
                events[nm].append(ev)
    print('build secs', round(time.time() - t0, 1), 'path points', npts)
    for nm, evs in events.items():
        tags = {}
        for e in evs:
            tags[e['tag']] = tags.get(e['tag'], 0) + 1
        print(nm, len(evs), tags)
    with open(OUT, 'wb') as fh:
        pickle.dump({'spacing_s': SPACING_S, 'horizon_min': HORIZON_MIN, 'events': events}, fh,
                    protocol=pickle.HIGHEST_PROTOCOL)


if __name__ == '__main__':
    main()
