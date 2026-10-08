"""Survey every ledger COPY (never the live ledgers) for closed trades and quote fields."""
import json, os, re, sys, collections
ROOT = 'C:/Users/Chavd/neomemecoins/.runtime/'
LIVE = os.path.normcase(os.path.normpath(ROOT + 'accounts'))
LIVE_ARCHIVE = os.path.normcase(os.path.normpath(ROOT + 'accounts/archive'))


def allowed(p):
    n = os.path.normcase(os.path.normpath(p))
    if n.startswith(LIVE + os.sep) and not n.startswith(LIVE_ARCHIVE + os.sep):
        return False
    return True


files = []
import itertools
for dp, dn, fn in itertools.chain(os.walk(ROOT), os.walk(ROOT + 'accounts/archive')):
    if not allowed(dp + '/x'):
        dn[:] = []
        continue
    for f in fn:
        if f in ('state.json',) or (f.startswith('strategy_lab') and f.endswith('.json')) or f in ('engine-18800-state-response.json', 'engine-18802-state-response.json') or f == 'strategy-user-review-snapshot.json':
            p = os.path.join(dp, f)
            if allowed(p):
                files.append(p)
files.sort()
out = []
for p in files:
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception as e:
        print('ERR', p, e)
        continue
    rel = p.replace('\\', '/').replace(ROOT, '')
    if 'history' in d and isinstance(d['history'], list):
        h = d['history']
        modes = collections.Counter(x.get('execution_mode') for x in h)
        sess = collections.Counter(x.get('session_id') for x in h)
        hasq = sum(1 for x in h if x.get('entry_quote'))
        hasx = sum(1 for x in h if x.get('exit_quote'))
        hasp = sum(1 for x in h if x.get('preflight_sell_quote'))
        print('%-110s hist=%3d modes=%s q=%d pre=%d xq=%d sessions=%d' % (rel[:110], len(h), dict(modes), hasq, hasp, hasx, len(sess)))
    elif 'books' in d:
        tot = 0
        keys = collections.Counter()
        for name, b in (d['books'].items() if isinstance(d['books'], dict) else enumerate(d['books'])):
            if isinstance(b, dict):
                for k, v in b.items():
                    if isinstance(v, list):
                        keys[k] += len(v)
        print('%-110s LAB books=%d lists=%s' % (rel[:110], len(d['books']), dict(keys)))
    else:
        print('%-110s other keys=%s' % (rel[:110], list(d.keys())[:12]))
