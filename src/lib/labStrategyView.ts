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
  unknown_candidates?: number;
  minimum_model_roundtrip_cost_pct?: number | null;
  maximum_roundtrip_cost_pct?: number;
  best_candidates?: { model_cost_feasible?: boolean | null }[];
};

export function planningCostStatus(data?: CostFeasibility) {
  const minimum = data?.minimum_model_roundtrip_cost_pct;
  const maximum = data?.maximum_roundtrip_cost_pct;
  if (!data?.checked_market_candidates || minimum == null || maximum == null
    || !Number.isFinite(minimum) || !Number.isFinite(maximum)) return null;
  const allCheckedTooExpensive = Number.isInteger(data.checked_market_candidates)
    && data.fixed_cost_infeasible_candidates === data.checked_market_candidates
    && !(data.unknown_candidates && data.unknown_candidates > 0) && minimum > maximum;
  return `${allCheckedTooExpensive ? 'Всички проверени кандидати надхвърлят лимита в модела' : 'Моделен минимум за разходите'}: ${minimum.toFixed(2)}% · лимит ${maximum.toFixed(2)}%. Без impact, мрежа и rent; нужна е изпълнима котировка.`;
}

export type LabEntryViewDiagnostics = {
  blocked_reason?: string;
  signal_candidates?: number;
  affordable_candidates?: number;
  promoted_cost_feasibility?: CostFeasibility;
};

type EntryViewBook = ViewBook & { entry_diagnostics?: LabEntryViewDiagnostics };
type EntryView = { status: string; detail: string | null; costLimited: boolean };

function allMatchedCostsExceedLimit(diagnostics: LabEntryViewDiagnostics) {
  const data = diagnostics.promoted_cost_feasibility;
  const checked = data?.checked_market_candidates;
  const minimum = data?.minimum_model_roundtrip_cost_pct;
  const maximum = data?.maximum_roundtrip_cost_pct;
  return typeof checked === 'number' && Number.isInteger(checked) && checked > 0
    && diagnostics.signal_candidates === checked
    && data?.fixed_cost_infeasible_candidates === checked
    && (data.unknown_candidates == null || data.unknown_candidates === 0)
    && (diagnostics.affordable_candidates == null || diagnostics.affordable_candidates === 0)
    && (data.best_candidates == null || Array.isArray(data.best_candidates)
      && data.best_candidates.every(candidate => candidate?.model_cost_feasible === false))
    && typeof minimum === 'number' && Number.isFinite(minimum)
    && typeof maximum === 'number' && Number.isFinite(maximum) && maximum >= 0
    && minimum > maximum;
}

export function labEntryView(book: EntryViewBook, backendAvailable = true): EntryView {
  if (isArchivedStrategy(book)) return { status: 'Новите входове са спрени', detail: null, costLimited: false };
  if (!backendAvailable) return {
    status: 'Изчаква актуални данни от backend',
    detail: 'Последната диагностика не потвърждава текущите възможности за вход.', costLimited: false,
  };
  const diagnostics = book.entry_diagnostics;
  if (!diagnostics) return { status: 'Чака данни за входа', detail: null, costLimited: false };
  const reason = diagnostics.blocked_reason;
  const genericWait = !reason || ['promoted_verified_flow_unavailable', 'promoted_verified_flow_stale',
    'promoted_buy_pressure_unconfirmed', 'promoted_cost_headroom_insufficient'].includes(reason);
  if (reason && reasons[reason] && !genericWait) return { status: reasons[reason], detail: null, costLimited: false };
  if (diagnostics.signal_candidates === 0) return { status: reasons.no_market_signal, detail: null, costLimited: false };
  if (isFundedStrategy(book) && genericWait && allMatchedCostsExceedLimit(diagnostics)) {
    const cost = diagnostics.promoted_cost_feasibility!;
    return {
      status: `Няма вход в модела: разходи поне ${cost.minimum_model_roundtrip_cost_pct!.toFixed(2)}% > лимит ${cost.maximum_roundtrip_cost_pct!.toFixed(2)}%`,
      detail: 'За всички текущи сигнали. Оценката изключва impact, мрежа и rent; не е изпълнена котировка.',
      costLimited: true,
    };
  }
  return {
    status: reason && reasons[reason] ? reasons[reason]
      : reason ? `Входът изчаква: ${reason}` : 'Проверява сигнал, цена и разходи',
    detail: null, costLimited: false,
  };
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

export function labEntryStatus(book: EntryViewBook, backendAvailable = true) {
  return labEntryView(book, backendAvailable).status;
}
