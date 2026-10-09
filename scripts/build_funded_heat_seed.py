"""Bounded read-only replay of actual recent market prices; never a trading backtest."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from build_ticker_registry_seed import replay_window, position_mark_row
import structural_rug_guard as rug
from funded_active_paper import finite


def build(journal, now, *, max_bytes=1024*1024*1024):
    window=replay_window(journal,61/1440,max_bytes=max_bytes)
    rows=[]; latest={}; read_rows=0
    with journal.open('rb') as handle:
        handle.seek(window['start_offset'])
        # Take a fixed end: the recorder may keep appending while we read.
        while handle.tell()<window['journal_bytes']:
            line=handle.readline(); read_rows+=1
            try:
                row=json.loads(line)
                c=row.get('coin') or {}
                available=finite(row.get('available_at'),-1)
                observed=finite(c.get('updatedAt'),-1)
                if (row.get('source',{}).get('kind')!='MAIN_SHARED_READ_ONLY_OBSERVATION'
                        or position_mark_row(row) or not rug.market_observation(c)
                        or not isinstance(c.get('sources'),list) or not c['sources']
                        or not all(isinstance(s,str) and s.strip() for s in c['sources'])
                        or not now-3_660_000<=observed<=available<=now
                        or finite(c.get('priceUsd'))<=0
                        or not all(isinstance(c.get(k),str) and c[k].strip() for k in ('address','pairAddress'))):
                    continue
                key=(c['address'],c['pairAddress'])
                if observed<=latest.get(key,0):
                    continue
                latest[key]=observed
                rows.append({'available_at':available,'coin':{k:c[k] for k in
                    ('address','pairAddress','priceUsd','updatedAt','sources')}})
                if len(rows)>150_000:
                    raise ValueError('Too many unique observations; refusing truncated proof')
            except (json.JSONDecodeError,AttributeError,TypeError,KeyError):
                continue
    rows.sort(key=lambda r:(r['available_at'],r['coin']['updatedAt']))
    return {'version':'FUNDED_HEAT_SEED_V1','source':str(journal.resolve()),'observations':rows,
            'observed_until':max((r['available_at'] for r in rows),default=0),
            'observed_since':min((r['available_at'] for r in rows),default=0),
            'read_rows':read_rows,'replay':window,'recorded_prices_only':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.out.resolve()==args.journal.resolve():
        parser.error('Output cannot be the journal')
    started=time.monotonic()
    seed=build(args.journal,int(time.time()*1000))
    if not seed['observations'] or int(time.time()*1000)-seed['observed_until']>120_000:
        raise SystemExit('No fresh actual observations; no seed written')
    payload=json.dumps(seed,separators=(',',':'))
    if len(payload.encode('utf-8'))>32*1024*1024:
        raise SystemExit('Seed exceeds bounded loader size')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    temp=args.out.with_name(args.out.name+f'.{os.getpid()}.tmp')
    temp.write_text(payload,encoding='utf-8')
    os.replace(temp,args.out)
    print(json.dumps({'path':str(args.out),'observations':len(seed['observations']),
        'recorded_span_minutes':(seed['observed_until']-seed['observed_since'])/60_000,
        'read_megabytes':(seed['replay']['journal_bytes']-seed['replay']['start_offset'])/1024/1024,
        'elapsed_seconds':round(time.monotonic()-started,2),'input_read_only':True}))


if __name__=='__main__':
    main()
