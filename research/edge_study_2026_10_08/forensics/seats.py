"""Tape-seat occupancy over time: how many of the NEO_TAPE_MAX_PAIRS=4 seats were exit pins?

Pins (tape_pool_scheduler._held_coins): every PumpSwap pool held by the main account or by any
Lab book (never capped); since V4 (deployed 2026-10-08 10:12 local) also pools held by personal
engines. entry_capacity = max(0, 4 - pins). Sources (read-only): Lab ledger COPY in
release-backup-20261008-101156 (complete Lab history), the saved /state snapshots for main and
the personal engines. Also cross-checks with obs.sqlite3 which pools actually had verified flow.
"""
import sys, os, json, sqlite3, collections, datetime
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
DEEP = os.path.dirname(HERE)
SCR = os.path.dirname(DEEP)
LAB = 'C:/Users/Chavd/neomemecoins/.runtime/release-backup-20261008-101156/.runtime/accounts/strategy_lab.json'
V4_DEPLOY_MS = None  # set below from the 18802 restart event at 10:12


def ms_local(s):
    return int(datetime.datetime.strptime(s, '%Y-%m-%d %H:%M:%S').timestamp() * 1000)


V4_DEPLOY_MS = ms_local('2026-10-08 10:12:00')
con = sqlite3.connect('file:%s?mode=ro' % os.path.join(DEEP, 'obs.sqlite3'), uri=True)
dex_of = dict(con.execute('SELECT pair, max(dex) FROM o GROUP BY pair').fetchall())
t0, t1 = con.execute('SELECT min(t), max(t) FROM o').fetchone()

intervals = []  # (open_ms, close_ms, pair, owner)
lab = json.load(open(LAB, encoding='utf-8'))
snap_end = None
for name, b in lab['books'].items():
    for h in b.get('history') or []:
        intervals.append((h['opened_at'], h['closed_at'], h['pairAddress'], 'LAB:' + name, h.get('symbol')))
    p = b.get('position')
    if isinstance(p, dict) and p.get('opened_at'):
        intervals.append((p['opened_at'], None, p['pairAddress'], 'LAB:' + name, p.get('symbol')))
for f, owner, personal in (('state_8878.json', 'MAIN', False), ('state_18801.json', 'OFA_18801', True), ('state_18802.json', 'CF_18802', True)):
    d = json.load(open(os.path.join(SCR, f), encoding='utf-8'))
    for h in d['history']:
        intervals.append((h['opened_at'], h['closed_at'], h['pairAddress'], owner + ('*' if personal else ''), h.get('symbol')))
    for p in d['positions']:
        intervals.append((p['opened_at'], None, p['pairAddress'], owner + ('*' if personal else ''), p.get('symbol')))

END = t1
minutes = range(int(t0 // 60000), int(END // 60000) + 1)
pins_by_min = {}
owners_by_min = {}
for m in minutes:
    ms = m * 60000 + 30000
    held = {}
    for o, c, pair, owner, sym in intervals:
        if o <= ms and (c is None or c > ms):
            if dex_of.get(pair, 'pumpswap') != 'pumpswap':
                continue
            if owner.endswith('*') and ms < V4_DEPLOY_MS:
                continue  # personal-engine pins exist only since V4
            held.setdefault(pair, set()).add(owner)
    pins_by_min[m] = held
cap = 4
hist = collections.Counter()
by_hour = collections.defaultdict(lambda: [0, 0, 0])
for m, held in pins_by_min.items():
    n = len(held)
    hist[min(n, cap)] += 1
    h = datetime.datetime.fromtimestamp(m * 60).strftime('%m-%d %H')
    by_hour[h][0] += 1
    by_hour[h][1] += n
    by_hour[h][2] += max(0, cap - n)
tot = sum(hist.values())
print('minutes', tot)
print('pinned pools per minute (capped at 4):', {k: '%d (%.1f%%)' % (v, 100 * v / tot) for k, v in sorted(hist.items())})
mean_entry = sum(max(0, cap - len(h)) for h in pins_by_min.values()) / tot
print('mean entry seats available', round(mean_entry, 2), 'of', cap)
print('hourly: minutes, mean pins, mean entry seats')
for h, (n, p, e) in sorted(by_hour.items()):
    print('  ', h, n, round(p / n, 2), round(e / n, 2))
# which owners pinned most seat-minutes
own = collections.Counter()
pool_min = collections.Counter()
symbol = {}
for o, c, pair, owner, sym in intervals:
    symbol[pair] = sym
for m, held in pins_by_min.items():
    for pair, owners in held.items():
        pool_min[pair] += 1
        for ow in owners:
            own[ow.split(':')[0]] += 1
print('pin seat-minutes by owner class', own.most_common())
print('pinned pools by minutes', [(symbol.get(p), p[:8], v) for p, v in pool_min.most_common(15)])
# cross-check with obs: share of verified-flow minutes on pinned pools vs entry pools
vf_units = collections.Counter()
for t, pair in con.execute('SELECT t, pair FROM o WHERE vf_trades IS NOT NULL'):
    vf_units[(int(t // 60000), pair)] += 1
pinned_vf = sum(1 for (m, p) in vf_units if p in pins_by_min.get(m, {}))
print('verified-flow (minute,pool) units', len(vf_units), 'on pinned pools', pinned_vf, round(100 * pinned_vf / max(1, len(vf_units)), 1), '%')
json.dump({'pins_hist': hist, 'mean_entry_seats': mean_entry, 'by_hour': by_hour, 'owners': own,
           'vf_units': len(vf_units), 'vf_on_pinned': pinned_vf}, open(os.path.join(HERE, 'seats.json'), 'w'), indent=1, default=list)
