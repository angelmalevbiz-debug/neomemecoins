import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fundedActiveSummary, labEntryStatus } from '../src/lib/labStrategyView';

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
