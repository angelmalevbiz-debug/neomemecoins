import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import PaperExitResearchPanel, { type PaperExitResearchSnapshot } from '../src/components/PaperExitResearchPanel';

const snapshot: PaperExitResearchSnapshot = {version:'PAPER_EXIT_RESEARCH_V1',status:'OBSERVING',
  active_episodes:12,observations:100,resumed_episodes:12,full_entry_episodes:0,
  paired_mature_entries:0,unique_pools:0,utc_days:0,coverage_complete:true,
  censored_or_unresolved_mature_entries:0,capacity_refusals:0,
  review_gate:{paired_entries:150,unique_pools:25,utc_days:5},
  profiles:['BASELINE_30_LOCK4','QUICK_6_LOCK2','RUNNER_LOCK4'].map(id=>({id,observed_closes:0,
    wins:0,win_rate_pct:null,modeled_net_usd:0,median_hold_minutes:null,censored:0,pending:0,
    eligible_paired_closes:0,stressed_mean_net_usd:null,status:'INSUFFICIENT_PROSPECTIVE_DATA'}))};

test('exit research shows paired frozen alternatives without calling them trades or profits', () => {
  const html=renderToStaticMarkup(createElement(PaperExitResearchPanel,{data:snapshot,connected:true}));
  for (const text of ['Бърз вариант','Сегашен изход','Следване на движението','Сравнение, не сделки',
    'не с изпълнима wallet котировка','изключени от оценката','0/150','0/25','0/5','WR —']) assert.ok(html.includes(text));
  assert.doesNotMatch(html,/100.0% WR/);
});

test('missing research is not a fake zero-result experiment', () => {
  assert.equal(renderToStaticMarkup(createElement(PaperExitResearchPanel,{connected:true})), '');
});

test('stale connection and incomplete paths remain visible and block acceptance', () => {
  const html=renderToStaticMarkup(createElement(PaperExitResearchPanel,{connected:false,
    data:{...snapshot,coverage_complete:false,censored_or_unresolved_mature_entries:3,capacity_refusals:1}}));
  assert.match(html,/не са актуални/);
  assert.match(html,/приемане на вариант е блокирано/);
  assert.match(html,/Непълни зрели сравнения 3/);
  assert.match(html,/не са скрити/);
});

test('research errors do not imply financial permissions and favorable models still need review', () => {
  const html=renderToStaticMarkup(createElement(PaperExitResearchPanel,{connected:true,error:'model mismatch',
    data:{...snapshot,profiles:[{...snapshot.profiles[1],observed_closes:2,win_rate_pct:50,
      modeled_net_usd:-2,median_hold_minutes:2.5,status:'CANDIDATE_FOR_MANUAL_REVIEW_NOT_VALIDATED'}]}}));
  assert.match(html,/model mismatch/);
  assert.match(html,/не разрешава нови сделки/);
  assert.match(html,/не е валидиран/);
  assert.match(html,/\$-2.00/);
  assert.match(html,/2.5 мин/);
});
