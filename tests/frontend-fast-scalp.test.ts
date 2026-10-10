import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import PaperFastScalpPanel, { type PaperFastScalpSnapshot } from '../src/components/PaperFastScalpPanel';

const snapshot: PaperFastScalpSnapshot = {
  version:'PAPER_FAST_SCALP_V1_250_5M', status:'online', enabled:true,
  starting_balance:1000, balance:1000, available_cash_usd:1000,
  realized_pnl_usd:0, unrealized_pnl_usd:0, trades:0, win_rate_pct:null,
  entries_last_60m:0, positions:[], history:[],
  config:{notional_usd:250,take_profit_net_usd:5,stop_loss_net_usd:7.5,
    profit_arm_net_usd:2,profit_giveback_usd:.75,max_hold_seconds:300},
  diagnostics:{blocked_reason:'fast_move_does_not_cover_cost',market_candidates:4,signal_candidates:0,rejections:{}},
};

test('separate scalp capital and zero closes never imply profit or a win rate',()=>{
  const html=renderToStaticMarkup(createElement(PaperFastScalpPanel,{data:snapshot,connected:true}));
  for(const text of ['FAST SCALP','Тестов капитал $1000.00, не печалба','бавните стратегии са запазени',
    'Вход $250.00','цел +$5.00 нето','стоп −$7.50 нето','максимум 5 мин','WR — няма оценка',
    'разходите + 0.25','не се смесва','не се допълва автоматично']) assert.ok(html.includes(text),text);
  assert.doesNotMatch(html,/WR 0.0%|WR 100.0%/);
});

test('no snapshot is not a fabricated zero experiment',()=>{
  assert.equal(renderToStaticMarkup(createElement(PaperFastScalpPanel,{connected:true})), '');
});

test('stale/error data and open losses are visible, not counted as realized profit',()=>{
  const html=renderToStaticMarkup(createElement(PaperFastScalpPanel,{connected:false,
    data:{...snapshot,status:'degraded',error:'ledger unavailable',unrealized_pnl_usd:-3.5,
      positions:[{trade_no:1,symbol:'TEST',address:'mint',pairAddress:'pool',opened_at:1,
        notional_usd:250,open_pnl_usd:-3.5,quote_status:'stale',profit_floor_usd:null}]}}));
  for(const text of ['не са актуални','ledger unavailable','не разрешава нови входове',
    'стара цена','не е прибрано','$-3.50','0 затворени']) assert.ok(html.includes(text),text);
});

test('realized losses and expired timeboxes stay visible; missing ledger stats are unknown',()=>{
  const html=renderToStaticMarkup(createElement(PaperFastScalpPanel,{connected:true,
    data:{...snapshot,balance:992,trades:1,realized_pnl_usd:-8,win_rate_pct:0,
      history:[{trade_no:1,symbol:'TEST',address:'mint',pairAddress:'pool',opened_at:1,
        closed_at:310000,notional_usd:250,open_pnl_usd:-8,pnl_usd:-8,reason:'FAST_MAX_HOLD_5M',quote_status:'fresh',profit_floor_usd:null}]}}));
  assert.match(html,/WR 0.0%/); assert.match(html,/\$-8.00 нето/); assert.match(html,/Максимум 5 минути/);
  const error=renderToStaticMarkup(createElement(PaperFastScalpPanel,{connected:true,
    data:{version:snapshot.version,status:'degraded',config:snapshot.config,error:'unreadable'}}));
  assert.match(error,/Баланс —/); assert.match(error,/— затворени/);
});

test('new independent discovery and last scan counts cannot be mistaken for actual fills',()=>{
  const html=renderToStaticMarkup(createElement(PaperFastScalpPanel,{connected:true,
    data:{...snapshot,version:'PAPER_FAST_SCALP_V2_COST_FIRST_250_5M',
      config:{...snapshot.config,market_screen:'FUNDED_V9_MOMENTUM_OR_COST_FIRST_PHYSICAL_V2'},
      diagnostics:{...snapshot.diagnostics!,at:Date.now()-5000,market_candidates:8,signal_candidates:2}}}));
  for(const text of ['Собствен подбор','не чака 5-минутния Momentum','8 кандидата','2 сигнала',
    'не изпълнени сделки','0 входа','общо 0 затворени']) assert.ok(html.includes(text),text);
});
