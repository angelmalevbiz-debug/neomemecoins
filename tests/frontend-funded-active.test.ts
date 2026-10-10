import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fundedActiveSummary, profitProtectionSummary, labEntryStatus } from '../src/lib/labStrategyView';

test('owner requested 250 policy shows uncapped soft limits, not zero risk or infinite cash', () => {
  const text=fundedActiveSummary({version:'V6',quality_mode:true,soft_limits_removed:true,
    fixed_notional_usd:250,trades:0,wins:0,win_rate:null,net_pnl_usd:0,
    orders_last_60m:0,closed_last_60m:0,open_positions:0,max_positions:null,
    target_orders_per_hour:null,exposure_usd:0,daily_loss_limit_usd:null,effective_position_capacity:3});
  assert.match(text!,/вход \$250.00/);
  assert.match(text!,/позиции 0\/без таван/);
  assert.match(text!,/капиталов капацитет 3/);
  assert.match(text!,/без дневен, часов и позиционен таван/);
  assert.match(text!,/без заем/);
  assert.doesNotMatch(text!,/null|undefined|дневен лимит загуба|Следващ UTC ден/);
  assert.match(labEntryStatus({id:'MOMENTUM',balance:200,starting_balance:1000,
    entry_diagnostics:{blocked_reason:'funded_active_cash_unavailable'}}),/свободен PAPER капитал/);
  const flow=labEntryStatus({id:'MOMENTUM',balance:900,starting_balance:1000,
    entry_diagnostics:{blocked_reason:'quality_buy_flow_too_small'}});
  assert.match(flow,/допълнителните прагове/);
  assert.doesNotMatch(flow,/\$100/);
});

test('decoupled flow repair displays restored floors separately from the full order size', () => {
  const text=fundedActiveSummary({version:'V8',quality_mode:true,flow_size_decoupled:true,
    quality_flow_requirements:{minimum_confirmed_30s_buy_usd:300,minimum_confirmed_30s_net_buy_usd:100},
    fixed_notional_usd:250,trades:0,wins:0,win_rate:null,net_pnl_usd:0,
    orders_last_60m:0,closed_last_60m:0,open_positions:0,max_positions:null,
    target_orders_per_hour:null,exposure_usd:0,daily_loss_limit_usd:null})!;
  assert.match(text,/размерът не затяга сигнала/);
  assert.match(text,/покупки ≥\$300\.00, нетни покупки ≥\$100\.00/);
  assert.match(text,/6 сделки \/ 4 портфейла/);
  assert.match(text,/разходите се проверяват при \$250/);
  assert.match(text,/0 входа \/ 0 изхода/);
  assert.match(text,/не доказват печалба/);
  assert.doesNotMatch(text,/undefined|750/);
});

test('ticker warning experiment is explicitly labelled and does not claim profit or dropped safety', () => {
  const stats = {version:'V7',quality_mode:true,ticker_reuse_log_only:true,trades:0,wins:0,
    win_rate:null,net_pnl_usd:0,orders_last_60m:0,closed_last_60m:0,open_positions:0,
    max_positions:null,target_orders_per_hour:null,exposure_usd:0,daily_loss_limit_usd:null};
  const text = fundedActiveSummary(stats)!;
  assert.match(text,/повтарящ се тикер е предупреждение, не забрана/);
  assert.match(text,/останалите защити и разходи остават/);
  assert.match(text,/резултатите се отчитат отделно/);
  assert.match(text,/печалба не е доказана/);
  assert.doesNotMatch(fundedActiveSummary({...stats,ticker_reuse_log_only:false})!,/PAPER експеримент/);
});

test('scalp profile displays independent duration and UTC pause without promising an entry', () => {
  const result=fundedActiveSummary({version:'FUNDED_ACTIVE_PAPER_V5_ADAPTIVE_100',quality_mode:true,
    trades:0,wins:0,win_rate:null,net_pnl_usd:0,orders_last_60m:0,closed_last_60m:0,
    open_positions:0,max_positions:4,target_orders_per_hour:50,exposure_usd:0,daily_loss_limit_usd:50,
    blocked_reason:'funded_active_daily_loss_limit',daily_net_usd:-60,
    daily_window_basis:'UTC_CALENDAR_DAY_INCLUDING_OPEN_MARKS',daily_window_ends_at:1791590400000,
    exit_policy:{version:'PAPER_MOMENTUM_SCALP_EXIT_V1_15',holding_profile:'SCALP',max_hold_minutes:15,
      take_profit_net_usd:6,stop_loss_net_usd:5,profit_protection_arm_net_usd:2,
      minimum_profit_giveback_usd:.75,profit_giveback_fraction:.2}});
  assert.match(result!,/SCALP · максимум 15 мин/);
  assert.match(result!,/Следващ UTC ден:/);
  assert.match(result!,/отворените загуби остават в риска/);
  assert.match(result!,/не гарантира нов вход/);
});

test('legacy snapshots and invalid risk clocks never fabricate a rollover time', () => {
  for (const stamp of [undefined,NaN,Infinity,-1,9e15]) {
    const result=fundedActiveSummary({version:'V5',quality_mode:true,trades:0,wins:0,win_rate:null,
      net_pnl_usd:0,orders_last_60m:0,closed_last_60m:0,open_positions:0,max_positions:4,
      target_orders_per_hour:50,exposure_usd:0,daily_loss_limit_usd:50,
      blocked_reason:'funded_active_daily_loss_limit',daily_window_ends_at:stamp,
      daily_window_basis:'UTC_CALENDAR_DAY_INCLUDING_OPEN_MARKS'});
    assert.doesNotMatch(result!,/Следващ UTC ден|Invalid Date/);
  }
});

test('active PAPER summary separates current policy, entries, exits and no results', () => {
  const result = fundedActiveSummary({ version: 'FUNDED_ACTIVE_PAPER_V1', trades: 0, wins: 0,
    win_rate: null, net_pnl_usd: 0, orders_last_60m: 2, closed_last_60m: 0,
    open_positions: 2, max_positions: 4, target_orders_per_hour: 50,
    exposure_usd: 50, daily_loss_limit_usd: 12.5 });
  assert.match(result!, /няма затворени сделки/);
  assert.match(result!, /2 входа \/ 0 изхода/);
  assert.match(result!, /след разходи/);
  assert.match(result!, /без гаранция/);
  assert.equal(fundedActiveSummary(), null);
});

test('active capacity and risk stops have honest readable reasons', () => {
  for (const [reason, text] of [
    ['funded_active_daily_loss_limit', 'Дневният лимит'],
    ['funded_active_slots_full', 'позиции са заети'],
    ['funded_active_hourly_order_limit', '50 нови входа'],
    ['funded_active_exposure_limit', 'свободен капитал'],
  ]) {
    assert.ok(labEntryStatus({id:'EARLY', starting_balance:250, balance:250,
      entry_diagnostics:{blocked_reason:reason}}).includes(text));
  }
});

test('V2 exposes the real fixed size without a small fallback', () => {
  const text = fundedActiveSummary({version:'FUNDED_ACTIVE_PAPER_V2_FIXED_100', fixed_notional_usd:100,
    trades:0,wins:0,win_rate:null,net_pnl_usd:0,orders_last_60m:0,closed_last_60m:0,
    open_positions:0,max_positions:4,target_orders_per_hour:50,exposure_usd:0,daily_loss_limit_usd:12.5});
  assert.match(text!, /вход \$100\.00, без намаляване/);
});

test('contributed PAPER capital is labelled separately from trading profit', () => {
  const text=fundedActiveSummary({version:'FUNDED_ACTIVE_PAPER_V3_MOMENTUM_PULSE_100',
    fixed_notional_usd:100,funded_capital_usd:1000,effective_position_capacity:4,
    trades:2,wins:0,win_rate:0,net_pnl_usd:-2.30,orders_last_60m:2,closed_last_60m:2,
    open_positions:0,max_positions:4,target_orders_per_hour:50,exposure_usd:0,daily_loss_limit_usd:50});
  assert.match(text!,/внесен PAPER капитал \$1000\.00 \(не е печалба\)/);
  assert.match(text!,/капиталов капацитет 4 позиции/);
  assert.match(text!,/нетен PnL −\$2\.30/);
});

test('capacity fills are not presented as successful strategic signals or protected daily risk', () => {
  const text=fundedActiveSummary({version:'PAPER_CAPACITY_TEST_V1_FIXED_100',capacity_test:true,
    risk_limits_shadow_only:true,fixed_notional_usd:100,funded_capital_usd:1000,effective_position_capacity:4,
    trades:0,wins:0,win_rate:null,net_pnl_usd:0,orders_last_60m:4,closed_last_60m:0,
    open_positions:4,max_positions:4,target_orders_per_hour:50,exposure_usd:400,daily_loss_limit_usd:50});
  assert.match(text!,/ТЕСТ ЗАПЪЛВАНЕ — не е вход по стратегически сигнал/);
  assert.match(text!,/не спират теста/);
  assert.match(text!,/PAPER капиталът може да се загуби/);
  assert.doesNotMatch(text!,/дневен лимит загуба/);
});

test('capacity test exit summary shows net dollar target and stop without claiming guaranteed fills', () => {
  const text=fundedActiveSummary({version:'PAPER_CAPACITY_TEST_V1_FIXED_100',capacity_test:true,
    risk_limits_shadow_only:true,fixed_notional_usd:100,
    exit_policy:{version:'PAPER_CAPACITY_EXIT_V2_NET30_STOP10',take_profit_net_usd:30,stop_loss_net_usd:10},
    trades:2,wins:0,win_rate:0,net_pnl_usd:-3,orders_last_60m:6,closed_last_60m:2,
    open_positions:4,max_positions:4,target_orders_per_hour:50,exposure_usd:400,daily_loss_limit_usd:50});
  assert.match(text!,/цел \+\$30\.00 нето \/ стоп −\$10\.00 нето/);
  assert.match(text!,/без кратък таймер и trailing/);
  assert.match(text!,/праговете не гарантират цена на изхода/);
  assert.match(text!,/не доказва печалба/);
});

test('quality separates old losses and open marks from unproven current-policy outcomes', () => {
  const text=fundedActiveSummary({version:'FUNDED_ACTIVE_PAPER_V4_QUALITY_100',quality_mode:true,
    entry_cost_limit_pct:1.5,legacy_closed_trades:23,legacy_net_pnl_usd:-70.88,
    legacy_open_positions:4,open_net_pnl_usd:2,
    exit_policy:{version:'PAPER_QUALITY_EXIT_V1_NET30_STOP5',take_profit_net_usd:30,stop_loss_net_usd:5},
    trades:0,wins:0,win_rate:null,net_pnl_usd:0,orders_last_60m:0,closed_last_60m:0,
    open_positions:4,max_positions:4,target_orders_per_hour:50,exposure_usd:400,daily_loss_limit_usd:50});
  assert.match(text!,/PAPER КАЧЕСТВО/);
  assert.match(text!,/без принудително запълване/);
  assert.match(text!,/нови позиции: цел \+\$30\.00 нето \/ стоп −\$5\.00 нето/);
  assert.match(text!,/23 затворени, нето −\$70\.88/);
  assert.match(text!,/отворен PnL \$2\.00 \(не е прибрана печалба\)/);
  assert.match(text!,/няма затворени сделки/);
  assert.match(text!,/дневен лимит загуба/);
  assert.doesNotMatch(text!,/не спират теста/);
});

test('quality admission rejects small flow, correlated lots and repeated losing pools with readable reasons', () => {
  for (const [reason,text] of [['quality_buy_flow_too_small','прагове'],
    ['quality_correlated_position','друга PAPER сметка'],['quality_pool_loss_pause','30 минути']]) {
    assert.ok(labEntryStatus({id:'EARLY',starting_balance:1000,balance:950,
      entry_diagnostics:{blocked_reason:reason}}).includes(text));
  }
});

test('adaptive summary allows early protection and shows the real daily entry pause', () => {
  const text=fundedActiveSummary({version:'FUNDED_ACTIVE_PAPER_V5_ADAPTIVE_100',quality_mode:true,
    exit_policy:{version:'PAPER_ADAPTIVE_EXIT_V1_NET30_STOP5_LOCK80',take_profit_net_usd:30,
      stop_loss_net_usd:5,profit_protection_arm_net_usd:4,profit_giveback_fraction:.2,minimum_profit_giveback_usd:1},
    trades:0,wins:0,win_rate:null,net_pnl_usd:0,orders_last_60m:0,closed_last_60m:0,
    open_positions:4,max_positions:4,target_orders_per_hour:50,exposure_usd:400,daily_loss_limit_usd:50,
    daily_net_usd:-60,blocked_reason:'funded_active_daily_loss_limit',adaptive_open_positions:4});
  assert.match(text!,/защита от \+\$4.00 нето/);
  assert.match(text!,/20% от нетния връх/);
  assert.match(text!,/може да затвори преди \+\$30/);
  assert.match(text!,/Дневният лимит/);
  assert.match(text!,/дневен нетен резултат −\$60.00/);
  assert.match(text!,/с адаптивен изход 4 отворени/);
  assert.doesNotMatch(text!,/запазени изходи|без кратък таймер и trailing/);
});

test('per-lot protection distinguishes waiting, unarmed, armed and stale without guaranteed profits', () => {
  const state={version:'adaptive',activated_at:1,last_mark_at:null,peak_net_usd:null,floor_net_usd:null,armed:false};
  assert.equal(profitProtectionSummary(),null);
  assert.match(profitProtectionSummary(state,'fresh')!,/първа прясна цена/);
  assert.match(profitProtectionSummary({...state,last_mark_at:2},'fresh')!,/още не е включена/);
  const armed={...state,last_mark_at:2,peak_net_usd:15,floor_net_usd:12,armed:true};
  assert.match(profitProtectionSummary(armed,'fresh')!,/праг за изход \+\$12.00/);
  assert.match(profitProtectionSummary(armed,'fresh')!,/не е гарантирана печалба/);
  assert.match(profitProtectionSummary(armed,'stale')!,/Котировката не е прясна/);
});

test('holding profile summary reports original clock, losing timeouts and actual target', () => {
  const text=fundedActiveSummary({version:'FUNDED_ACTIVE_PAPER_V5_ADAPTIVE_100',quality_mode:true,
    exit_policy:{version:'PAPER_HORIZON_EXIT_V1_60_240_720',holding_profile:'QUICK',max_hold_minutes:60,
      take_profit_net_usd:6,stop_loss_net_usd:5,profit_protection_arm_net_usd:2,
      profit_giveback_fraction:.2,minimum_profit_giveback_usd:.75},
    trades:0,wins:0,win_rate:null,net_pnl_usd:0,orders_last_60m:0,closed_last_60m:0,
    open_positions:1,max_positions:4,target_orders_per_hour:50,exposure_usd:100,daily_loss_limit_usd:50});
  for(const part of ['QUICK','максимум 60 мин от оригиналния вход','затваря и на загуба',
    'рестартът не удължава срока','цел +$6.00','защита от +$2.00','може да затвори преди +$6.00']) assert.ok(text!.includes(part));
  assert.doesNotMatch(text!,/без фиксиран времеви изход|преди \+\$30/);
});

test('quick per-lot unarmed protection does not claim a four dollar threshold', () => {
  const text=profitProtectionSummary({version:'horizon',activated_at:1,last_mark_at:2,
    peak_net_usd:1,floor_net_usd:null,armed:false,arm_net_usd:2},'fresh');
  assert.match(text!,/\+\$2.00/);assert.doesNotMatch(text!,/\+\$4.00/);
});
