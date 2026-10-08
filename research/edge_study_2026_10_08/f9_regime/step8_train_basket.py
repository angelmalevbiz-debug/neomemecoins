"""Step 8 (TRAIN ONLY): deterministic market-dip basket (no random salt): every universe pool is entered
while the market regime is 'down', one position per pool, cooldown 300 s after each exit."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategies as S
H = S.H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
for name, sig in (('BASKET br15<0.35', lambda P: S.U_liquid(P) and S._breadth_low(P)),
                  ('BASKET med15<-0.2', lambda P: S.U_liquid(P) and S._mkt_dip(P))):
    tr = H.simulate(sig, t_to=cut, **S.E2)
    sm = H.summarize(tr)
    print(name, {k: sm.get(k) for k in ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'pf', 'ci95_mean_usd', 'top_pair_share', 'exits')})
