import { moneyOrUnavailable } from '../lib/paperDashboardState';

type FastPosition = {
  trade_no: number; symbol: string; address: string; pairAddress: string;
  opened_at: number; notional_usd: number; open_pnl_usd: number;
  quote_status: string; profit_floor_usd: number | null;
  pending_exit?: { reason: string } | null;
};
type FastTrade = FastPosition & { closed_at: number; pnl_usd: number; reason: string };
export type PaperFastScalpSnapshot = {
  version: string; status: string; enabled?: boolean; error?: string | null;
  updated_at?: number; starting_balance?: number; balance?: number; available_cash_usd?: number;
  realized_pnl_usd?: number; unrealized_pnl_usd?: number; trades?: number;
  win_rate_pct?: number | null; entries_last_60m?: number;
  config: { notional_usd: number; take_profit_net_usd: number; stop_loss_net_usd: number;
    profit_arm_net_usd: number; profit_giveback_usd: number; max_hold_seconds: number;
    market_screen?: string };
  positions?: FastPosition[]; history?: FastTrade[];
  diagnostics?: { at?: number; blocked_reason?: string; market_candidates: number; signal_candidates: number;
    rejections: Record<string, number> };
};

const reasons: Record<string, string> = {
  fast_no_market_signal: 'Няма подходящ пазарен сигнал',
  fast_market_outside_universe: 'Пулът вече е извън допустимия пазарен подбор',
  fast_move_does_not_cover_cost: 'Краткото движение не покрива разходите и буфера',
  fast_pending_signal_expired: 'Сигналът е изтекъл преди нова прясна цена',
  fast_waiting_next_observation: 'Потвърден кандидат — чака следваща прясна цена',
  fast_cash_unavailable: 'Няма свободен капитал за пълен вход и такси',
  fast_pool_already_open: 'Този токен вече е отворен в скалпъра',
  fast_pool_cooldown: 'Кратка пауза след последния изход от този токен',
  fast_roundtrip_cost: 'Разходите надвишават 1.50%',
  fast_independent_price_unavailable: 'Чака независима прясна цена за точния pool',
  fast_market_stale: 'Пазарната цена е стара',
  fast_entries_disabled_exits_continue: 'Входовете са изключени; изходите продължават',
  promoted_verified_flow_unavailable: 'Чака потвърден поток за точния pool',
  promoted_verified_flow_stale: 'Потвърденият поток е изтекъл',
  promoted_buy_pressure_unconfirmed: 'Покупателният натиск не е потвърден',
  promoted_safety_unavailable: 'Чака завършена проверка за риск',
  heat_history_warming: 'Събира реална ценова история за защитите',
  heat_return_5m_surge: 'Отказан прекалено нагорещен pool',
  heat_buy_share_5m: 'Отказана прекалено едностранна активност',
  rug_ticker_reuse: 'Тикерът се използва и от друг токен',
  FAST_TARGET_NET: 'Нетна цел', FAST_STOP_NET: 'Нетен стоп',
  FAST_PROFIT_LOCK: 'Защита на нетния връх', FAST_MAX_HOLD_5M: 'Максимум 5 минути',
};

export default function PaperFastScalpPanel({ data, connected }: {
  data?: PaperFastScalpSnapshot; connected: boolean;
}) {
  if (!data) return null;
  const config = data.config;
  const reason = data.diagnostics?.blocked_reason;
  const stale = !connected || (data.updated_at != null && Date.now() - data.updated_at > 30_000);
  return <section data-testid="paper-fast-scalp" className="border-b border-cyan-300/15 bg-cyan-300/[0.03] px-4 py-4 text-[10px] leading-5 text-slate-300">
    <h3 className="font-black text-cyan-200">FAST SCALP · отделна бърза PAPER стратегия</h3>
    <p className="mt-1 text-amber-100">Тестов капитал {moneyOrUnavailable(data.starting_balance)}, не печалба. Собствен баланс и история; бавните стратегии са запазени. Няма истински пари, гарантиран профит или принудителни входове.</p>
    <p className="mt-1">Вход {moneyOrUnavailable(config.notional_usd)} · цел +{moneyOrUnavailable(config.take_profit_net_usd)} нето · стоп −{moneyOrUnavailable(config.stop_loss_net_usd)} нето · защита от +{moneyOrUnavailable(config.profit_arm_net_usd)}, отстъпление {moneyOrUnavailable(config.profit_giveback_usd)} · максимум {config.max_hold_seconds / 60} мин от входа.</p>
    <p className="mt-1 text-slate-400">Движение за 60 сек над разходите + 0.25 процентни пункта и потвърдени 3 сделки / 2 портфейла. Такси, impact, slippage и забавяне са включени. Изпълнение на следваща прясна цена след ≥2 сек; не на удобния праг. При липса на цена изходът се бави и стопът може да бъде надхвърлен.</p>
    {config.market_screen === 'FUNDED_V9_MOMENTUM_OR_COST_FIRST_PHYSICAL_V2' && <p className="mt-1 text-slate-400">Собствен подбор: Momentum или ликвидни пулове с ниски разходи. Новият клон не чака 5-минутния Momentum филтър; потвърденият поток и всички проверки за риск остават задължителни.</p>}
    {stale && <p className="mt-2 text-amber-200">Няма актуална връзка със скалпъра — показаните данни не са актуални.</p>}
    {data.error && <p role="status" className="mt-2 text-red-300">Скалпърът има грешка: {data.error}. Това не разрешава нови входове.</p>}
    {!data.enabled && !data.error && <p className="mt-2 text-amber-200">Новите входове са изключени. Съществуващите позиции продължават да се управляват.</p>}
    <p className="mt-2">Баланс {moneyOrUnavailable(data.balance)} · свободни {moneyOrUnavailable(data.available_cash_usd)} · реализиран нетен PnL {moneyOrUnavailable(data.realized_pnl_usd, true)} · отворен {moneyOrUnavailable(data.unrealized_pnl_usd, true)} (не е прибрано)</p>
    <p>Последен час: {data.entries_last_60m ?? '—'} входа · общо {data.trades ?? '—'} затворени · {data.win_rate_pct == null ? 'WR — няма оценка' : `WR ${data.win_rate_pct.toFixed(1)}%`} · позиции {data.positions?.length ?? '—'}</p>
    {data.diagnostics && <p>Последен подбор: {data.diagnostics.market_candidates} кандидата · {data.diagnostics.signal_candidates} сигнала (не изпълнени сделки){data.diagnostics.at != null && <> · преди {Math.max(0, Math.floor((Date.now()-data.diagnostics.at)/1000))} сек</>}</p>}
    {reason && <p className="mt-1 text-slate-400">Сега: {reasons[reason] ?? reason}</p>}
    {data.positions?.map(position => <div key={position.trade_no} className="mt-2 rounded-lg border border-cyan-200/15 px-3 py-2">
      ${position.symbol} · {moneyOrUnavailable(position.notional_usd)} · нетна оценка {moneyOrUnavailable(position.open_pnl_usd, true)} · {position.quote_status === 'fresh' ? 'прясна цена' : 'стара цена — чака обновяване'}
      {position.profit_floor_usd != null && <> · защитен праг {moneyOrUnavailable(position.profit_floor_usd, true)} (не гарантира изпълнение)</>}
      {position.pending_exit && <> · чака изход: {reasons[position.pending_exit.reason] ?? position.pending_exit.reason}</>}
    </div>)}
    {!!data.history?.length && <details className="mt-2"><summary>Последни затворени сделки на скалпъра</summary>
      {data.history.slice().reverse().map(trade => <p key={trade.trade_no}>#{trade.trade_no} · ${trade.symbol} · {moneyOrUnavailable(trade.pnl_usd, true)} нето · {reasons[trade.reason] ?? trade.reason}</p>)}
    </details>}
    <p className="mt-2 text-amber-200">Проспективен PAPER експеримент. Честотата и печалбата не са доказани; капиталът не се допълва автоматично. Резултатът не се смесва с четирите стратегии или със старите HF тестове.</p>
  </section>;
}
