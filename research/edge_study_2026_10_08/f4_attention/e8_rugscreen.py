"""How often does the interim ticker-reuse rule fire only because the SAME mint was seen on another pair
(e.g. its own pump.fun bonding-curve pair before graduation)? Read-only, no outcomes used."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
idx = H._ticker_index()
pairs_flag = {'same_mint_only': 0, 'different_mint': 0}
pts = {'same_mint_only': 0, 'different_mint': 0, 'mcap_rule': 0}
total_pts = 0
for p, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    lst = idx.get(H.norm_ticker(s['sym']), [])
    seen_kind = None
    for i in range(len(s['t'])):
        total_pts += 1
        mc, liq, age = s['mcap'][i], s['liq'][i], s['age'][i]
        young = not (age >= 14 * 1440)
        if mc >= 20e6 and liq > 0 and liq / mc < 0.02 and young:
            pts['mcap_rule'] += 1
            continue
        if not young:
            continue
        others = [q for t0, q in lst if t0 <= s['t'][i] and q != p]
        if not others:
            continue
        kind = 'same_mint_only' if all(series[q]['mint'] == s['mint'] for q in others) else 'different_mint'
        pts[kind] += 1
        seen_kind = seen_kind or kind
    if seen_kind:
        pairs_flag[seen_kind] += 1
print('pumpswap SOL points', total_pts, 'flagged points by rule:', pts)
print('pairs whose ticker-rule flag comes only from the same mint vs a different mint:', pairs_flag)
