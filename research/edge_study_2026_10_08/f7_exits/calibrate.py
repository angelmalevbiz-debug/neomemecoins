"""TRAIN ONLY, counts only: pick random-baseline probabilities whose train trade count matches each config's."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits')
import harness as H
import strategies as S

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
universes = [('hi20',), ('hi20',), S.ALL50]
for cfg, tags in zip(S.CONFIGS, universes):
    n_cfg = len(H.simulate(cfg['signal'], t_to=cut, **cfg['kwargs']))
    out = []
    for p in (0.005, 0.01, 0.02, 0.04, 0.08):
        n = len(H.simulate(S.rnd_in(tags, p, 'f7base'), t_to=cut, **cfg['kwargs']))
        out.append((p, n))
    print(cfg['name'], 'train n', n_cfg, 'baseline train n by prob', out)
