"""Shared loader: every closed trade in every ledger COPY, deduplicated by (account, trade id).

Never touches the live ledgers (.runtime/accounts/state.json, users/*/state.json,
strategy_lab*.json); only backup folders, .runtime/archive and .runtime/accounts/archive.
"""
import itertools, json, os, re

ROOT = 'C:/Users/Chavd/neomemecoins/.runtime/'
LIVE = os.path.normcase(os.path.normpath(ROOT + 'accounts'))
LIVE_ARCHIVE = os.path.normcase(os.path.normpath(ROOT + 'accounts/archive'))
B58 = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,48}')


def ab(s):
    return B58.sub(lambda m: m.group(0)[:8], str(s))


def allowed(p):
    n = os.path.normcase(os.path.normpath(p))
    return not (n.startswith(LIVE + os.sep) and not n.startswith(LIVE_ARCHIVE + os.sep))


def account_files():
    out = []
    for dp, dn, fn in itertools.chain(os.walk(ROOT), os.walk(ROOT + 'accounts/archive')):
        if not allowed(dp + '/x'):
            dn[:] = []
            continue
        for f in fn:
            if f == 'state.json':
                p = os.path.join(dp, f).replace('\\', '/')
                if allowed(p):
                    out.append(p)
    return sorted(set(out))


def lab_files():
    out = []
    for dp, dn, fn in itertools.chain(os.walk(ROOT), os.walk(ROOT + 'accounts/archive')):
        if not allowed(dp + '/x'):
            dn[:] = []
            continue
        for f in fn:
            if f == 'strategy_lab.json':
                p = os.path.join(dp, f).replace('\\', '/')
                if allowed(p):
                    out.append(p)
    return sorted(set(out))


def account_of(path):
    m = re.search(r'users/([0-9a-f]{8})', path)
    return m.group(1) if m else 'main'


def load_account_trades():
    """{(account, id): (trade, source_path)}; later file mtime wins for the record content."""
    seen = {}
    for p in sorted(account_files(), key=os.path.getmtime):
        try:
            d = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        h = d.get('history')
        if not isinstance(h, list):
            continue
        acct = account_of(p)
        for t in h:
            if not isinstance(t, dict) or not t.get('closed_at'):
                continue
            key = (acct, t.get('id') or '%s:%s' % (t.get('session_id'), t.get('trade_no')))
            seen[key] = (t, p)
    return seen


def load_lab_trades():
    seen = {}
    for p in sorted(lab_files(), key=os.path.getmtime):
        try:
            d = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        books = d.get('books')
        if not isinstance(books, dict):
            continue
        for name, b in books.items():
            if not isinstance(b, dict):
                continue
            for t in b.get('history', []) or []:
                if not isinstance(t, dict):
                    continue
                key = (name, t.get('strategy_id'), t.get('pairAddress'), t.get('opened_at'), t.get('trade_no'))
                seen[key] = (t, p)
    return seen
