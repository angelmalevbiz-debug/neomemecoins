"""F5 simulation screen 3 (TRAIN entries only): outlier-robust comparison (trimmed mean, mean without top-3),
age filters, sustained breadth, no-chase filter; tape vs DexScreener analogue vs random in one universe."""
import sys, os, time, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from f5sig import H, fin, bshare5, ret, universe, rnd, tape_live, U, tb, TF

series, meta = H.load()
CUT = H.split_t(meta)
LOG = os.path.join(HERE, 'configs_log.jsonl')
EX = dict(stop=-10, tp=6, hold_min=30)


def robust(tr):
    xs = sorted(x['net50'] for x in tr)
    n = len(xs)
    if n < 10:
        return {'n': n}
    k = max(1, int(n * 0.05))
    trim = xs[k:n - k]
    by = {}
    for x in tr:
        by.setdefault(x['pair'], []).append(x['net50'])
    pairavg = sum(sum(v) / len(v) for v in by.values()) / len(by)
    return {'n': n, 'pairs': len(by), 'mean': round(sum(xs) / n, 2), 'median': round(xs[n // 2], 2),
            'trim5': round(sum(trim) / len(trim), 2), 'mean_wo_top3': round(sum(xs[:-3]) / (n - 3), 2),
            'pairavg': round(pairavg, 2), 'win': round(100 * sum(1 for v in xs if v > 0) / n, 1),
            'top_share': round(max(len(v) for v in by.values()) / n, 3)}


def age_ge(m):
    return lambda P: fin(P('age')) and P('age') >= m


def nochase(P):
    r = ret(P, 300)
    return fin(r) and -2 <= r <= 5


def sustained(P):
    f = TF.flow(P.static('pair'), P.t - 300_000, 300)
    return f is not None and f['ub'] >= 10


def ds(extra=None):
    def sig(P):
        if not U()(P):
            return False
        if extra is not None and not extra(P):
            return False
        return P('b5') >= 10 and fin(bshare5(P)) and bshare5(P) >= 0.55
    return sig


RUNS = [
    ('tb base (ref, already counted)', tb(), False),
    ('DS analog (ref, already counted)', ds(), False),
    ('tb age>=360', tb(extra=age_ge(360)), True),
    ('tb age>=1440', tb(extra=age_ge(1440)), True),
    ('tb nochase r300 in [-2,5]', tb(extra=nochase), True),
    ('tb sustained (prev 5m ub>=10)', tb(extra=sustained), True),
    ('DS analog age>=360', ds(age_ge(360)), True),
]
def main():
    n_cfg = 0
    for name, sig, counted in RUNS:
        t0 = time.time()
        tr = H.simulate(sig, t_to=CUT, **EX)
        n_cfg += counted
        r = robust(tr)
        print('%-36s %s %.0fs' % (name, json.dumps(r), time.time() - t0))
        sys.stdout.flush()
        with open(LOG, 'a') as fh:
            fh.write(json.dumps({'stage': 'simscreen3', 'name': name, 'exit': EX, 'part': 'train', 'robust': r}) + '\n')
    for name, univ in (('RANDOM tape-live', U()), ('RANDOM tape-live age>=360', lambda P: U()(P) and age_ge(360)(P))):
        for salt in ('a', 'b', 'c'):
            tr = H.simulate(lambda P: univ(P) and rnd(0.01, salt)(P), t_to=CUT, **EX)
            print('%-36s salt %s %s' % (name, salt, json.dumps(robust(tr))))
    print('new configs counted:', n_cfg)


if __name__ == '__main__':
    main()
