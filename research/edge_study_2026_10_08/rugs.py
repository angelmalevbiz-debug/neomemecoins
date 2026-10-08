"""Rug census inside the cost-first universe (read-only over the dataset)."""
import json, sys
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
rows = []
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ever_cf = False
    first_cf = None
    for i in range(len(s['t'])):
        P = H.Past(s, i)
        liq = s['liq'][i]
        if liq >= 250_000 and H.fee_bps(s, i) <= 50:
            ever_cf = True
            first_cf = i
            break
    if not ever_cf:
        continue
    peak_liq = max(s['liq'][first_cf:])
    last_liq = s['liq'][-1]
    pmax = max(s['price'][first_cf:])
    plast = s['price'][-1]
    rug = last_liq < 0.1 * peak_liq or plast < 0.2 * pmax
    mint = s['mint'] or ''
    age0 = s['age'][first_cf]
    mc0, liq0 = s['mcap'][first_cf], s['liq'][first_cf]
    rows.append({'sym': s['sym'], 'pair': pair[:8], 'mint_pump_suffix': mint.endswith('pump'), 'mint': mint[:8],
                 'rug': rug, 'age_min_at_entry_window': round(age0, 1), 'mcap_m': round(mc0 / 1e6, 2),
                 'liq_k': round(liq0 / 1e3), 'liq_to_mcap_pct': round(100 * liq0 / mc0, 2) if mc0 > 0 else None,
                 'peak_liq_k': round(peak_liq / 1e3), 'last_liq_k': round(last_liq / 1e3),
                 'price_drop_pct': round(100 * (plast / pmax - 1), 1) if pmax > 0 else None,
                 'hours_seen': round((s['t'][-1] - s['t'][first_cf]) / 3.6e6, 2),
                 'src': int(s['src'][first_cf]), 'boost': s['boost'][first_cf]})
rows.sort(key=lambda r: (not r['rug'], r['sym'] or ''))
for r in rows:
    print(json.dumps(r, ensure_ascii=True))
n = len(rows); nr = sum(r['rug'] for r in rows)
print('pairs ever in universe', n, 'rugged', nr)
for flag in (True, False):
    sub = [r for r in rows if r['mint_pump_suffix'] == flag]
    print('mint ends with pump =', flag, 'pairs', len(sub), 'rugged', sum(r['rug'] for r in sub))
live = {'GhBPuDpt': 'WOSE live', 'D2pVedgH': 'GOIF live', 'HiPe6mDS': 'SARP live'}
for p, s in series.items():
    for pre, label in live.items():
        if p.startswith(pre):
            print(label, 'mint', (s['mint'] or '')[:8], 'pump_suffix', (s['mint'] or '').endswith('pump'), 'age_min', s['age'][-1],
                  'liq_k', round(s['liq'][-1] / 1e3), 'mcap_m', round(s['mcap'][-1] / 1e6, 1), 'points', len(s['t']))
syms = {}
for p, s in series.items():
    syms.setdefault(s['sym'], set()).add(p[:8])
print('tickers with several pairs:', {k: sorted(v) for k, v in syms.items() if len(v) > 2 and k in {r['sym'] for r in rows}})
