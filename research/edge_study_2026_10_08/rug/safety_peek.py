"""Bounded read-only peek (30 MB window inside the dataset's byte range) at the 'safety' block of raw
observation rows for a few family pools, to see whether safety.allowed=0 means 'evaluated and blocked'
or 'not evaluated'. Nothing is written outside this folder; addresses are abbreviated in the output."""
import collections, json, sys
from rugcommon import _series

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
SRC = 'C:/Users/Chavd/neomemecoins/.runtime/accounts/training/observations.jsonl'
want = {}
for p, s in _series.items():
    if s['dex'] == 'pumpswap' and (s['sym'] or '') in ('USDP', 'USDF', 'UDR', 'TWEETCRAFT', 'SharkTank', 'knightcat', 'TITS', 'SARP'):
        want[p] = s['sym']
with open(SRC, 'rb') as f:
    f.seek(12_900_000_000)
    f.readline()
    blob = f.read(30_000_000)
seen = collections.Counter()
shape = collections.Counter()
for line in blob.split(b'\n'):
    if not line:
        continue
    hit = None
    for p in want:
        if p.encode() in line:
            hit = p
            break
    try:
        r = json.loads(line)
    except Exception:
        continue
    saf = r.get('safety')
    key = 'none' if saf is None else ('keys:' + ','.join(sorted(saf.keys()))[:120] if isinstance(saf, dict) else type(saf).__name__)
    shape[key] += 1
    if hit and seen[hit] < 2:
        seen[hit] += 1
        txt = json.dumps(saf)[:600] if saf is not None else 'None'
        for p in want:  # abbreviate any address
            txt = txt.replace(p, p[:8])
        mint = (r.get('coin') or {}).get('address') or ''
        if mint:
            txt = txt.replace(mint, mint[:8])
        print(want[hit], hit[:8], 'safety =', txt)
print('safety block shapes in window:', shape.most_common(6))
