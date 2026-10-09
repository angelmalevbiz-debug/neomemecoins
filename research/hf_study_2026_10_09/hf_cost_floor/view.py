"""Print compact tables from a p04_*.jsonl file. usage: view.py file [filter expr on r]"""
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1], encoding='utf-8')]
flt = sys.argv[2] if len(sys.argv) > 2 else 'True'
cols = sys.argv[3].split(',') if len(sys.argv) > 3 else None
hdr = ('univ spec     sl size rule        n    tph  h45  net50  netcal  net0  $/tr50  $/h50  $/hcal win50 win0 prs top  rt0  hold cyc  enl exl re60 re300 re1800 bust_h mdd%  gap van')
print(hdr)
for r in rows:
    if not eval(flt):
        continue
    if r['n'] == 0:
        print('%-4s %-8s %2d %4d %-10s n=0' % (r['univ'], r['spec'], r['slots'], r['size'], r['rule']))
        continue
    print('%-4s %-8s %2d %4d %-10s %5d %6.1f %4.2f %6.2f %6.2f %6.2f %7.3f %6.1f %6.1f %5.1f %4.1f %3d %4.2f %4.2f %5.0f %4.0f %4.0f %3.0f %4.2f %5.2f %6.2f %6s %5.1f %3d %3d' % (
        r['univ'], r['spec'], r['slots'], r['size'], r['rule'], r['n'], r['tph'], r['hours_ge45'], r['mean_net50'],
        r['mean_netcal'], r['mean_net0'], r['mean_usd50'], r['usd50_per_h'], r['usdcal_per_h'], r['win50'], r['win0'],
        r['pairs'], r['top_pair_share'], r['mean_rt_cost_model'], r['mean_hold_s'], r['mean_cycle_s'],
        r['mean_entry_lag_s'], r['mean_exit_lag_s'], r['reentry_share_le60s'], r['reentry_share_le300s'],
        r['reentry_share_le1800s'], r['hours_to_bust_500'], r['mdd_pct_500'], r['gap_closed'], r['vanished']))
