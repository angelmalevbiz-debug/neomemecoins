"""What did the tape seats observe? Classify every (minute, pool) that was on the tape
(fast-flow quality COMPLETE or DEGRADED in obs) by the scheduler group it most likely had and by
whether ANY live engine could have traded it (passes that engine's non-flow rules)."""
import sys, os, json, sqlite3, collections
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + '/../../backend' + '')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winner_ensemble as WE
import order_flow_adaptive_oct4 as OFA
import cost_first_engine_profile as CFP
import paper_market_feasibility as PMF
from funnel import coin_of, COLS, rt_pct, ladder_min, main_requested

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(os.path.dirname(HERE), 'obs.sqlite3')
con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
units = {}
q = "SELECT %s FROM o WHERE ff_q IN ('COMPLETE','DEGRADED')" % ','.join(COLS)
for row in con.execute(q):
    r = dict(zip(COLS, row))
    coin = coin_of(r)
    rules = WE.market_candidates(coin)
    feas = PMF.execution_feasibility(coin, 1.5, base_slippage_bps=0, latency_buffer_bps=0)['model_cost_feasible']
    grp = 'FEASIBLE' if (rules and feas) else 'OVER_BUDGET_OR_UNKNOWN' if rules else 'EXPLORATION_OR_PIN'
    rt = rt_pct(coin, ladder_min(main_requested(coin)))
    we_ok = bool(rules) and rt is not None and rt <= 1.5
    ofa_rej = [x for x in OFA.market_rejections(coin, now=r['t']) if x != 'stale_feed']
    rt200 = rt_pct(coin, 200.0)
    ofa_ok = not ofa_rej and rt200 is not None and rt200 <= 2.75
    cf_ok = not CFP.universe_rejections(coin, 200.0)
    k = (int(r['t'] // 60000), r['pair'])
    u = units.setdefault(k, {'grp': set(), 'we': False, 'ofa': False, 'cf': False, 'sym': r['sym'], 'complete': False})
    u['grp'].add(grp)
    u['we'] |= we_ok
    u['ofa'] |= ofa_ok
    u['cf'] |= cf_ok
    u['complete'] |= r['ff_q'] == 'COMPLETE'
n = len(units)
c = collections.Counter()
for u in units.values():
    g = 'FEASIBLE' if 'FEASIBLE' in u['grp'] else 'OVER_BUDGET_OR_UNKNOWN' if 'OVER_BUDGET_OR_UNKNOWN' in u['grp'] else 'EXPLORATION_OR_PIN'
    c[g] += 1
print('tape (minute,pool) units', n)
print('by likely scheduler group:', {k: '%d (%.1f%%)' % (v, 100 * v / n) for k, v in c.most_common()})
anyok = sum(1 for u in units.values() if u['we'] or u['ofa'] or u['cf'])
print('units tradable by >=1 engine on non-flow rules:', anyok, round(100 * anyok / n, 1), '%')
for e in ('we', 'ofa', 'cf'):
    s = sum(1 for u in units.values() if u[e])
    sc = sum(1 for u in units.values() if u[e] and u['complete'])
    print('  %s eligible units %d (%.1f%%), of which with COMPLETE flow %d' % (e, s, 100 * s / n, sc))
none = collections.Counter(u['sym'] for u in units.values() if not (u['we'] or u['ofa'] or u['cf']))
print('top seat-minutes on pools NO engine could trade:', none.most_common(15))
json.dump({'units': n, 'groups': c, 'tradable_any': anyok}, open(os.path.join(HERE, 'seatuse.json'), 'w'), indent=1)
