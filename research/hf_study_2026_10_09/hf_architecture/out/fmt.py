import json,sys
for line in open(sys.argv[1]):
    if not line.startswith('{'):
        print(line.strip()); continue
    r=json.loads(line)
    ex=r.get('exits') or {}
    print(f"{r['cfg']:<46} {r['part']:<7} n={r['n']:<5} tph={r['trades_per_hour']:<6} act_h={r['active_hours']:<6} tpah={r['trades_per_active_hour']:<6} n50={r['mean_net50_pct']:<7} n0={r['mean_net0_pct']:<7} bk={r['mean_booked_pct']:<7} $50/t={r['mean_usd50']:<8} $50/h={r['usd50_per_hour']:<8} w50={r['win_rate_net50']:<4} w0={r['win_rate_net0']:<5} pairs={r['pairs']:<3} top={r['top_pair_share']:<6} ci={r['ci95_mean_usd50_pair']} dd={r['max_drawdown_pct_net50']:<6} bkTot={r['total_usd_booked']:<9} hold={r['avg_hold_s']:<6} lag={r['avg_entry_lag_s']:<5} kill={r['killed_after_h']} pause={r['pause_at_utc_hour']} ex={ex}")
