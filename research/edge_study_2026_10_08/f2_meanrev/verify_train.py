"""TRAIN-ONLY reproduction check of strategies.py (entries restricted to t < cut)."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev')
import strategies as S
H = S.H
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
CUT = H.split_t(meta)
for c in S.CONFIGS + S.BASELINES:
    tr = H.simulate(c['signal'], t_to=CUT, **c['kwargs'])
    st = H.summarize(tr)
    print(c['name'], {k: st.get(k) for k in ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'pf', 'top_pair_share', 'exits')})
