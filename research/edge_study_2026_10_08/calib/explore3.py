"""Flatten a trade and list relevant key paths (read-only, addresses abbreviated)."""
import json, re, sys
B58 = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,48}')
SKIP = ('effective_entry_thresholds', 'entry_flow', 'verified_entry_flow', 'entry_context', 'imageUrl')


def ab(s):
    return B58.sub(lambda m: m.group(0)[:8] + '..', s)


def flat(o, pre=''):
    if isinstance(o, dict):
        for k, v in o.items():
            if pre == '' and k in SKIP:
                continue
            yield from flat(v, pre + '.' + str(k) if pre else str(k))
    elif isinstance(o, list):
        if len(o) and all(not isinstance(x, (dict, list)) for x in o):
            yield pre, o[:6]
        else:
            for i, x in enumerate(o[:4]):
                yield from flat(x, pre + '[%d]' % i)
    else:
        yield pre, o


path = sys.argv[1]
src = sys.argv[2]  # 'history' or 'users'
idx = int(sys.argv[3]) if len(sys.argv) > 3 else -1
pat = sys.argv[4] if len(sys.argv) > 4 else ''
d = json.load(open(path, encoding='utf-8'))
h = d[src]
print('n', len(h))
t = h[idx]
for k, v in flat(t):
    if pat and not re.search(pat, k, re.I):
        continue
    print(ab(k), '=', ab(json.dumps(v, ensure_ascii=False))[:200])
