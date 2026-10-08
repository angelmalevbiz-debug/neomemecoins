"""Adds past-only tape features to samples.pkl -> samples2.pkl."""
import sys, os, pickle, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tapefeat as TF

t0 = time.time()
d = pickle.load(open(os.path.join(HERE, 'samples.pkl'), 'rb'))
T = TF.tape()
print('tape pairs', len(T), round(time.time() - t0, 1))
NAN = float('nan')
cov = 0
for r in d['rows']:
    p, t = r['pair'], r['t']
    age = TF.last_available_age_s(p, t)
    r['tp_age'] = age if age is not None else NAN
    live = age is not None and age <= 600
    r['tp_live'] = live
    if not live:
        continue
    cov += 1
    f60 = TF.flow(p, t, 60)
    f120 = TF.flow(p, t, 120)
    f300 = TF.flow(p, t, 300)
    f900 = TF.flow(p, t, 900)
    r['tp_nb120'], r['tp_ns120'] = f120['nb'], f120['ns']
    r['tp_ub120'], r['tp_us120'] = f120['ub'], f120['us']
    r['tp_ub300'], r['tp_us300'] = f300['ub'], f300['us']
    r['tp_newb300'] = f300['newb']
    r['tp_bsol300'], r['tp_ssol300'] = f300['bsol'], f300['ssol']
    r['tp_net300'] = f300['net_sol']
    r['tp_net120'] = f120['net_sol']
    r['tp_bshare300'] = f300['bsol'] / (f300['bsol'] + f300['ssol']) if (f300['bsol'] + f300['ssol']) > 0 else NAN
    r['tp_ubshare300'] = f300['ub'] / (f300['ub'] + f300['us']) if (f300['ub'] + f300['us']) > 0 else NAN
    r['tp_top300'] = f300['top_share']
    r['tp_breadth300'] = f300['ub'] / f300['nb'] if f300['nb'] > 0 else NAN
    # acceleration: buyers in last 120 s vs the average 120 s in the preceding 13 min
    prev_ub = f900['ub']
    r['tp_uacc'] = f120['ub'] / max(1.0, (f900['nb'] - f120['nb']) / 6.5) if f900['nb'] > 0 else NAN
    r['tp_bsacc'] = (f60['bsol'] / max(1e-9, (f900['bsol'] - f60['bsol']) / 14)) if f900['bsol'] > 0 else NAN
    r['tp_nbsol900'] = f900['bsol']
    r['tp_netsol900'] = f900['net_sol']
print('rows', len(d['rows']), 'tape-live rows', cov, round(time.time() - t0, 1))
with open(os.path.join(HERE, 'samples2.pkl'), 'wb') as fh:
    pickle.dump(d, fh, protocol=pickle.HIGHEST_PROTOCOL)
print('saved', round(time.time() - t0, 1))
