"""Step 10 (POST-HOC, descriptive only - no selection): decompose the DIP comparator into
'systematic dip' (market med15 < -0.2) vs 'idiosyncratic dip' (market not dipping), train and holdout,
plus average modeled round-trip cost of the trades."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategies as S
H = S.H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
not_dip = lambda P: (lambda v: v == v and v >= -0.2)(S.regime('med15', P.t))
for name, sig, kw in (('DIP systematic (med15<-0.2) E2', lambda P: S.sig_dip(P) and S._mkt_dip(P), S.E2),
                      ('DIP idiosyncratic (med15>=-0.2) E2', lambda P: S.sig_dip(P) and not_dip(P), S.E2),
                      ('DIP systematic (med15<-0.2) H30', lambda P: S.sig_dip(P) and S._mkt_dip(P), S.E3),
                      ('DIP idiosyncratic (med15>=-0.2) H30', lambda P: S.sig_dip(P) and not_dip(P), S.E3)):
    tr = H.simulate(sig, **kw)
    ev = H.evaluate(tr)
    rc = [x['rt_cost'] for x in tr if x['rt_cost'] is not None]
    print('==', name, 'avg model RT cost %.2f%% (+1.00 stress)' % (sum(rc) / len(rc)))
    for part in ('train', 'holdout'):
        sm = ev[part]
        print('   %-8s n=%s pairs=%s mean50=%s median=%s win=%s pf=%s ci$=%s' % (part, sm.get('n'), sm.get('pairs'), sm.get('mean_pct'), sm.get('median_pct'), sm.get('win_rate'), sm.get('pf'), sm.get('ci95_mean_usd')))
