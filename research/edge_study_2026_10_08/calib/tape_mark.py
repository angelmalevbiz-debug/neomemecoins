"""DexScreener mark (what the harness fills at) vs the executable pool mid reconstructed from the on-chain tape.

For each harness point of a tape-covered PumpSwap pool: take the last BUY and last SELL swap within the
previous W seconds, remove constant-product impact (2*q/L), mid = sqrt(buy_px * sell_px) (fees cancel),
half_spread = sqrt(buy_px / sell_px) - 1 (= pool fee if the decoder reports fee-inclusive amounts).
offset = mark priceNative / mid - 1  (negative => the mark sits BELOW the executable mid => harness buys too cheap).
"""
import bisect, collections, json, math, os, sqlite3, sys
HERE = os.path.dirname(os.path.abspath(__file__))
DEEP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, DEEP)
import harness as H
from stats import desc, fmt, ok

W_S = 10


def load_tape():
    con = sqlite3.connect('file:%s?mode=ro' % os.path.join(DEEP, 'tape_snapshot.sqlite3').replace('\\', '/'), uri=True)
    by = collections.defaultdict(list)
    for (p,) in con.execute('SELECT payload FROM events'):
        d = json.loads(p)
        if d.get('pool_orientation') not in (None, 'TRACKED_BASE'):
            continue
        if not str(d.get('quote_asset') or '').startswith('So1111'):
            continue
        try:
            ta, qa = float(d['token_amount']), float(d['quote_amount'])
        except (TypeError, ValueError, KeyError):
            continue
        if not (ta > 0 and qa > 0):
            continue
        side = 1 if d.get('direction') == 'BUY' else (-1 if d.get('direction') == 'SELL' else 0)
        if not side:
            continue
        by[d['pairAddress']].append((int(d['event_time']), side, qa / ta, qa, int(d.get('slot') or 0)))
    con.close()
    for v in by.values():
        v.sort()
    return by


def main():
    tape = load_tape()
    series, meta = H.load()
    out = []
    for pair, ev in tape.items():
        s = series.get(pair)
        if s is None or s['dex'] != 'pumpswap' or s['quote_sol'] != 1 or len(ev) < 20:
            continue
        et = [e[0] for e in ev]
        buys = [e for e in ev if e[1] > 0]
        sells = [e for e in ev if e[1] < 0]
        bt = [e[0] for e in buys]
        st = [e[0] for e in sells]
        ts = s['t']
        lo = bisect.bisect_left(ts, et[0])
        hi = bisect.bisect_right(ts, et[-1])
        for i in range(lo, hi):
            t = ts[i]
            pn, liq, price = s['pnative'][i], s['liq'][i], s['price'][i]
            if not (pn > 0 and liq > 0 and price > 0):
                continue
            su = price / pn
            kb = bisect.bisect_right(bt, t) - 1
            ks = bisect.bisect_right(st, t) - 1
            if kb < 0 or ks < 0:
                continue
            b, sl = buys[kb], sells[ks]
            if t - b[0] > W_S * 1000 or t - sl[0] > W_S * 1000:
                continue
            ib = 2 * b[3] * su / liq
            is_ = 2 * sl[3] * su / liq
            if ib > 0.02 or is_ > 0.02:
                continue
            bp = b[2] / (1 + ib)
            sp = sl[2] / (1 - is_)
            mid = math.sqrt(bp * sp)
            P = H.Past(s, i)
            pn60 = P.ago('pnative', 60)
            pn30 = P.ago('pnative', 30)
            out.append({'pair8': pair[:8], 'opened_at': t, 'fee': H.fee_bps(s, i), 'liq': liq,
                        'half_spread': 100 * (math.sqrt(bp / sp) - 1), 'offset': 100 * (pn / mid - 1),
                        'mv60': 100 * (pn / pn60 - 1) if pn60 > 0 else float('nan'),
                        'mv30': 100 * (pn / pn30 - 1) if pn30 > 0 else float('nan'),
                        'swap_age_s': (t - min(b[0], sl[0])) / 1000, 'same_slot': b[4] == sl[4]})
    print('points', len(out), 'pairs', len(set(r['pair8'] for r in out)))
    K = ('n', 'clusters', 'mean', 'median', 'p10', 'p90', 'ci95')
    fb = lambda f: 'fee<=50' if f <= 50 else ('fee55-95' if f <= 95 else 'fee100-125')
    print('\n== half spread from tape (fee-inclusive decoder => equals pool fee) minus fee tier, %')
    for name in ('fee<=50', 'fee55-95', 'fee100-125'):
        rs = [r for r in out if fb(r['fee']) == name]
        print('  %-11s half_spread %s' % (name, fmt(desc(rs, lambda r: r['half_spread']), ('n', 'clusters', 'mean', 'median'))))
        print('  %-11s minus fee   %s' % ('', fmt(desc(rs, lambda r: r['half_spread'] - r['fee'] / 100), K)))
    print('  by exact tier:')
    for f in sorted(set(r['fee'] for r in out)):
        rs = [r for r in out if r['fee'] == f]
        if len(rs) >= 200:
            print('    fee %5.1f %s' % (f, fmt(desc(rs, lambda r: r['half_spread']), ('n', 'clusters', 'median', 'p10', 'p90'))))
    print('\n== mark offset vs tape mid (negative = mark below executable mid), %')
    print('  ALL', fmt(desc(out, lambda r: r['offset']), K))
    for name in ('fee<=50', 'fee55-95', 'fee100-125'):
        rs = [r for r in out if fb(r['fee']) == name]
        print('  %-11s %s' % (name, fmt(desc(rs, lambda r: r['offset']), K)))
    print('  by prior 60 s mark move:')
    bins = [(-1e9, -3), (-3, -1), (-1, -0.3), (-0.3, 0.3), (0.3, 1), (1, 3), (3, 1e9)]
    for a, b in bins:
        rs = [r for r in out if ok(r['mv60']) and a <= r['mv60'] < b]
        print('    mv60 [%5g,%5g) %s' % (a, b, fmt(desc(rs, lambda r: r['offset']), K)))
    print('  by prior 30 s mark move:')
    for a, b in bins:
        rs = [r for r in out if ok(r['mv30']) and a <= r['mv30'] < b]
        print('    mv30 [%5g,%5g) %s' % (a, b, fmt(desc(rs, lambda r: r['offset']), K)))
    json.dump({'n': len(out)}, open(os.path.join(HERE, 'tape_mark_meta.json'), 'w'))
    # compact rows for later calibration fitting
    with open(os.path.join(HERE, 'tape_mark_rows.json'), 'w', encoding='utf-8') as fh:
        json.dump([{k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()} for r in out], fh)


if __name__ == '__main__':
    main()
