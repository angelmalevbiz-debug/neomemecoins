export type LabForwardGate = {
  all_evaluable_pass?: boolean;
  // Met only when every criterion was evaluated and passes; the Lab cannot evaluate the
  // 3-slot $1000 drawdown replay, so its best status is 'evaluable_criteria_pass_replay_pending'.
  gate_met?: boolean;
  gate_status?: string;
  automatic_promotion?: boolean;
  not_evaluated?: string[];
  criteria?: Record<string, { value?: number | null; threshold?: string; pass?: boolean | null }>;
  // The 'same period' of the control comparison: until the control could no longer enter.
  control_window?: { control_entry_end_reason?: string | null; trade_share?: number | null; time_share?: number | null };
};

export type StrategyLifecycle = {
  version?: string;
  status?: string;
  reason?: string;
  entry_enabled?: boolean;
  position_management_enabled?: boolean;
  evidence?: {
    closed_trades?: number; wins?: number; net_pnl_usd?: number; note?: string;
    // LAB_FORWARD_KILL_RULE_V1 evidence (net50 basis, frozen config only).
    min_closes?: number; mean_net50_usd?: number | null; ci95_mean_net50_usd?: (number | null)[];
    ci_method?: string | null; kill_rule_met?: boolean; pairs?: number;
    vanished_closes?: number; drained_closes?: number; zero_capital_closes?: number;
    // LAB_FORWARD_FILL_BASIS_V4: net50 re-priced at the research fills (next DexScreener refresh).
    kill_rule_met_bases?: string[];
    research_fill?: { closed_trades?: number; pending?: number; mean_net50_usd?: number | null;
      ci95_mean_net50_usd?: (number | null)[]; unobserved_leg_share?: number | null };
  };
  // LAB_FORWARD_CASH_STATE_V1: status 'cash_exhausted' when the fixed $200 entry cannot be funded.
  cash?: { exhausted?: boolean; balance_usd?: number | null; min_entry_balance_usd?: number; zero_capital_closes?: number };
  kill_rule_evaluable?: boolean;
  promotion_gate?: LabForwardGate;
  // LAB_FORWARD_CONTROL_CONTINUITY_V1: a control keeps entering while its hypothesis can
  // ('zero_capital_control' once its own balance cannot fund the fixed entry).
  capital_mode?: string;
  control_continuity?: { hypothesis?: string; hypothesis_can_enter?: boolean; kill_rule_deferred?: boolean; zero_capital_entries?: boolean };
  // LAB_FORWARD_SIGNAL_CARRY_V2: signals that waited on the price cross-check.
  signal_carry?: { pending_signals?: number; entered?: number; lost_price_pending?: number };
};

// LAB_FORWARD_TESTS_V1: pre-registered research hypotheses and their random controls.
export const LAB_FORWARD_KILL_RULE_VERSION = 'LAB_FORWARD_KILL_RULE_V1';
const forwardNotes: Record<string, string> = {
  LAB_A_SURGE_EST_GUARD: 'Форуърд тест (предварително регистрирана хипотеза): свеж скок на покупките за 5 мин. (≥ 30 и ≥ 3× часовото темпо) в PumpSwap/SOL pool с такса ≤ 95 bps и ликвидност ≥ $50k; изход −5/+10 нето, 60 мин., $200.',
  RND_LAB_A: 'Случайна контрола на Lab A: детерминиран жребий p = 0.0005 на наблюдение в същия универсум, същите изходи.',
  LAB_B_DIP_MKTDIP_GUARD: 'Форуърд тест (предварително регистрирана хипотеза): спад ≥ 10% за 15 мин., докато медианата на пазара за 15 мин. е под −0.2%; ликвидност ≥ $50k; изход −15/+20 нето, 60 мин., $200.',
  RND_LAB_B: 'Случайна контрола на Lab B: детерминиран жребий p = 0.0007 на наблюдение, без филтър за спад и пазар, същите изходи.',
};

export const isForwardTestBook = (book: { id: string; strategy_lifecycle?: StrategyLifecycle }) =>
  book.id in forwardNotes || book.strategy_lifecycle?.version === LAB_FORWARD_KILL_RULE_VERSION;

export function labForwardNote(id: string) {
  return forwardNotes[id] ?? null;
}

// LAB_FORWARD_CASH_STATE_V1: the book cannot fund its fixed entry and holds nothing.
export const isForwardCashExhausted = (book: { id: string; strategy_lifecycle?: StrategyLifecycle }) =>
  isForwardTestBook(book) && book.strategy_lifecycle?.status === 'cash_exhausted';

// LAB_FORWARD_CONTROL_CONTINUITY_V1: a control out of cash that keeps measuring for its hypothesis.
export const ZERO_CAPITAL_CONTROL = 'zero_capital_control';
export const isForwardZeroCapital = (book: { id: string; strategy_lifecycle?: StrategyLifecycle }) =>
  isForwardTestBook(book) && book.strategy_lifecycle?.status === 'active'
  && book.strategy_lifecycle?.capital_mode === ZERO_CAPITAL_CONTROL;

export function forwardCapitalNote(mode?: string) {
  return mode === ZERO_CAPITAL_CONTROL ? 'нулев капитал: не променя баланса' : null;
}

// LAB_FORWARD_CLOSE_POLICY_V1 close kinds.
export function forwardCloseNote(kind?: string) {
  if (kind === 'vanished') return 'изчезнал pool: оценен на последната цена −10%';
  if (kind === 'drained') return 'източен pool (ликвидност 0): оценен на 0';
  return null;
}

const usd = (value: number) => `${value < 0 ? '−' : ''}$${Math.abs(value).toFixed(2)}`;

export function labForwardSummary(book: { id: string; strategy_lifecycle?: StrategyLifecycle }) {
  if (!isForwardTestBook(book)) return null;
  const lifecycle = book.strategy_lifecycle;
  const evidence = lifecycle?.evidence;
  const closed = evidence?.closed_trades ?? 0;
  const minimum = evidence?.min_closes ?? 50;
  const mean = evidence?.mean_net50_usd;
  const [low, high] = evidence?.ci95_mean_net50_usd ?? [];
  const parts = [`Затворени ${closed}/${minimum} до правилото за спиране`];
  if (typeof mean === 'number' && Number.isFinite(mean)) parts.push(`средно net50 ${usd(mean)}/сделка`);
  if (typeof low === 'number' && typeof high === 'number') parts.push(`CI95 [${usd(low)}, ${usd(high)}]`);
  // LAB_FORWARD_FILL_BASIS_V4: the same closes re-priced at the research fills (next DexScreener refresh).
  const research = evidence?.research_fill;
  if (research && (research.closed_trades ?? 0) > 0) {
    const [researchLow, researchHigh] = research.ci95_mean_net50_usd ?? [];
    let text = `при изпълнение на следващото опресняване (research): ${research.closed_trades} сделки`;
    if (typeof research.mean_net50_usd === 'number' && Number.isFinite(research.mean_net50_usd)) {
      text += `, средно net50 ${usd(research.mean_net50_usd)}/сделка`;
    }
    if (typeof researchLow === 'number' && typeof researchHigh === 'number') {
      text += `, CI95 [${usd(researchLow)}, ${usd(researchHigh)}]`;
    }
    parts.push(text);
  }
  if (lifecycle?.status === 'retired') {
    const bases = evidence?.kill_rule_met_bases ?? [];
    parts.push(`спряна: средно net50 < 0 и горна граница на CI95 < 0${bases.length === 1 && bases[0] === 'research_fill' ? ' (при research изпълнение)' : ''}`);
  }
  if (lifecycle?.status === 'cash_exhausted') {
    const balance = lifecycle.cash?.balance_usd;
    parts.push(`без капитал${typeof balance === 'number' ? ` (${usd(balance)})` : ''}: фиксираният вход $200 не може да се финансира${lifecycle.kill_rule_evaluable ? '' : ', правилото за спиране не може да се оцени'}`);
  }
  // LAB_FORWARD_CONTROL_CONTINUITY_V1: the control keeps measuring while its hypothesis can enter.
  if (lifecycle?.status === 'active' && lifecycle.reason === 'control_kill_rule_deferred') {
    parts.push('правилото за спиране е изпълнено, но контролата продължава, докато хипотезата може да влиза');
  }
  if (lifecycle?.status === 'active' && lifecycle.capital_mode === ZERO_CAPITAL_CONTROL) {
    parts.push('балансът не покрива $200: контролата продължава с нулев капитал (тези сделки не променят баланса)');
  }
  const zeroCapital = evidence?.zero_capital_closes ?? 0;
  if (zeroCapital > 0) parts.push(`сделки с нулев капитал ${zeroCapital}`);
  const unpriced = (evidence?.vanished_closes ?? 0) + (evidence?.drained_closes ?? 0);
  if (unpriced > 0) parts.push(`изчезнали/източени pool-ове ${unpriced}`);
  // LAB_FORWARD_SIGNAL_CARRY_V2: signals lost while the independent price check was still pending.
  const lost = lifecycle?.signal_carry?.lost_price_pending ?? 0;
  if (lost > 0) parts.push(`изгубени сигнали при чакаща проверка на цената ${lost}`);
  const gate = lifecycle?.promotion_gate;
  const coverage = gate?.criteria?.control_coverage;
  if (gate && coverage?.pass === false && typeof coverage.value === 'number') {
    parts.push(`контролата е можела да влиза само в ${(coverage.value * 100).toFixed(0)}% от периода (${gate.control_window?.control_entry_end_reason ?? 'няма контрола'})`);
  }
  // A gate is met only when every criterion was evaluated; the 3-slot $1000 drawdown needs an
  // offline replay, so passing the evaluable criteria is not a met gate.
  if (gate) parts.push(labForwardGateStatus(gate));
  return parts.join(' · ');
}

export function labForwardGateStatus(gate: LabForwardGate) {
  if (gate.gate_met === true) {
    return 'гейтът за промоция е изпълнен: само за преглед от собственика, без автоматична промоция';
  }
  if (gate.all_evaluable_pass) {
    return 'оценимите критерии на гейта са изпълнени; чака офлайн проверка на спада при 3 слота и $1000 (≤ 20%), затова гейтът още не е изпълнен; без автоматична промоция';
  }
  return 'гейтът за промоция не е изпълнен (≥ 150 сделки, ≥ 25 pool-а, ≥ 3 дни, CI95 > 0 и по-добра от контролата в същия период, при записаните и при research изпълненията)';
}

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
  funded_active_daily_loss_limit: 'Дневният лимит за PAPER загуба е достигнат',
  funded_active_marks_unavailable: 'Чака прясна оценка на отворените позиции',
  funded_active_slots_full: 'Всички паралелни PAPER позиции са заети',
  funded_active_hourly_order_limit: 'Лимитът от 50 нови входа за час е достигнат',
  funded_active_exposure_limit: 'Няма свободен капитал в лимита за експозиция',
  no_market_signal: 'Няма пазарен сигнал по правилата',
  promoted_verified_flow_unavailable: 'Чака пресен потвърден поток',
  promoted_verified_flow_stale: 'Потвърденият поток е остарял',
  promoted_buy_pressure_unconfirmed: 'Няма потвърден натиск от купувачи',
  promoted_recent_loss_cooldown: 'Пауза след скорошна загуба',
  quality_buy_flow_too_small: 'Потвърденият купувачески поток е твърде слаб за вход $100',
  quality_correlated_position: 'Този токен или pool вече е отворен в друга PAPER сметка',
  quality_pool_loss_pause: 'Пауза 30 минути след загуба в този pool',
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
  rug_ticker_registry_warming: 'Регистърът на тикерите още няма достатъчна непрекъсната история',
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
  // LAB_FORWARD_TESTS_V1: the pre-registered kill rule stopped new entries of this forward-test book.
  lab_forward_kill_rule_retired: 'Спряна по предварително регистрираното правило (≥ 50 сделки, net50 < 0, CI95 < 0)',
  // LAB_FORWARD_CASH_STATE_V1 / frozen cost model of the forward-test books.
  lab_forward_cash_exhausted: 'Без капитал: балансът не покрива фиксирания вход от $200',
  lab_forward_cost_model_mismatch: 'Моделът на разходите се различава от замразения: входовете са спрени',
  // LAB_FORWARD_SIGNAL_CARRY_V2: the signal is kept for up to 60 s while the price check is pending.
  lab_forward_price_check_pending: 'Сигналът чака независимата проверка на цената (пази се до 60 сек.)',
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
  // The cap is min(0.5 × net stop, this model ceiling).
  max_entry_roundtrip_cost_ceiling_pct?: number;
  stop_loss_net_pct?: number;
  // LAB_FORWARD_TESTS_V1 books check one fixed size and never reduce it.
  entry_size_rule?: string;
  fixed_notional_usd?: number;
  // LAB_ACTIVE_V6: fee-and-buffer planning floor published for every book.
  cost_feasibility?: CostFeasibility;
  promoted_cost_feasibility?: CostFeasibility;
};

type EntryViewBook = ViewBook & { entry_diagnostics?: LabEntryViewDiagnostics };
type EntryView = { status: string; detail: string | null; costLimited: boolean };

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

// LAB_ACTIVE_V6: the admission cap is min(0.5 × net stop, the model ceiling). Name the
// ceiling when it binds (a 15% stop gives 2.75%, not 7.5%); state no equation that the
// published numbers do not satisfy.
export function costCapRule(cap?: number, stop?: number, ceiling?: number) {
  if (!finite(cap) || !finite(stop)) return null;
  const half = 0.5 * stop;
  if (Math.abs(cap - half) <= 0.005) return `0.5 × нетен стоп ${stop.toFixed(2)}%`;
  const roof = finite(ceiling) ? ceiling : cap;
  if (half > cap && Math.abs(cap - roof) <= 0.005) return `min(0.5 × нетен стоп ${stop.toFixed(2)}%, таван ${roof.toFixed(2)}%)`;
  return null;
}

function costCapView(diagnostics: LabEntryViewDiagnostics): EntryView | null {
  // LAB_ACTIVE_V6: every checked candidate of this book exceeded the admission
  // cap under the full modeled round trip (fees, impact, buffers, network).
  if (diagnostics.blocked_reason !== 'modeled_roundtrip_cost_limit') return null;
  const rejected = diagnostics.cost_infeasible_candidates ?? diagnostics.cost_rejected;
  const cap = diagnostics.max_entry_roundtrip_cost_pct;
  if (!finite(rejected) || rejected <= 0 || !finite(cap) || cap < 0) return null;
  const rule = costCapRule(cap, diagnostics.stop_loss_net_pct, diagnostics.max_entry_roundtrip_cost_ceiling_pct);
  const signals = finite(diagnostics.signal_candidates) ? diagnostics.signal_candidates : rejected;
  // A forward-test book checks one fixed size and never reduces it to fit.
  const size = finite(diagnostics.fixed_notional_usd)
    ? `при фиксирания вход $${diagnostics.fixed_notional_usd.toFixed(0)} (размерът не се намалява)`
    : 'при всеки проверен размер';
  return {
    status: `Няма вход: ${rejected} от ${signals} кандидата над лимита на разходите ${cap.toFixed(2)}%`,
    detail: `Моделираният round-trip (DEX такса вход и изход, impact, slippage/забавяне, мрежа) ${size} надхвърля лимита ${cap.toFixed(2)}%${rule ? ` = ${rule}` : ''}. Кандидатите са отчетени като неизпълними по разходи, не са скрити; лимитът не се сваля, за да се отворят сделки. Не е изпълнима котировка.`,
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
    // The book's own net stop when published (the cap may be the model ceiling, not stop / 2).
    const stopBudget = finite(diagnostics.stop_loss_net_pct) ? diagnostics.stop_loss_net_pct
      : cost.maximum_roundtrip_cost_pct! * 2;
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

export type FundedActiveStats = {
  version: string; trades: number; wins: number; win_rate: number | null; net_pnl_usd: number;
  orders_last_60m: number; closed_last_60m: number; open_positions: number; max_positions: number;
  target_orders_per_hour: number; exposure_usd: number; daily_loss_limit_usd: number; fixed_notional_usd?: number;
  funded_capital_usd?: number; effective_position_capacity?: number;
  capacity_test?: boolean; risk_limits_shadow_only?: boolean;
  quality_mode?: boolean; entry_cost_limit_pct?: number;
  legacy_open_positions?: number; legacy_closed_trades?: number; legacy_net_pnl_usd?: number;
  open_net_pnl_usd?: number;
  daily_net_usd?: number; blocked_reason?: string | null; adaptive_open_positions?: number;
  daily_window_basis?: string; daily_window_ends_at?: number; next_day_guarantees_entry?: boolean;
  exit_policy?: { version: string; take_profit_net_usd: number; stop_loss_net_usd: number;
    max_hold_minutes?: number | null; holding_profile?: string;
    profit_protection_arm_net_usd?: number; profit_giveback_fraction?: number; minimum_profit_giveback_usd?: number };
};

export type ProfitProtection = { version: string; activated_at: number; last_mark_at: number | null;
  peak_net_usd: number | null; floor_net_usd: number | null; armed: boolean; arm_net_usd?: number };

export function profitProtectionSummary(state?: ProfitProtection, quoteStatus?: string) {
  if (!state) return null;
  if (state.last_mark_at == null) return 'Защитата чака първа прясна цена след включването; старият връх не е използван.';
  const stale = quoteStatus !== 'fresh' ? ' Котировката не е прясна; прагът не потвърждава изпълним изход.' : '';
  return state.armed && finite(state.peak_net_usd) && finite(state.floor_net_usd)
    ? `Защита включена · нетен връх от включването +${usd(state.peak_net_usd)} · праг за изход +${usd(state.floor_net_usd)} (не е гарантирана печалба).${stale}`
    : `Защитата още не е включена · чака нетна печалба +${usd(state.arm_net_usd ?? 4)}; старият връх не е използван.${stale}`;
}

export function fundedActiveSummary(stats?: FundedActiveStats) {
  if (!stats) return null;
  const win = stats.win_rate == null ? 'няма затворени сделки' : `${stats.win_rate.toFixed(1)}% печеливши`;
  const size = stats.fixed_notional_usd == null ? '' : ` · вход ${usd(stats.fixed_notional_usd)}, без намаляване`;
  const capital = stats.funded_capital_usd == null ? '' : ` · внесен PAPER капитал ${usd(stats.funded_capital_usd)} (не е печалба)`;
  const capacity = stats.effective_position_capacity == null ? '' : ` · капиталов капацитет ${stats.effective_position_capacity} позиции`;
  const mode = stats.quality_mode ? 'PAPER КАЧЕСТВО — само потвърдени сигнали; без принудително запълване; печалба не е доказана'
    : stats.capacity_test ? 'ТЕСТ ЗАПЪЛВАНЕ — не е вход по стратегически сигнал; не доказва печалба' : 'Нова PAPER политика';
  const adaptive = stats.exit_policy?.profit_protection_arm_net_usd != null;
  const horizon = stats.exit_policy?.max_hold_minutes;
  const clock = horizon != null ? `${stats.exit_policy?.holding_profile ?? 'PAPER'} · максимум ${horizon} мин от оригиналния вход; при прясна валидна цена затваря и на загуба, рестартът не удължава срока` : 'без фиксиран времеви изход';
  const trail = adaptive ? `${clock} · защита от +${usd(stats.exit_policy!.profit_protection_arm_net_usd!)} нето, отстъпление max(${usd(stats.exit_policy!.minimum_profit_giveback_usd!)}, ${((stats.exit_policy!.profit_giveback_fraction ?? 0)*100).toFixed(0)}% от нетния връх); може да затвори преди +${usd(stats.exit_policy!.take_profit_net_usd)}`
    : 'без кратък таймер и trailing';
  const exits = stats.exit_policy ? ` · ${stats.quality_mode ? 'нови позиции: ' : ''}цел +${usd(stats.exit_policy.take_profit_net_usd)} нето / стоп −${usd(stats.exit_policy.stop_loss_net_usd)} нето · ${trail}; праговете не гарантират цена на изхода` : '';
  const quality = stats.quality_mode ? ` · разходи до ${stats.entry_cost_limit_pct?.toFixed(2) ?? '1.50'}% · един токен само в една сметка · стари позиции ${stats.legacy_open_positions ?? 0}${adaptive ? ` · с адаптивен изход ${stats.adaptive_open_positions ?? 0} отворени (промяната е записана)` : ' (запазени изходи)'} · история преди тази политика: ${stats.legacy_closed_trades ?? 0} затворени, нето ${usd(stats.legacy_net_pnl_usd ?? 0)} · отворен PnL ${usd(stats.open_net_pnl_usd ?? 0)} (не е прибрана печалба)` : '';
  const pause = stats.quality_mode && stats.blocked_reason ? ` · Нови входове: ${reasons[stats.blocked_reason] ?? stats.blocked_reason}${finite(stats.daily_net_usd) ? ` · дневен нетен резултат ${usd(stats.daily_net_usd)}` : ''}; изходите продължават да работят` : '';
  const reset = stats.quality_mode && stats.blocked_reason === 'funded_active_daily_loss_limit'
    && stats.daily_window_basis === 'UTC_CALENDAR_DAY_INCLUDING_OPEN_MARKS'
    && finite(stats.daily_window_ends_at) && stats.daily_window_ends_at > 0 && stats.daily_window_ends_at < 8.64e15
    ? ` · Следващ UTC ден: ${new Date(stats.daily_window_ends_at).toLocaleString('bg-BG')} местно време; отворените загуби остават в риска; това не гарантира нов вход` : '';
  const limits = stats.risk_limits_shadow_only ? `цел запълване на свободните места; сигналните филтри, паузите и лимитите за загуба/честота са само записани, не спират теста. PAPER капиталът може да се загуби; цена, safety и капиталовата експозиция остават задължителни` : `цел до ${stats.target_orders_per_hour} входа/ч, без гаранция · дневен лимит загуба ${usd(stats.daily_loss_limit_usd)}`;
  return `${mode}${size}${capital}${exits} · ${stats.trades} затворени · ${win} · нетен PnL ${usd(stats.net_pnl_usd)} след разходи · последен час: ${stats.orders_last_60m} входа / ${stats.closed_last_60m} изхода · позиции ${stats.open_positions}/${stats.max_positions}${capacity} · ${limits}${quality}${pause}${reset}`;
}
