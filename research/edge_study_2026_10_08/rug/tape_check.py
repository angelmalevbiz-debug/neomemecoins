"""What does the on-chain swap tape show around drain events? (read-only)"""
import collections, json, os, pickle, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEEP = os.path.dirname(HERE)
sys.path.insert(0, DEEP)
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
lab = pickle.load(open(os.path.join(HERE, 'labels.pkl'), 'rb'))
con = sqlite3.connect('file:%s?mode=ro' % os.path.join(DEEP, 'tape_snapshot.sqlite3'), uri=True)
tape_pairs = {r[0]: r[1] for r in con.execute('select pair, reason from pairs')}
print('tape pairs', len(tape_pairs))

drained = [r for r in lab['rows'] if r['dex'] == 'pumpswap' and r['drain_t'] is not None]
covered = [r for r in drained if r['pair'] in tape_pairs]
print('drained pumpswap pairs', len(drained), 'in tape', len(covered))
cnt_any = 0
for r in sorted(covered, key=lambda r: r['drain_t']):
    dt = r['drain_t']
    evs = []
    for (et, payload) in con.execute('select event_time, payload from events where pair=? and event_time between ? and ? order by event_time',
                                     (r['pair'], dt - 7_200_000, dt + 1_800_000)):
        d = json.loads(payload)
        evs.append((et, d.get('direction'), float(d.get('quote_amount') or 0), (d.get('wallet') or '')[:8], float(d.get('token_amount') or 0)))
    if not evs:
        print(r['sym'], r['pair'][:8], 'kinds', r['kinds'], 'no tape events in [-2h,+30m]', 'tape reason', tape_pairs[r['pair']])
        continue
    cnt_any += 1
    pre = [e for e in evs if e[0] < dt - 600_000]
    near = [e for e in evs if dt - 600_000 <= e[0] <= dt + 60_000]
    post = [e for e in evs if e[0] > dt + 60_000]

    def agg(es):
        b = sum(e[2] for e in es if e[1] == 'BUY'); s = sum(e[2] for e in es if e[1] == 'SELL')
        nb = sum(1 for e in es if e[1] == 'BUY'); ns = sum(1 for e in es if e[1] == 'SELL')
        big = max((e for e in es if e[1] == 'SELL'), key=lambda e: e[2], default=None)
        w = len({e[3] for e in es})
        return 'n=%d buys=%d (%.1f SOL) sells=%d (%.1f SOL) wallets=%d biggest_sell=%s' % (
            len(es), nb, b, ns, s, w, ('%.1f SOL by %s at %+.1f min' % (big[2], big[3], (big[0] - dt) / 60000)) if big else '-')
    print('==', r['sym'], r['pair'][:8], 'kinds', r['kinds'], 'liq_peak %.0f' % r['liq_peak'])
    print('   pre(-2h..-10m):', agg(pre))
    print('   near(-10m..+1m):', agg(near))
    print('   post(+1m..+30m):', agg(post))
    # top sellers near drain: share of sell SOL by wallet
    sw = collections.Counter()
    for e in near:
        if e[1] == 'SELL':
            sw[e[3]] += e[2]
    if sw:
        tot = sum(sw.values())
        print('   near sellers top3:', [(w, round(v, 1), round(100 * v / tot)) for w, v in sw.most_common(3)])
print('drained pairs with tape events around the drain', cnt_any)
