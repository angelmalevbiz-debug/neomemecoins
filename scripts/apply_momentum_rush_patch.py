#!/usr/bin/env python3
"""One-shot repository patch for the isolated PAPER Momentum Rush Brain test strategy."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


def sha256_lf(path: Path) -> str:
    text = path.read_text(encoding='utf-8').replace('\r\n', '\n').replace('\r', '\n')
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


lab_path = ROOT / 'backend' / 'strategy_lab.py'
lab = lab_path.read_text(encoding='utf-8')

# The one-shot migration is retained for old checkouts. Running it again on an
# integrated engine must preserve the reviewed implementation and its ledger.
if ("import momentum_rush_brain as " in lab
        and "'id':'MOMENTUM_RUSH_BRAIN'" in lab):
    print('Momentum Rush PAPER strategy is already integrated; no files changed')
    sys.exit(0)

lab = replace_once(
    lab,
    "import lab_activity as activity\nfrom lab_position_marks import POSITION_MARK_FEED",
    "import lab_activity as activity\nimport momentum_rush_brain as rush\nfrom lab_position_marks import POSITION_MARK_FEED",
    'rush import',
)

momentum_line = " {'id':'MOMENTUM','name':'Momentum','rule':lambda f: f['score']>=90 and f['liq']>=15000 and 5<=f['m5']<=30 and f['bs']>=1.15 and f['lmc']>=.08 and 3<=f['age']<=300},"
lab = replace_once(
    lab,
    momentum_line,
    momentum_line + "\n {'id':'MOMENTUM_RUSH_BRAIN','name':'Momentum Rush Brain','rule':lambda f: True},",
    'strategy registration',
)

lab = replace_once(
    lab,
    "assert set(activity.RULES)=={s['id'] for s in STRATEGIES}, 'All 33 entries need a policy'",
    "assert set(activity.RULES)=={s['id'] for s in STRATEGIES}, 'Every Strategy Lab entry needs a policy'",
    'strategy policy assertion',
)

old_exit = """        # Unified 3:10 NET exit framework across all 33 strategies.\n        # Entry logic stays strategy-specific; exits are identical and include\n        # DEX fee, price impact, slippage/latency and network cost.\n        if total_live_pct<=-STOP_LOSS:\n            reason='STOP_LOSS_3_NET'\n        elif total_live_pct>=TAKE_PROFIT:\n            reason='TAKE_PROFIT_10_NET'\n        elif hold>=MAX_HOLD_MIN:\n            reason='ABSOLUTE_MAX_HOLD_60'\n"""
new_exit = """        # Standard books keep the common 3/10 NET framework. The high-frequency\n        # Rush TEST book can secure gains sooner, but never weakens the 3% net stop.\n        is_rush=pos.get('strategy_id')=='MOMENTUM_RUSH_BRAIN'\n        peak_net=max(num(pos.get('peak_net_pct'),total_live_pct),total_live_pct)\n        entry_liq=num(pos.get('entry_liquidity_usd'))\n        current_liq=pair_liquidity_usd(coin)\n        if total_live_pct<=-STOP_LOSS:\n            reason='STOP_LOSS_3_NET'\n        elif is_rush and entry_liq>0 and current_liq<entry_liq*.65:\n            reason='RUSH_LIQUIDITY_COLLAPSE'\n        elif is_rush and num(f.get('trades'))>=4 and num(f.get('ratio'))<.65 and num(f.get('sell_usd'))>num(f.get('buy_usd')):\n            reason='RUSH_FLOW_REVERSAL'\n        elif is_rush and peak_net>=4 and total_live_pct<=peak_net-2.5:\n            reason='RUSH_PROFIT_TRAIL'\n        elif is_rush and total_live_pct>=7:\n            reason='RUSH_TAKE_PROFIT_7_NET'\n        elif not is_rush and total_live_pct>=TAKE_PROFIT:\n            reason='TAKE_PROFIT_10_NET'\n        elif is_rush and hold>=20:\n            reason='RUSH_MAX_HOLD_20'\n        elif not is_rush and hold>=MAX_HOLD_MIN:\n            reason='ABSOLUTE_MAX_HOLD_60'\n"""
lab = replace_once(lab, old_exit, new_exit, 'rush exits')

lab = replace_once(
    lab,
    "                    'pnl_pct':round(total_live_pct,3),'open_pnl_usd':round(open_pnl,4),",
    "                    'pnl_pct':round(total_live_pct,3),'open_pnl_usd':round(open_pnl,4),\n                    'peak_net_pct':round(peak_net,3),",
    'rush peak tracking',
)

lab = replace_once(
    lab,
    "        eligible=[]\n        checked=0",
    "        eligible=[]\n        rush_strategy=strategy['id']=='MOMENTUM_RUSH_BRAIN'\n        rush_best_score=0.0\n        checked=0",
    'rush candidate state',
)

old_match = """        for coin,features in candidates:\n            # Funded promoted books use the stricter strategy definition declared\n            # in this lab. Shared activity rules stay unchanged for TEST/main PAPER.\n            matched=(strategy['rule'](features) if book.get('portfolio_group')=='PROMOTED_PAPER'\n                     else rule.matches(features))\n            if not matched:\n"""
new_match = """        for coin,features in candidates:\n            # Funded promoted books use their declared strict rules. Rush is a separate\n            # TEST-only brain that ranks a broader causal momentum universe.\n            rush_meta=rush.evaluate(book,coin,features,now) if rush_strategy else None\n            if rush_strategy:\n                rush_best_score=max(rush_best_score,num((rush_meta or {}).get('final_score')))\n                matched=bool((rush_meta or {}).get('allow'))\n            else:\n                matched=(strategy['rule'](features) if book.get('portfolio_group')=='PROMOTED_PAPER'\n                         else rule.matches(features))\n            if not matched:\n"""
lab = replace_once(lab, old_match, new_match, 'rush matching')

old_proposal = """            proposed=activity.affordable_entry(\n                coin,balance,entry_limit,entry_execution,exit_execution,\n                minimum_notional=min_notional\n            )\n            if proposed is None:\n                blocked_cost+=1\n                continue\n            eligible.append((proposed['initial_pnl_pct'],num(features.get('score')),\n                             coin,features,proposed))\n"""
new_proposal = """            candidate_limit=entry_limit\n            if rush_strategy:\n                candidate_limit=min(candidate_limit,rush.candidate_notional_limit(rush_meta or {},balance,candidate_limit))\n            if candidate_limit<min_notional:\n                blocked_cost+=1\n                continue\n            proposed=activity.affordable_entry(\n                coin,balance,candidate_limit,entry_execution,exit_execution,\n                minimum_notional=min_notional\n            )\n            if proposed is None:\n                blocked_cost+=1\n                continue\n            eligible.append((num((rush_meta or {}).get('final_score')),proposed['initial_pnl_pct'],\n                             num(features.get('score')),coin,features,proposed,rush_meta))\n"""
lab = replace_once(lab, old_proposal, new_proposal, 'rush proposal sizing')

lab = replace_once(
    lab,
    "            'risk_limited_notional_usd':round(entry_limit,4),\n        }\n        if strategy['id']=='SCALPER':",
    "            'risk_limited_notional_usd':round(entry_limit,4),\n        }\n        if rush_strategy:\n            book['entry_diagnostics'].update({\n                'brain_version':rush.VERSION,\n                'brain_best_score':round(rush_best_score,4),\n                'target_win_rate_pct':rush.TARGET_WIN_RATE_PCT,\n                'target_is_guarantee':False,\n            })\n        if strategy['id']=='SCALPER':",
    'rush diagnostics',
)

old_choose = """        if not eligible:\n            continue\n        _,_,coin,features,proposed=max(eligible,key=lambda item:(item[0],item[1]))\n        address=coin['address']; price=num(coin['priceUsd'])\n"""
new_choose = """        if not eligible:\n            continue\n        if rush_strategy:\n            chosen=max(eligible,key=lambda item:(item[0],item[1],item[2]))\n        else:\n            chosen=max(eligible,key=lambda item:(item[1],item[2]))\n        _,_,_,coin,features,proposed,rush_meta=chosen\n        address=coin['address']; price=num(coin['priceUsd'])\n"""
lab = replace_once(lab, old_choose, new_choose, 'rush ranking')

lab = replace_once(
    lab,
    "            'score':coin.get('score'),'entry_features':features,\n            'partial_realized_pnl':0.0,'partial_exits':[],",
    "            'score':coin.get('score'),'entry_features':features,\n            'momentum_rush_brain':rush_meta if rush_strategy else None,\n            'entry_liquidity_usd':round(pair_liquidity_usd(coin),4),\n            'entry_market_cap_usd':round(num(coin.get('marketCap') or coin.get('fdv')),4),\n            'peak_net_pct':round(proposed['initial_pnl_pct'],3),\n            'partial_realized_pnl':0.0,'partial_exits':[],",
    'rush position evidence',
)

lab_path.write_text(lab, encoding='utf-8', newline='\n')


test_path = ROOT / 'tests' / 'test_lab_activity.py'
test = test_path.read_text(encoding='utf-8')
test = replace_once(
    test,
    "        lab.STATE={'started_at':42,'books':{s['id']:lab.empty_book(s) for s in lab.STRATEGIES}}",
    "        lab.rush._SAMPLE_BY_PAIR.clear()\n        lab.STATE={'started_at':42,'books':{s['id']:lab.empty_book(s) for s in lab.STRATEGIES}}",
    'test rush memory reset',
)
test = replace_once(test, '    def test_all_33_rules_exist(self):\n        self.assertEqual(len(a.RULES),33)',
                    '    def test_all_34_rules_exist(self):\n        self.assertEqual(len(a.RULES),34)', 'test rule count')
test = test.replace('    def test_net_stop_all_33_no_loss_clamping(self):',
                    '    def test_net_stop_all_34_no_loss_clamping(self):', 1)

insert_before = "    def test_constants_preserved(self):\n"
new_tests = """    def test_momentum_rush_opens_broader_low_cap_setup_without_promoting_it(self):\n        c=coin()\n        c.update(score=82,marketCap=40000,liquidityUsd=12000,ageMinutes=12)\n        c['priceChange']={'m5':2,'h1':8}\n        c['volume']={'h1':9000}\n        c['txns']={'m5':{'buys':18,'sells':10}}\n        flow={ADDRESS:{'trades':5,'buys':4,'sells':1,'buy_usd':350,'sell_usd':100,\n                       'unique_wallets':4,'ratio':3.5,'max_sell':50}}\n        features=a.market_features(c,flow[ADDRESS])\n        self.assertFalse(a.RULES['MOMENTUM'].matches(features))\n        with patch.object(lab,'now_ms',return_value=NOW):\n            lab.maybe_open([c],flow)\n        book=lab.STATE['books']['MOMENTUM_RUSH_BRAIN']\n        self.assertEqual(book['portfolio_group'],'TEST')\n        self.assertIsNotNone(book['position'])\n        self.assertLessEqual(book['position']['notional_usd'],60)\n        self.assertEqual(book['position']['momentum_rush_brain']['target_win_rate_pct'],89.0)\n        self.assertFalse(book['position']['momentum_rush_brain']['target_is_guarantee'])\n\n    def test_momentum_rush_rejects_bearish_low_cap_flow(self):\n        c=coin()\n        c.update(score=86,marketCap=40000,liquidityUsd=14000,ageMinutes=15)\n        c['priceChange']={'m5':3,'h1':10}\n        c['txns']={'m5':{'buys':16,'sells':9}}\n        flow={'trades':6,'buys':2,'sells':4,'buy_usd':100,'sell_usd':450,\n              'unique_wallets':4,'ratio':.22,'max_sell':1800}\n        meta=lab.rush.evaluate(lab.STATE['books']['MOMENTUM_RUSH_BRAIN'],c,a.market_features(c,flow),NOW)\n        self.assertFalse(meta['allow'])\n        self.assertTrue(meta['verified_flow_bearish'])\n        self.assertTrue(meta['dump_risk'])\n\n"""
test = replace_once(test, insert_before, new_tests + insert_before, 'rush tests')
test_path.write_text(test, encoding='utf-8', newline='\n')


lock_path = ROOT / 'strategy-lock.json'
lock = json.loads(lock_path.read_text(encoding='utf-8'))
lock['source_commit'] = 'USER_AUTHORIZED_MOMENTUM_RUSH_PAPER_TEST_20261007'
files = lock.setdefault('support_files_sha256', {})
for rel in ('backend/strategy_lab.py', 'backend/lab_activity.py', 'backend/momentum_rush_brain.py'):
    files[rel] = sha256_lf(ROOT / rel)
reasons = lock.setdefault('hash_change_reasons', {})
reasons['backend/strategy_lab.py'] = ('Adds an isolated TEST-only Momentum Rush Brain book with broader causal momentum ranking, low-cap sizing, fast PAPER exits, and unchanged modeled fees/slippage/impact; promoted $1,000 cohort remains unchanged.')
reasons['backend/lab_activity.py'] = ('Registers the broad Momentum Rush TEST family, faster TEST cooldown, and $5 minimum research notional; no live execution and no promoted strategy rule changes.')
reasons['backend/momentum_rush_brain.py'] = ('Causal PAPER-only multi-factor momentum scoring with short-window acceleration, similarity memory, low-cap risk sizing, bearish-flow vetoes, and an explicitly non-guaranteed 89% research target.')
changes = lock.setdefault('support_file_changes', {})
changes['backend/strategy_lab.py'] = reasons['backend/strategy_lab.py']
changes['backend/lab_activity.py'] = reasons['backend/lab_activity.py']
changes['backend/momentum_rush_brain.py'] = reasons['backend/momentum_rush_brain.py']
lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')

print('Momentum Rush PAPER patch applied')
print('strategy_lab_sha256=', files['backend/strategy_lab.py'])
print('lab_activity_sha256=', files['backend/lab_activity.py'])
print('rush_brain_sha256=', files['backend/momentum_rush_brain.py'])
