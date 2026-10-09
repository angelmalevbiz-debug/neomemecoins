import json,sys
rows=[json.loads(l) for l in open('p04_base.jsonl')]+[json.loads(l) for l in open('p04_rules.jsonl')]
band=[r for r in rows if r['n'] and 45<=r['tph']<=80]
print('in band',len(band))
for key,desc in (('usd50_per_h','least $/h lost'),('mean_net50','best %/trade')):
    print('==',desc)
    for rule in ('R0','CD300','CD60'):
        b=[r for r in band if r['rule']==rule]
        b.sort(key=lambda r:-r[key])
        for r in b[:6]:
            print(rule,r['univ'],r['spec'],r['slots'],r['size'],'tph',r['tph'],'net50',r['mean_net50'],'net0',r['mean_net0'],'$/h',r['usd50_per_h'],'pairs',r['pairs'],'top',r['top_pair_share'],'re60',r['reentry_share_le60s'],'bust',r['hours_to_bust_500'])
