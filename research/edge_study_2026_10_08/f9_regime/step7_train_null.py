"""Step 7 (TRAIN ONLY): null distributions. For each selected config, 20 random-entry runs (different salts)
in the same universe and exits at a matched count: (a) no regime filter, (b) same regime filter.
Where does the config's train mean fall? Small-sample noise check before any holdout look."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategies as S
H = S.H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
setups = [
    ('F9_DIP_MKTDIP_E2', S.CONFIGS[0], 0.0007, 0.004, S._mkt_dip),
    ('F9_RANDOM_BREADTHLOW_E2', S.CONFIGS[1], 0.0003, 0.004, S._breadth_low),
    ('F9_DIP_MKTDIPN_H30', S.CONFIGS[2], 0.00035, 0.004, S._mkt_dip_native),
]
for name, cfg, p_plain, p_reg, reg in setups:
    tr = H.simulate(cfg['signal'], t_to=cut, **cfg['kwargs'])
    me = sum(x['net50'] for x in tr) / len(tr)
    for label, mk in (('random, no filter', lambda salt: (lambda P: S.U_liquid(P) and S.rnd(p_plain, salt)(P))),
                      ('random + same regime filter', (lambda salt: (lambda P: S.U_liquid(P) and S.rnd(p_reg, salt)(P) and reg(P))) if p_reg else None)):
        if mk is None:
            continue
        means, ns = [], []
        for k in range(20):
            rt = H.simulate(mk('null%d' % k), t_to=cut, **cfg['kwargs'])
            if rt:
                means.append(sum(x['net50'] for x in rt) / len(rt))
                ns.append(len(rt))
        means.sort()
        rank = sum(1 for m in means if m < me) / len(means)
        print('%-24s config train mean50=%6.2f n=%d | %-28s n~%d  null mean=%6.2f  p5=%6.2f p50=%6.2f p95=%6.2f  config pct-rank=%.2f' % (
            name, me, len(tr), label, sum(ns) // len(ns), sum(means) / len(means), means[1], means[10], means[18], rank))
