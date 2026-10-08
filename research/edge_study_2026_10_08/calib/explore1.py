"""Explore ledger-copy structure (read-only)."""
import json, sys
R = 'C:/Users/Chavd/neomemecoins/.runtime/release-backup-20261008-101156/.runtime/accounts/'
paths = [R + 'state.json', R + 'users/42d3192d-f033-4061-85d6-2408c5e168e7/state.json', R + 'strategy_lab_compact.json']


def shape(o, depth=0, maxd=2):
    if depth > maxd:
        return type(o).__name__
    if isinstance(o, dict):
        return {k: (shape(v, depth + 1, maxd) if isinstance(v, (dict, list)) else type(v).__name__) for k, v in list(o.items())[:60]}
    if isinstance(o, list):
        return ['list[%d]' % len(o)] + ([shape(o[0], depth + 1, maxd)] if o else [])
    return type(o).__name__


for p in paths:
    d = json.load(open(p, encoding='utf-8'))
    print('=====', p[-60:])
    for k, v in d.items():
        if isinstance(v, list):
            print(' ', k, 'list', len(v))
        elif isinstance(v, dict):
            print(' ', k, 'dict', len(v), list(v.keys())[:15])
        else:
            s = str(v)
            print(' ', k, type(v).__name__, s[:80] if 'key' not in k.lower() and 'secret' not in k.lower() and 'token' not in k.lower() else '<redacted>')
