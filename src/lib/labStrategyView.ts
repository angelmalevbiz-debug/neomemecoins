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
  // LAB_ACTIVE_V6: every Lab book reports cost-infeasible candidates instead of hiding them.
  modeled_roundtrip_cost_limit: 'Няма вход: моделираните разходи надхвърлят лимита',
  // COST_FIRST_ESTABLISHED_V1: every universe candidate refused only for size or re-entry cooldown.
  cost_first_size_below_minimum: 'Размерът по ликвидност е под минималния вход',
  reentry_cooldown: 'Пауза преди повторен вход в същия токен',
  // DEFENSIVE_ENTRY_LAYER_V1: same meaning as engine_entry_policy.LABELS (structural rug guard,
  // pool loss memory of this book, heat veto); every matched candidate was removed by the layer.
  defensive_entry: 'Защитният слой блокира всички кандидати',
  rug_input_unknown: 'Непълни данни за структурна rug проверка',
  rug_lp_pullable: 'Ликвидност >= капитализация: създателят може да изтегли LP',
  rug_young_pool: 'Pool-ът е по-млад от 12 часа',
  rug_fake_market_cap: 'Надута капитализация (>= $20M) при ликвидност под 2%',
  rug_ticker_reuse: 'Тикерът вече е използван от друг mint',
  rug_ticker_registry_warming: 'Регистърът на тикерите наблюдава пазара под 24 ч.: токен под 14 дни чака',
  pool_loss_cooldown: 'Пауза 6 ч. след 2 поредни загуби в същия pool',
  heat_history_warming: 'Историята на pool-а още не покрива прозорците на проверката (5/15/60 мин. след рестарт)',
  heat_input_unknown: 'Липсва текуща цена за проверка на прегряване',
  heat_return_5m_surge: 'Ръст >= 3% за 5 минути (гонене на движение)',
  heat_buy_share_5m: 'Дял на покупките >= 70% за 5 минути',
  heat_volume_acceleration: 'Ускорение на обема >= 1.3x спрямо часовото темпо',
  heat_extended_move: 'Разтегнато движение (6ч >= +200% или 24ч >= +150%)',
  heat_paid_profile_high_fee: 'Платен профил през последния час при такса >= 100 bps',
  heat_crash_in_progress: 'Срив в ход (<= 75% от 15-мин. връх или -20% за 5 мин.)',
  heat_turnover_5m: 'Оборот за 5 мин. / ликвидност >= 0.095',
  defensive_entry_error: 'Грешка в защитната проверка: входът е блокиран',
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
  return `${allCheckedTooExpensive ? 'Всички проверени кандидати надхвърлят лимита в модела' : 'Моделен минимум за разходите'}: ${minimum.toFixed(2)}% · лимит ${maximum.toFixed(2)}%. Това включва оценената DEX такса при вход и изход и буфер за slippage/забавяне; не включва impact, мрежа и rent. Нужна е изпълнима котировка.`;
}

export type LabEntryViewDiagnostics = {
  blocked_reason?: string;
  signal_candidates?: number;
  matched_candidates?: number;
  cost_rejected?: number;
  cost_infeasible_candidates?: number;
  affordable_candidates?: number;
  max_entry_roundtrip_cost_pct?: number;
  stop_loss_net_pct?: number;
  // LAB_ACTIVE_V6: fee-and-buffer planning floor published for every book.
  cost_feasibility?: CostFeasibility;
  promoted_cost_feasibility?: CostFeasibility;
};

type EntryViewBook = ViewBook & { entry_diagnostics?: LabEntryViewDiagnostics };
type EntryView = { status: string; detail: string | null; costLimited: boolean };

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

function costCapView(diagnostics: LabEntryViewDiagnostics): EntryView | null {
  // LAB_ACTIVE_V6: every checked candidate of this book exceeded the admission
  // cap under the full modeled round trip (fees, impact, buffers, network).
  if (diagnostics.blocked_reason !== 'modeled_roundtrip_cost_limit') return null;
  const rejected = diagnostics.cost_infeasible_candidates ?? diagnostics.cost_rejected;
  const cap = diagnostics.max_entry_roundtrip_cost_pct;
  if (!finite(rejected) || rejected <= 0 || !finite(cap) || cap < 0) return null;
  const stop = finite(diagnostics.stop_loss_net_pct) ? diagnostics.stop_loss_net_pct : cap * 2;
  const signals = finite(diagnostics.signal_candidates) ? diagnostics.signal_candidates : rejected;
  return {
    status: `Няма вход: ${rejected} от ${signals} кандидата над лимита на разходите ${cap.toFixed(2)}%`,
    detail: `Моделираният round-trip (DEX такса вход и изход, impact, slippage/забавяне, мрежа) при всеки проверен размер надхвърля лимита ${cap.toFixed(2)}% = 0.5 × нетен стоп ${stop.toFixed(2)}%. Кандидатите са отчетени като неизпълними по разходи, не са скрити; лимитът не се сваля, за да се отворят сделки. Не е изпълнима котировка.`,
    costLimited: true,
  };
}

function allMatchedCostsExceedLimit(diagnostics: LabEntryViewDiagnostics, data: CostFeasibility | undefined) {
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
    'promoted_buy_pressure_unconfirmed', 'promoted_cost_headroom_insufficient', 'modeled_roundtrip_cost_limit'].includes(reason);
  if (reason && reasons[reason] && !genericWait) return { status: reasons[reason], detail: null, costLimited: false };
  if (diagnostics.signal_candidates === 0) return { status: reasons.no_market_signal, detail: null, costLimited: false };
  const capped = costCapView(diagnostics);
  if (capped) return capped;
  // The funded-only planning summary stays funded-only; the V6 summary applies to every book.
  const feasibility = diagnostics.cost_feasibility
    ?? (isFundedStrategy(book) ? diagnostics.promoted_cost_feasibility : undefined);
  if (genericWait && allMatchedCostsExceedLimit(diagnostics, feasibility)) {
    const cost = feasibility!;
    const stopBudget = cost.maximum_roundtrip_cost_pct! * 2;
    const stopHeadroom = stopBudget - cost.minimum_model_roundtrip_cost_pct!;
    return {
      status: `Няма вход в модела: разходи поне ${cost.minimum_model_roundtrip_cost_pct!.toFixed(2)}% > лимит ${cost.maximum_roundtrip_cost_pct!.toFixed(2)}%`,
      detail: `За всички текущи сигнали: това е прогнозната DEX такса за вход и изход плюс буфери, не само комисиона. При нетен стоп ${stopBudget.toFixed(2)}% този минимум оставя ${stopHeadroom.toFixed(2)} п.п. запас; impact, мрежа и rent са извън оценката. Не е изпълнима котировка.`,
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
