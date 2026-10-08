"""OFA (18801) specifics: (1) DexScreener updatedAt age at observation (OFA needs <= 6 s at decision
because MAX_SIGNAL_AGE_MS 8000 - ADAPTIVE_QUOTE_LATENCY_MARGIN_MS 2000) for OFA-market-eligible rows;
(2) OFA proxy-pass (minute, pool) units while OFA was live (2026-10-08 01:28 local onwards)."""
import sys, os, sqlite3, collections, datetime
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from funnel import coin_of, COLS, stage_ofa
HERE = os.path.dirname(os.path.abspath(__file__))
con = sqlite3.connect('file:%s?mode=ro' % os.path.join(os.path.dirname(HERE), 'obs.sqlite3'), uri=True)
LIVE = int(datetime.datetime(2026, 10, 8, 1, 28).timestamp() * 1000)
age_ok = collections.Counter()
units = {}
for row in con.execute("SELECT %s FROM o WHERE dex='pumpswap' AND quote_sol=1 AND score >= 85 AND liq >= 30000" % ','.join(COLS)):
    r = dict(zip(COLS, row))
    st = stage_ofa(r, coin_of(r))
    if st >= 2:
        a = r['t'] - (r['upd'] or 0)
        age_ok['<=6s' if 0 <= a <= 6000 else '>6s'] += 1
    if r['t'] >= LIVE:
        k = (int(r['t'] // 60000), r['pair'])
        if st > units.get(k, (0, ''))[0]:
            units[k] = (st, r['sym'])
print('OFA-market-eligible rows by DexScreener updatedAt age at observation:', dict(age_ok),
      round(100 * age_ok['<=6s'] / max(1, sum(age_ok.values())), 1), '% fresh enough')
c = collections.Counter(st for st, _ in units.values())
print('OFA (minute,pool) units since live, by deepest stage (2 market,3 cost,4 vf,5 fresh,6 pressure,7 oct4 flow,8 conviction):', sorted(c.items()))
print('pools reaching stage 8 since live:', collections.Counter(sym for st, sym in units.values() if st >= 8).most_common())
print('pools reaching stage 3 (all non-flow) since live:', collections.Counter(sym for st, sym in units.values() if st >= 3).most_common())
