"""(1) Independent re-implementation of the row vs the leaderboard's recorded trades.
(2) Re-run of the family's own BASELINES[0] through harness_final (as run_all does) to confirm the recorded trades.
(3) Leakage probes on the harness path: a LookAhead-trap Past view, and an i-truncated series (future deleted)."""
import sys
sys.dont_write_bytecode = True
import importlib.util, pickle, time, json, array
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
OUT = DEEP + '/verify_f2_meanrev_leakage_RANDOM_ctx1h_for_F2_OVERSOLD1H'
sys.path.insert(0, OUT)
import indep as I

t0 = time.time()
ser, meta = I.load()
print('loaded', len(ser), 'pairs', 't0', meta['t0'], 't1', meta['t1'], 'meta_pkl', meta['meta_pkl'], round(time.time() - t0, 1), 's')
CUT = I.cut_time()
sh_tr, sh_ho = (CUT - meta['t0']) / 3.6e6, (meta['t1'] - CUT) / 3.6e6

# ---------------- (1) independent
elig = I.eligible_index(screen='interim')
print('eligible pairs', len(elig), 'eligible points', sum(len(v) for v in elig.values()), round(time.time() - t0, 1), 's')
skips = []
mine = I.run(elig, 'f2r1', 0.005, count_skips=skips)
tr, ho = I.split(mine)
print('\nINDEPENDENT  n', len(mine), 'train', len(tr), 'holdout', len(ho), 'no-fill skips', len(skips))
print(' train  ', I.summ(tr, span_h=sh_tr))
print(' holdout', I.summ(ho, span_h=sh_ho))
print(' holdout model', I.summ(ho, 'usd0', span_h=sh_ho))
print(' portfolio holdout', I.portfolio(ho))

with open(OUT + '/lb_trades.pkl', 'rb') as fh:
    lb = pickle.load(fh)['orig']


def cmp(a_list, b_list, la, lb_):
    ka = {(x['pair'], x['entry_t']): x for x in a_list}
    kb = {(x['pair'], x['entry_t']): x for x in b_list}
    both = set(ka) & set(kb)
    print('compare %s vs %s: %d vs %d trades, %d matched by (pair, entry_t); only-%s %d, only-%s %d' % (
        la, lb_, len(ka), len(kb), len(both), la, len(set(ka) - both), lb_, len(set(kb) - both)))
    mx = {'net0': 0.0, 'net50': 0.0}
    diff_reason = 0
    for k in both:
        a, b = ka[k], kb[k]
        for f in mx:
            mx[f] = max(mx[f], abs(a[f] - b[f]))
        if a['reason'] != b['reason'] or a['exit_t'] != b['exit_t'] or a['flag'] != b['flag']:
            diff_reason += 1
            print('   exit differs', k[0][:8], a['reason'], a['flag'], a['exit_t'], '|', b['reason'], b['flag'], b['exit_t'])
    print('   max |net0| diff %.6f  max |net50| diff %.6f  exit mismatches %d' % (mx['net0'], mx['net50'], diff_reason))
    for k in sorted(set(ka) - both):
        print('   only', la, k[0][:8], ka[k]['sym'], k[1], ka[k]['reason'], round(ka[k]['net50'], 2))
    for k in sorted(set(kb) - both):
        print('   only', lb_, k[0][:8], kb[k]['sym'], k[1], kb[k]['reason'], round(kb[k]['net50'], 2))


cmp(mine, lb, 'indep', 'leaderboard')

# ---------------- (2) harness_final rerun of the family module
spec = importlib.util.spec_from_file_location('harness_final', DEEP + '/leaderboard/harness_final.py')
H = importlib.util.module_from_spec(spec)
sys.modules['harness_final'] = H
spec.loader.exec_module(H)
H._LOADED = (ser, meta['meta_pkl'])          # same data object, no second 300 MB load
sys.modules['harness'] = H
spec2 = importlib.util.spec_from_file_location('f2_strat_verify', DEEP + '/f2_meanrev/strategies.py')
F2 = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(F2)
b = [x for x in F2.BASELINES if x['name'] == 'RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB'][0]
print('\nbaseline kwargs', b['kwargs'], 'desc', b['description'])
hf = H.simulate(b['signal'], **b['kwargs'])
ev = H.evaluate(hf)
print('HARNESS_FINAL rerun: train', {k: ev['train'][k] for k in ('n', 'pairs', 'mean_usd', 'mean_pct')},
      'holdout', {k: ev['holdout'][k] for k in ('n', 'pairs', 'mean_usd', 'mean_pct', 'median_pct', 'win_rate', 'pf',
                                                 'ci95_mean_usd', 'ci95_mean_usd_pair', 'top_pair_share', 'trades_per_hour')})
cmp(hf, lb, 'harness_final_rerun', 'leaderboard')

# ---------------- (3a) LookAhead trap: wrap Past so any negative back / static() of a series raises; also record max index read
reads = {'max_ahead': 0, 'calls': 0, 'static_fields': set(), 'fields': set()}


class TrapPast(H.Past):
    __slots__ = ()

    def __call__(self, f, back=0):
        reads['calls'] += 1
        reads['fields'].add(f)
        if back < 0:
            raise H.LookAhead(f)
        return orig_past.__call__(self, f, back)

    def static(self, f):
        reads['static_fields'].add(f)
        return orig_past.static(self, f)


orig_past = H.Past
H.Past = TrapPast
try:
    hf_trap = H.simulate(b['signal'], **b['kwargs'])
finally:
    H.Past = orig_past
print('\ntrap run: trades', len(hf_trap), 'identical', [(x['pair'], x['entry_t'], x['net50']) for x in hf_trap] ==
      [(x['pair'], x['entry_t'], x['net50']) for x in hf], 'fields read', sorted(reads['fields']),
      'static fields', sorted(reads['static_fields']))

# ---------------- (3b) truncation test: for every decision of the recorded run, evaluate the signal on a copy of the
# pair's series truncated at the decision point (all future points deleted).  A past-only signal must still fire.
# Also re-evaluate at 200 random non-firing points per pair-sample to check it stays silent.
def truncated(s, i):
    c = {k: (array.array('d', v[:i + 1]) if isinstance(v, array.array) else v) for k, v in s.items()}
    return c


fires_ok = 0
for x in hf:
    s = ser[x['pair']]
    i = list(s['t']).index(x['decision_t'])
    st = truncated(s, i)
    P = H.Past(st, i)
    fires_ok += bool(b['signal'](P))
print('truncation test: %d / %d recorded decisions still fire with the future deleted' % (fires_ok, len(hf)))
# silent-point check
import random as R
rr = R.Random(3)
mism = chk = 0
pairs = list(elig.keys())
for _ in range(3000):
    p = rr.choice(pairs)
    s = ser[p]
    i = rr.randrange(len(s['t']) - 1)
    full = bool(b['signal'](H.Past(s, i)))
    tru = bool(b['signal'](H.Past(truncated(s, i), i)))
    chk += 1
    mism += full != tru
print('truncation test on %d random points of eligible pairs: %d mismatches full-vs-truncated' % (chk, mism))

with open(OUT + '/v02_trades.pkl', 'wb') as fh:
    pickle.dump({'indep': mine, 'harness_final': hf, 'skips': skips}, fh)
print('secs', round(time.time() - t0, 1))
