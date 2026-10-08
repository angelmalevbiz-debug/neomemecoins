"""TRAIN ONLY: print stop x TP (FIX family) mean50 matrices per hold for chosen cells, to see plateaus."""
import pickle, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
R = pickle.load(open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits/' + (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith('.pkl') else 'grid_train.pkl') + r'', 'rb'))
cells = [a.split('/') for a in sys.argv[1:] if not a.endswith('.pkl')] or [('mom', 'hi20'), ('rnd', 'hi'), ('rnd', 'cf'), ('rnd', 'hi20')]
stops = ['None', '-3', '-5', '-8', '-12', '-20', '-35']
tps = ['3', '5', '10', '20', '40', '80', 'None']
for sig, u in cells:
    for h in ('60', '120', '240'):
        print('== %s/%s FIX hold %s  (rows stop, cols tp) mean net50 %% [n]' % (sig, u, h))
        print('        ' + ''.join('%8s' % t for t in tps))
        for s in stops:
            row = []
            for t in tps:
                a = R.get((sig, u, 'FIX s%s t%s h%s' % (s, t, h)))
                row.append('%8.2f' % a['mean50'] if a else '%8s' % '-')
            print('%8s' % s + ''.join(row))
    print('== %s/%s TRAIL hold 240 stop -35 / -20 / None (rows arm, cols width)' % (sig, u))
    for s in ('-35', '-20', 'None'):
        print(' stop', s, '       ' + ''.join('%8s' % w for w in (2, 4, 7, 12, 20)))
        for a_ in (3, 5, 10, 20, 40):
            row = []
            for w in (2, 4, 7, 12, 20):
                a = R.get((sig, u, 'TRAIL s%s a%s w%s h240' % (s, a_, w)))
                row.append('%8.2f' % a['mean50'] if a else '%8s' % '-')
            print('%14s' % a_ + ''.join(row))
