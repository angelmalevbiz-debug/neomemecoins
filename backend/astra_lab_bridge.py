"""Read-only projection of the independently running Astra paper book."""
import json, os, time
from pathlib import Path


def merge_astra_snapshot(state):
    path=Path(os.getenv('NEO_ASTRA_DATA_DIR','/var/lib/neo-market'))/'astra_6_brain.json'
    try:
        source=json.loads(path.read_text())
        book=source['book']
        if book.get('id')!='ASTRA_6_BRAIN': return state
        stamp=int(time.time()*1000)
        hide={'entry_quote','entry_sell_check','last_sell_quote'}
        def compact(p):
            return {k:v for k,v in p.items() if k not in hide}
        b={**book,'positions':[compact(p) for p in book.get('positions',[])],
           'position':compact(book['position']) if book.get('position') else None,
           'history':[compact(t) for t in book.get('history',[])[:100]]}
        astra={**source,'book':b}
        if stamp-int(source.get('updated_at',0))>15000: astra['status']='stale'
        # Crucially, do not insert Astra into the running legacy STATE object.
        return {**state,'books':{**state.get('books',{}),'ASTRA_6_BRAIN':b},
                'stats':{**state.get('stats',{}),'ASTRA_6_BRAIN':source['stats']},'astra':astra}
    except (OSError,ValueError,KeyError,TypeError):
        return state
