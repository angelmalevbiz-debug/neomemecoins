"""Shared loading / splitting / metric helpers for the rug study (stdlib only)."""
import math, os, pickle, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import harness as H

NAN = float('nan')
_series, META = H.load()
CUT = H.split_t(META)
T1 = META['t1']
H2 = 7_200_000


def load_points():
    pts = pickle.load(open(os.path.join(HERE, 'feat.pkl'), 'rb'))
    lab = {r['pair']: r for r in pickle.load(open(os.path.join(HERE, 'labels.pkl'), 'rb'))['rows']}
    for x in pts:
        r = lab[x['pair']]
        x['drain_t'] = None
        if x['y2h']:
            x['drain_t'] = x['t'] + x['mins_to_drain'] * 60000
        # split with no event shared between train and holdout
        if x['t'] < CUT:
            x['part'] = 'train'
            if x['y2h'] and x['drain_t'] >= CUT:
                x['part'] = 'drop'          # label belongs to a holdout-period event
            elif not x['y2h'] and x['t'] + H2 > CUT:
                # negative whose window crosses the cut: negative only if no drain at all before t+2h
                x['part'] = 'train'
        else:
            x['part'] = 'holdout'
        x['clean_neg'] = (x['y2h'] == 0 and x['van2h'] == 0 and x['cens'] == 0)
    return pts


def universe(name):
    if name == 'ALL':
        return lambda x: True
    if name == 'LIQ20':
        return lambda x: x['liq'] >= 20_000
    if name == 'LIQ50':
        return lambda x: x['liq'] >= 50_000
    if name == 'LIQ250':
        return lambda x: x['liq'] >= 250_000
    if name == 'CF':      # cost-first physical screen (fee <= 50 & liq >= 250k)
        return lambda x: x['liq'] >= 250_000 and x['fee'] <= 50
    if name == 'HF50':    # fee 100-125 & liq >= 50k
        return lambda x: x['liq'] >= 50_000 and x['fee'] >= 100
    if name == 'MF50':    # fee 55-95 & liq >= 50k
        return lambda x: x['liq'] >= 50_000 and 50 < x['fee'] < 100
    raise KeyError(name)


UNIVERSES = ['ALL', 'LIQ20', 'LIQ50', 'LIQ250', 'CF', 'HF50', 'MF50']


def metrics(pts, flag, part, uni='ALL'):
    """Point-level and event-level confusion for a flag function over one split and universe."""
    u = universe(uni)
    sel = [x for x in pts if x['part'] == part and u(x)]
    pos = [x for x in sel if x['y2h']]
    neg = [x for x in sel if x['clean_neg']]
    tp = sum(1 for x in pos if flag(x))
    fp = sum(1 for x in neg if flag(x))
    # event level: each drained pair with >= 1 point in its 2 h window
    ev = {}
    for x in pos:
        ev.setdefault(x['pair'], []).append(x)
    ev_any = sum(1 for v in ev.values() if any(flag(x) for x in v))
    ev_all = sum(1 for v in ev.values() if all(flag(x) for x in v))
    ev_frac = sum(sum(1 for x in v if flag(x)) / len(v) for v in ev.values()) / len(ev) if ev else NAN
    last = sum(1 for v in ev.values() if flag(max(v, key=lambda z: z['t'])))
    # pool level false positives: share of never-flag-worthy pools' points flagged, averaged per pool
    pp = {}
    for x in neg:
        pp.setdefault(x['pair'], []).append(1 if flag(x) else 0)
    pool_fpr = sum(sum(v) / len(v) for v in pp.values()) / len(pp) if pp else NAN
    pools_ever = sum(1 for v in pp.values() if any(v)) / len(pp) if pp else NAN
    flagged = tp + fp
    return {'n_pos': len(pos), 'n_neg': len(neg), 'recall': tp / len(pos) if pos else NAN,
            'fpr': fp / len(neg) if neg else NAN, 'precision': tp / flagged if flagged else NAN,
            'events': len(ev), 'ev_recall_any': ev_any / len(ev) if ev else NAN,
            'ev_recall_all': ev_all / len(ev) if ev else NAN, 'ev_recall_mean': ev_frac,
            'ev_recall_last': last / len(ev) if ev else NAN,
            'pool_fpr': pool_fpr, 'pools_ever_flagged': pools_ever, 'neg_pools': len(pp)}


def fmt_metrics(m):
    def f(v):
        return 'nan' if v != v else ('%.3f' % v if isinstance(v, float) else str(v))
    return ' '.join('%s=%s' % (k, f(v)) for k, v in m.items())
