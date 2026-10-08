"""Post-freeze diagnostics (no tuning): drains missed by a guard and negatives it flags, per part."""
import collections, os, pickle, sys
from rugcommon import _series, HERE, T1, H
import rug_guard as G

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
flag_name = sys.argv[1]
part = sys.argv[2]
minliq = float(sys.argv[3]) if len(sys.argv) > 3 else 20_000
lab = {r['pair']: r for r in pickle.load(open(os.path.join(HERE, 'labels.pkl'), 'rb'))['rows']}
fl = pickle.load(open(os.path.join(HERE, 'flags.pkl'), 'rb'))
ev = collections.defaultdict(list)
neg = collections.defaultdict(list)
for x in fl:
    if x['part'] != part or x['liq'] < minliq:
        continue
    if x['y2h']:
        ev[x['pair']].append(x)
    elif x['clean_neg']:
        neg[x['pair']].append(x)
print('== drain events (2h window, liq >= %g) and guard status' % minliq)
for p, v in sorted(ev.items(), key=lambda kv: kv[1][0]['kind']):
    s = _series[p]
    v.sort(key=lambda z: z['t'])
    last = v[-1]
    P = H.Past(s, last['i'])
    share = sum(1 for z in v if z[flag_name]) / len(v)
    liq, mc, age = P('liq'), P('mcap'), P('age')
    print('  %-10s %s %-34s flagged %.2f | liq %9.0f mcap %12.0f lmc %.4f age %7.0f fee %3d reasons %s' % (
        (s['sym'] or '')[:10], p[:8], last['kind'], share, liq, mc, liq / mc if mc > 0 else float('nan'), age, last['fee'],
        G.rug_reasons(P)))
print('== clean negatives flagged (pairs), with fate')
for p, v in sorted(neg.items(), key=lambda kv: -sum(1 for z in kv[1] if z[flag_name])):
    k = sum(1 for z in v if z[flag_name])
    if not k:
        continue
    s = _series[p]
    r = lab[p]
    x = v[-1]
    P = H.Past(s, x['i'])
    fate = ('DRAINED %s +%.1fh after last neg point' % ('+'.join(r['kinds']), (r['drain_t'] - x['t']) / 3.6e6)) if r['drain_t'] else (
        'vanished %.1fh before end' % ((T1 - r['t_last']) / 3.6e6) if r['vanished'] else 'alive at end')
    liq, mc, age = P('liq'), P('mcap'), P('age')
    print('  %-10s %s flagged %4d/%4d liq %9.0f mcap %12.0f lmc %.4f age %7.0f fee %3d %s | %s' % (
        (s['sym'] or '')[:10], p[:8], k, len(v), liq, mc, liq / mc if mc > 0 else float('nan'), age, x['fee'], G.rug_reasons(P), fate))
