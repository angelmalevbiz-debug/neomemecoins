"""Print one history trade (addresses abbreviated) and the strategy-lab book structure."""
import json, re, sys
R = 'C:/Users/Chavd/neomemecoins/.runtime/release-backup-20261008-101156/.runtime/accounts/'
B58 = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,48}')


def ab(s):
    return B58.sub(lambda m: m.group(0)[:8] + '..', s)


def dump(o, ind=0, maxlen=160, maxd=4):
    pad = '  ' * ind
    if isinstance(o, dict):
        for k, v in o.items():
            if any(w in k.lower() for w in ('secret', 'private', 'apikey', 'api_key', 'token_key')):
                print(pad + k + ': <redacted>')
                continue
            if isinstance(v, (dict, list)) and ind < maxd:
                print(pad + ab(str(k)) + ':' + (' list[%d]' % len(v) if isinstance(v, list) else ''))
                dump(v if isinstance(v, dict) else v[:2], ind + 1, maxlen, maxd)
            else:
                print(pad + ab(str(k)) + ': ' + ab(json.dumps(v, ensure_ascii=False))[:maxlen])
    elif isinstance(o, list):
        for x in o:
            print(pad + '-')
            dump(x, ind + 1, maxlen, maxd)
    else:
        print(pad + ab(json.dumps(o, ensure_ascii=False))[:maxlen])


which = sys.argv[1] if len(sys.argv) > 1 else 'hist'
if which == 'hist':
    d = json.load(open(R + 'state.json', encoding='utf-8'))
    h = d['history']
    print('n history', len(h))
    dump(h[int(sys.argv[2]) if len(sys.argv) > 2 else -1], maxd=3)
elif which == 'lab':
    d = json.load(open(R + 'strategy_lab_compact.json', encoding='utf-8'))
    b = d['books']
    k0 = sys.argv[2] if len(sys.argv) > 2 else list(b)[0]
    print({k: (len(v.get('history', [])) if isinstance(v, dict) else None) for k, v in b.items()})
    bk = b[k0]
    print('book keys', list(bk.keys()))
    hist = bk.get('history') or bk.get('closed') or []
    if hist:
        dump(hist[-1], maxd=3)
