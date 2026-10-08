"""TRAIN ONLY (exact position semantics, grid_train3.pkl): which exit captures the right tail? Per universe,
reference exit policies with mean net50, win rate, top-5% share of gross gains and the sum without the top 5%."""
import pickle, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
R = pickle.load(open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits/grid_train3.pkl', 'rb'))
REFS = ['FIX sNone tNone h60', 'FIX sNone tNone h240', 'FIX s-5 t10 h60', 'FIX sNone t5 h240', 'FIX s-35 t40 h240',
        'TRAIL s-35 a40 w2 h240', 'TRAIL s-35 a3 w2 h240', 'TRAIL s-20 a10 w7 h240', 'BE sNone t20 a3 b0.5 h120',
        'PARTIAL s-20 t1_10 a20 w12 h240']
for sig, unis in (('rnd', ('cf', 'mid', 'hi', 'hi20', 'all50')), ('mom', ('hi', 'hi20', 'all50')), ('vacc', ('mid', 'hi', 'hi20', 'all50'))):
    for u in unis:
        cell = {k[2]: v for k, v in R.items() if k[0] == sig and k[1] == u}
        if not cell:
            continue
        best = max(cell.items(), key=lambda kv: kv[1]['mean50'])
        print('#### %s / %s   (best of %d policies: %s mean50 %.2f n %d)' % (sig, u, len(cell), best[0], best[1]['mean50'], best[1]['n']))
        for name in REFS:
            a = cell.get(name)
            if not a:
                continue
            print('  %-34s n=%4d pairs=%3d mean50=%7.2f med=%7.2f win=%5.1f top5%%->%4.0f%% of gross gains | sum$ %7.0f | sum$ w/o top5%% %7.0f' % (
                name, a['n'], a['pairs'], a['mean50'], a['med50'], a['win'], 100 * a['top5_share_gains'], a['sum50'], a['sum_wo_top5']))
