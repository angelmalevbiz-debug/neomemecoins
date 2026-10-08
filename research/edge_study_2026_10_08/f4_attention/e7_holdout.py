"""ONE holdout pass for the 3 pre-selected CONFIGS + their random baselines, plus holdout confirmation of the
descriptive event study / attention-veto hypothesis fixed on TRAIN. Writes results.json."""
import sys, os, json, pickle
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import strategies as S

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
res = {}
for c in S.CONFIGS + S.BASELINES:
    tr = H.simulate(c['signal'], **c['kwargs'])
    ev = H.evaluate(tr)
    ho = [x for x in tr if x['entry_t'] >= cut]
    ev['portfolio_holdout'] = H.portfolio(ho, 3)
    ev['portfolio_all'] = H.portfolio(tr, 3)
    ev['holdout_top_pairs'] = sorted(((sum(1 for x in ho if x['pair'] == p), (series[p]['sym'] or '')[:10], p[:8])
                                      for p in {x['pair'] for x in ho}), reverse=True)[:5]
    res[c['name']] = ev
    print('==', c['name'], '|', c['description'])
    for part in ('train', 'holdout', 'train_model', 'holdout_model'):
        print('  ', part, json.dumps(ev[part]))
    print('   portfolio holdout', ev['portfolio_holdout'], 'all', ev['portfolio_all'])
    print('   holdout top pairs', ev['holdout_top_pairs'])

# ---- holdout confirmation of the TRAIN event study (descriptive)
d = pickle.load(open(os.path.join(HERE, 'events.pkl'), 'rb'))
HZ = (60, 180, 300, 600, 900, 1800, 3600)


def line(rows, key='f50'):
    out = []
    for h in HZ:
        xs = sorted(r[key][h][0] for r in rows if r[key][h] is not None)
        if xs:
            out.append('%ds:n%d m%+.2f med%+.2f w%d%%' % (h, len(xs), sum(xs) / len(xs), xs[len(xs) // 2], 100 * sum(1 for x in xs if x > 0) / len(xs)))
    return ' '.join(out)


def gline(rows):
    out = []
    for h in HZ:
        xs = sorted(r['g'][h] for r in rows if r['g'][h] is not None)
        if xs:
            out.append('%ds:m%+.1f med%+.1f' % (h, sum(xs) / len(xs), xs[len(xs) // 2]))
    return ' '.join(out)


print('\n######## event study NET50, rug-screened, PumpSwap: TRAIN vs HOLDOUT')
evs = [e for e in d['events'] if e['dex'] == 'pumpswap' and not e['rug']]
for ty in ('FIRST_latest', 'ONSET1_latest', 'BOOST_ON', 'ONSET1_boosted-latest', 'FIRST_pumpswap-address-catalog'):
    for part, rows in (('train', [e for e in evs if ty in e['types'] and e['t'] < cut]),
                       ('holdout', [e for e in evs if ty in e['types'] and e['t'] >= cut])):
        if rows:
            print('  %-30s %-7s n=%3d pairs=%3d | %s' % (ty, part, len(rows), len({r['pair'] for r in rows}), line(rows)))
            print('  %-30s %-7s gross | %s' % ('', '', gline(rows)))
# all-'latest' events pooled incl. graduations flagged only by the ticker-reuse rule (same mint)
for part, rows in (('train', [e for e in d['events'] if e['dex'] == 'pumpswap' and any(t.endswith('_latest') for t in e['types']) and e['t'] < cut]),
                   ('holdout', [e for e in d['events'] if e['dex'] == 'pumpswap' and any(t.endswith('_latest') for t in e['types']) and e['t'] >= cut])):
    print('  %-30s %-7s n=%3d | %s' % ('ANY latest event, no screen', part, len(rows), line(rows)))
for part, rows in (('train', [e for e in d['events'] if e['dex'] == 'pumpfun' and 'FIRST_latest' in e['types'] and e['t'] < cut]),
                   ('holdout', [e for e in d['events'] if e['dex'] == 'pumpfun' and 'FIRST_latest' in e['types'] and e['t'] >= cut])):
    print('  %-30s %-7s n=%3d gross | %s' % ('pumpfun FIRST_latest', part, len(rows), gline(rows)))


def fee_b(f):
    return 'fee<=50' if f <= 50 else ('fee55-95' if f <= 95 else 'fee100-125')


def liq_b(l):
    return 'liq>=250k' if l >= 250_000 else ('liq50-250k' if l >= 50_000 else 'liq<50k')


def state(b):
    s = b['src']
    if s & 4:
        return 'on_latest_list'
    if s & 3 or b['boost'] > 0:
        return 'boosted'
    return 'no_flag'


print('\n######## attention-veto hypothesis (fixed on TRAIN): 1/min samples, rug-screened, NET50 at 15 and 60 min')
for part in ('train', 'holdout'):
    bs = [b for b in d['base'] if (b['t'] < cut) == (part == 'train') and not b['rug']]
    for fb, lb in (('fee100-125', 'liq<50k'), ('fee100-125', 'liq50-250k'), ('fee55-95', 'liq50-250k')):
        for st in ('on_latest_list', 'boosted', 'no_flag'):
            rows = [b for b in bs if state(b) == st and fee_b(b['fee']) == fb and liq_b(b['liq']) == lb]
            out = []
            for h in (900, 3600):
                xs = sorted(r['f50'][h][0] for r in rows if r['f50'][h] is not None)
                if len(xs) >= 20:
                    out.append('%ds n%d m%+.2f med%+.2f w%d%%' % (h, len(xs), sum(xs) / len(xs), xs[len(xs) // 2], 100 * sum(1 for x in xs if x > 0) / len(xs)))
            if out:
                print('  %-7s %-10s %-10s %-15s pairs %3d | %s' % (part, fb, lb, st, len({r['pair'] for r in rows}), ' '.join(out)))

with open(os.path.join(HERE, 'results.json'), 'w', encoding='utf-8') as fh:
    json.dump(res, fh, indent=1, default=str)
