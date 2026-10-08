"""Step 4: trade-level spot check of the holdout's biggest contributor (LOADED 2XjwJiED, +34.5% net50) from the raw
sources: DexScreener points (series + raw obs rows) vs on-chain prints around the decision, entry fill and exit.
Also re-prices every C1 holdout trade at on-chain prices with a 60 s truth window (wider coverage)."""
import sys
sys.dont_write_bytecode = True
import bisect, importlib.util, json, math, os, pickle, sqlite3, time

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
OUT = DEEP + '/verify_f5_flowaccel_leakage_F5_C1_TAPE_BREADTH_NOCHASE'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


H = _load('harness_final', DEEP + '/leaderboard/harness_final.py')
sys.modules['harness'] = H
sys.path.insert(0, OUT)
import indep as I  # noqa: E402

series, meta = H.load()
CUT = H.split_t(meta)
c1 = pickle.load(open(os.path.join(OUT, 'trades_orig_rerun.pkl'), 'rb'))
x = [t for t in c1 if t['sym'] == 'LOADED'][0]
pair = x['pair']
s = series[pair]
dt, et_, xt = x['decision_t'], x['entry_t'], x['exit_t']
print('trade', {k: (round(v, 4) if isinstance(v, float) else v) for k, v in x.items() if k not in ('pair',)})
print('\nDexScreener series points (rel s to decision, price USD, pnative, liq, b5, s5, pc5):')
i0 = bisect.bisect_left(s['t'], dt - 400_000)
i1 = bisect.bisect_right(s['t'], xt + 60_000)
last = None
for i in range(i0, i1):
    if s['pnative'][i] != last or s['t'][i] in (dt, et_, xt):
        mark = ' <-decision' if s['t'][i] == dt else (' <-entry fill' if s['t'][i] == et_ else (' <-exit fill' if s['t'][i] == xt else ''))
        print('  %+8.1f  %.10f  %.4e  liq=%9.0f b5=%s s5=%s pc5=%s%s' % ((s['t'][i] - dt) / 1000, s['price'][i], s['pnative'][i],
              s['liq'][i], s['b5'][i], s['s5'][i], s['pc5'][i], mark))
        last = s['pnative'][i]
print('\nflow seen by the signal at decision:', I.flow(pair, dt, 300, 0, 'event'))
print('flow with 5 s ingestion lag:', I.flow(pair, dt, 300, 5000, 'event'))
print('tape age at decision s:', I.tape_age_s(pair, dt))
con = sqlite3.connect('file:%s?mode=ro' % (DEEP + '/tape_snapshot.sqlite3'), uri=True)
print('\non-chain prints (rel s to decision by block time; ingest lag s; dir; SOL; px SOL/token; px / DexScreener fill):')
fill = s['pnative'][bisect.bisect_left(s['t'], et_)]
rows = con.execute('SELECT event_time, available, payload FROM events WHERE pair=? AND event_time BETWEEN ? AND ? ORDER BY event_time',
                   (pair, dt - 120_000, xt + 30_000)).fetchall()
for e, a, pl in rows:
    p = json.loads(pl)
    px = float(p['quote_amount']) / float(p['token_amount'])
    print('  %+8.1f ingest+%5.1f %-4s %8.3f SOL  %.4e  %.3f  w=%s' % ((e - dt) / 1000, (a - e) / 1000, p['direction'], float(p['quote_amount']),
          px, px / fill, (p.get('wallet') or '')[:8]))
con.close()
