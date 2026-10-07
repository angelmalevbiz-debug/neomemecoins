export type StrategyLifecycle = {
  status?: string;
  entry_enabled?: boolean;
  position_management_enabled?: boolean;
  evidence?: { closed_trades?: number; wins?: number; net_pnl_usd?: number; note?: string };
};

type ViewBook = {
  id: string; starting_balance: number; balance: number;
  portfolio_group?: string;
  runtime_compatibility?: { status?: string };
  strategy_lifecycle?: StrategyLifecycle;
};
type ViewStats = { return_pct?: number };

export const isArchivedStrategy = (book: ViewBook) =>
  book.strategy_lifecycle?.status === 'retired' && book.strategy_lifecycle.entry_enabled === false
  || book.runtime_compatibility?.status === 'preserved_inactive';

export const isFundedStrategy = (book: ViewBook) =>
  book.portfolio_group === 'PROMOTED_PAPER' || book.portfolio_group === 'PROMOTION_DRAINING';

export function partitionLabStrategies<T extends ViewBook>(books: T[], stats: Record<string, ViewStats> = {}) {
  const percent = (book: T) => {
    const reported = stats[book.id]?.return_pct;
    return reported != null && Number.isFinite(reported) ? reported
      : book.starting_balance > 0 ? (book.balance / book.starting_balance - 1) * 100 : 0;
  };
  const sorted = books.filter(book => book.id !== 'ASTRA_6_BRAIN').slice().sort((a, b) =>
    percent(b) - percent(a) || a.id.localeCompare(b.id));
  return {
    funded: sorted.filter(book => !isArchivedStrategy(book) && isFundedStrategy(book)),
    research: sorted.filter(book => !isArchivedStrategy(book) && !isFundedStrategy(book)),
    archived: sorted.filter(isArchivedStrategy),
  };
}

const reasons: Record<string, string> = {
  no_market_signal: 'Няма пазарен сигнал по правилата',
  promoted_verified_flow_unavailable: 'Чака пресен потвърден поток',
  promoted_verified_flow_stale: 'Потвърденият поток е остарял',
  promoted_buy_pressure_unconfirmed: 'Няма потвърден натиск от купувачи',
  promoted_recent_loss_cooldown: 'Пауза след скорошна загуба',
  promoted_safety_unknown: 'Чака завършена проверка за безопасност',
  promoted_safety_not_fresh_pass: 'Чака прясна проверка за безопасност',
  promoted_safety_unavailable: 'Чака прясна пълна проверка за безопасност',
  promoted_price_identity_unconfirmed: 'Цената за точния пазар не е потвърдена',
  promoted_price_identity_unverified: 'Цената за точния пазар не е потвърдена',
  network_price_unknown: 'Чака проверима оценка на мрежовите такси',
  promoted_cost_headroom_insufficient: 'Разходите оставят твърде малък резерв до стопа',
  promotion_waiting_for_existing_position_exit: 'Чака изход на стара позиция',
  insufficient_balance: 'Недостатъчен свободен баланс',
  retired_repeated_losses: 'Спряна след повтарящи се загуби',
  strategy_retired_observed_losses: 'Спряна след повтарящи се загуби',
};

export type CostFeasibility = {
  checked_market_candidates?: number;
  fixed_cost_infeasible_candidates?: number;
  minimum_model_roundtrip_cost_pct?: number | null;
  maximum_roundtrip_cost_pct?: number;
};

export function planningCostStatus(data?: CostFeasibility) {
  const minimum = data?.minimum_model_roundtrip_cost_pct;
  const maximum = data?.maximum_roundtrip_cost_pct;
  if (!data?.checked_market_candidates || minimum == null || maximum == null
    || !Number.isFinite(minimum) || !Number.isFinite(maximum)) return null;
  const allTooExpensive = data.fixed_cost_infeasible_candidates === data.checked_market_candidates;
  return `${allTooExpensive ? 'Всички кандидати надхвърлят лимита в модела' : 'Моделен минимум за разходите'}: ${minimum.toFixed(2)}% · лимит ${maximum.toFixed(2)}%. Без impact, мрежа и rent; нужна е изпълнима котировка.`;
}

const quoteFailures: Record<string, string> = {
  ENTRY_SEQUENCE_BUSY: 'Друг вход проверява котировките; изчаква ред',
  EXIT_PRIORITY_PENDING: 'Котировките за изход са с предимство',
  PREFLIGHT_PREVIEW_STALE: 'Предварителната цена за продажба е остаряла',
  PREFLIGHT_SLOT_DRIFT: 'Котировките са от твърде отдалечени блокове',
  PREFLIGHT_QUANTITY_DRIFT: 'Цената се е променила по време на проверката',
  ENTRY_POOL_MISMATCH: 'Входната котировка използва различен пазар',
  QUOTE_STALE_AFTER_DELAY: 'Котировката е остаряла преди моделирания вход',
  RATE_LIMITED: 'Доставчикът ограничава честотата на котировките',
  RATE_LIMIT_WAIT: 'Изчаква позволената честота на котировките',
  QUOTE_RESPONSE_INVALID: 'Доставчикът върна невалидна котировка',
};

export function quoteFailureStatus(code?: string) {
  return code ? quoteFailures[code] ?? `Проверка на котировката: ${code}` : 'Няма валидна изпълнима котировка';
}

export function labEntryStatus(book: ViewBook & { entry_diagnostics?: { blocked_reason?: string; signal_candidates?: number } }) {
  if (isArchivedStrategy(book)) return 'Новите входове са спрени';
  const diagnostics = book.entry_diagnostics;
  if (!diagnostics) return 'Чака данни за входа';
  const reason = diagnostics.blocked_reason;
  if (reason && reasons[reason]) return reasons[reason];
  if (diagnostics.signal_candidates === 0) return reasons.no_market_signal;
  return reason ? `Входът изчаква: ${reason}` : 'Проверява сигнал, цена и разходи';
}
