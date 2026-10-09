// LAB_HIGH_FREQUENCY_V1: pure view helpers for the HF PAPER books (strategy_lab.high_frequency).
// The books trade about 50 times an hour each at $25 and are expected to lose about the
// round-trip cost on every trade: a measurement, never a strategy or a promotion candidate.

export type LabHighFrequencyClose = {
  trade_no?: number; seq?: number; symbol?: string | null; pairAddress?: string; closed_at?: number;
  kind?: string; pnl_usd?: number | null; pnl_pct?: number | null; net50_usd?: number | null;
  net50_pct?: number | null; fee_bps?: number | null; hold_s?: number | null; flags?: string[];
};

export type LabHighFrequencyKill = {
  closes?: number; next_checkpoint?: number; utc_days_with_closes?: number; min_utc_days?: number;
  applies?: boolean;
  last?: {
    at?: number; closes?: number; met?: boolean; mean_net50_usd?: number | null;
    ci95_net50_usd?: (number | null)[]; gap_net50_pct?: number | null; control_ci_half_width_pct?: number | null;
  } | null;
};

export type LabHighFrequencyBook = {
  id: string; name: string; role?: 'hypothesis' | 'random_control' | string; control?: string | null;
  config_hash?: string;
  // active, warming, session_closed (open_hour), cap (until), retired (reason), degraded, cost_model_mismatch
  status: string; open_hour?: number; until?: number; reason?: string;
  control_retired?: boolean; retired?: { reason?: string; at?: number } | null;
  trades_last_60m?: number; trades_today?: number; orders_today?: number; orders_last_60m?: number;
  open_slots?: number; max_slots?: number;
  balance_usd?: number | null; equity_usd?: number | null; start_balance_usd?: number | null;
  booked_today_usd?: number | null; net50_today_usd?: number | null;
  booked_total_usd?: number | null; net50_total_usd?: number | null;
  closes_total?: number; closes_config?: number;
  mean_booked_pct?: number | null; mean_net50_pct?: number | null; win_rate_net50_pct?: number | null;
  usd_per_hour_today?: { booked?: number | null; net50?: number | null } | null;
  cap?: { limit_usd?: number; used_usd?: number | null; tripped_at?: number | null };
  session_open_hour?: number;
  kill?: LabHighFrequencyKill;
  gap_vs_control?: {
    gap_net50_pct?: number | null; ci95?: (number | null)[] | null; within_fee_bucket_pct?: Record<string, number>;
  } | null;
  top_pair_share?: number | null; pairs?: number;
  cancels_today?: Record<string, number>;
  last_closes?: LabHighFrequencyClose[];
  blocked_reason?: string | null;
};

export type LabHighFrequencySnapshot = {
  version?: string; title?: string; badge?: string; updated_at?: number; loaded?: boolean; degraded?: boolean;
  // The last HF failure while it is at most 10 minutes old (strategy_lab hf_error).
  last_error?: { text?: string | null; at?: number | null; count?: number | null; consecutive_loops?: number | null } | null;
  notional_usd?: number; hold_seconds?: number; expected_net50_pct_per_trade?: number;
  automatic_promotion?: boolean; profitability_proven?: boolean;
  universe?: { events?: number | null; universe?: number | null; pools?: number | null };
  books: LabHighFrequencyBook[];
};

export const HF_TITLE = 'Висока честота (HF)';
export const HF_BADGE = 'ЕКСПЕРИМЕНТ · ОЧАКВА СЕ ЗАГУБА';
export const HF_NOTE = 'Около 50 сделки/час на книга, $25 на сделка, изход след 2 мин. Цени: следващото опресняване на DexScreener + моделирани разходи (CALIB_V1, net50); не е изпълнима котировка. Изследването очаква ≈ −3,6% на сделка (≈ $37–44/час на книга). Измерване, не стратегия; без промоция.';
export const HF_EMPTY = 'Няма HF данни';
export const HF_CAP_TEXT = 'спрян до 00:00 UTC';
export const HF_ERROR_PREFIX = 'Грешка в HF';

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

export function hfMoney(value: number | null | undefined, digits = 2): string {
  if (!finite(value)) return '—';
  return `${value < 0 ? '−' : value > 0 ? '+' : ''}$${Math.abs(value).toFixed(digits)}`;
}

export function hfPct(value: number | null | undefined, digits = 2): string {
  if (!finite(value)) return '—';
  return `${value < 0 ? '−' : value > 0 ? '+' : ''}${Math.abs(value).toFixed(digits)}%`;
}

export function hfPp(value: number | null | undefined, digits = 2): string {
  if (!finite(value)) return '—';
  return `${value < 0 ? '−' : value > 0 ? '+' : ''}${Math.abs(value).toFixed(digits)} pp`;
}

const hour = (value: number | undefined) => `${String(finite(value) ? value : 0).padStart(2, '0')}:00 UTC`;

const retireReasons: Record<string, string> = {
  capital_floor: 'капиталът падна до 50% от началния',
  kill_checkpoint: 'статистическата проверка потвърди загуба',
  operator_flag: 'спряна от оператора',
  all_hypotheses_retired: 'и двете хипотези са спрени',
};

export function hfStatusText(book: Pick<LabHighFrequencyBook, 'status' | 'open_hour' | 'reason'>): string {
  switch (book.status) {
    case 'active': return 'Търгува';
    case 'warming': return 'Загрява (историята на цените и heat филтърът се попълват)';
    case 'session_closed': return `Сесията е затворена до ${hour(book.open_hour)}`;
    case 'cap': return HF_CAP_TEXT;
    case 'retired': return `Спряна окончателно${book.reason && retireReasons[book.reason] ? `: ${retireReasons[book.reason]}` : ''}`;
    case 'degraded': return 'Забавена: без нови поръчки, изходите продължават';
    case 'cost_model_mismatch': return 'Разходният модел се различава от регистрирания: без нови поръчки';
    default: return 'Изчаква';
  }
}

export function hfStatusTone(status: string): 'ok' | 'wait' | 'stop' {
  if (status === 'active') return 'ok';
  if (status === 'retired' || status === 'cost_model_mismatch') return 'stop';
  return 'wait';
}

export function hfCapBar(book: Pick<LabHighFrequencyBook, 'cap'>): { usedPct: number; text: string } {
  const limit = finite(book.cap?.limit_usd) && book.cap!.limit_usd! > 0 ? book.cap!.limit_usd! : 100;
  const used = finite(book.cap?.used_usd) ? Math.max(0, book.cap!.used_usd!) : 0;
  const usedPct = Math.min(100, Math.round(used / limit * 1000) / 10);
  return { usedPct, text: `Дневен лимит на загубата: $${used.toFixed(2)} от $${limit.toFixed(0)}` };
}

export function hfKillText(book: Pick<LabHighFrequencyBook, 'role' | 'kill'>): string {
  if (book.role !== 'hypothesis' || book.kill?.applies === false) {
    return 'Контролата спира, когато спрат и двете хипотези, или когато капиталът падне до 50%.';
  }
  const kill = book.kill ?? {};
  const last = kill.last;
  const progress = `Проверка при ${kill.next_checkpoint ?? 500} сделки и ≥ ${kill.min_utc_days ?? 3} UTC дни: `
    + `${kill.closes ?? 0}/${kill.next_checkpoint ?? 500} сделки, ${kill.utc_days_with_closes ?? 0} дни.`;
  if (!last) return progress;
  const high = last.ci95_net50_usd?.[1];
  return `${progress} Последна проверка: средно net50 ${hfMoney(last.mean_net50_usd, 3)}/сделка, `
    + `горна граница CI95 ${hfMoney(high ?? null, 3)}, разлика спрямо контролата ${hfPp(last.gap_net50_pct)} `
    + `(половин CI на контролата ${hfPp(last.control_ci_half_width_pct)}) → ${last.met ? 'спира' : 'продължава'}.`;
}

export function hfGapText(book: Pick<LabHighFrequencyBook, 'role' | 'gap_vs_control'>): string | null {
  if (book.role !== 'hypothesis') return null;
  const gap = book.gap_vs_control;
  if (!gap || !finite(gap.gap_net50_pct)) return 'Разлика спрямо контролата: още няма сделки и в двете книги.';
  const ci = gap.ci95 && finite(gap.ci95[0]) && finite(gap.ci95[1])
    ? ` (CI95 ${hfPp(gap.ci95[0])} … ${hfPp(gap.ci95[1])})` : ' (CI95: нужни са поне 3 пула във всяка книга)';
  const buckets = gap.within_fee_bucket_pct ?? {};
  const within = ['fee<=50', 'fee55-95'].filter(name => finite(buckets[name]))
    .map(name => `${name === 'fee<=50' ? 'такса ≤ 50 bps' : 'такса 55–95 bps'}: ${hfPp(buckets[name])}`);
  return `Разлика net50 спрямо контролата: ${hfPp(gap.gap_net50_pct)}${ci}`
    + (within.length ? `; в една и съща ценова група: ${within.join(', ')}` : '') + '.';
}

const cancelReasons: Record<string, string> = {
  hf_entry_no_next_observation: 'няма ново наблюдение до 60 с',
  hf_structural_block_at_fill: 'rug проверка при изпълнението',
  hf_entry_unpriced: 'липсва мрежова цена',
  restart: 'рестарт',
};

export function hfCancelText(cancels: Record<string, number> | undefined): string {
  const entries = Object.entries(cancels ?? {}).filter(([, count]) => count > 0).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return 'Отказани поръчки днес: няма.';
  return 'Отказани поръчки днес: ' + entries.map(([reason, count]) => `${cancelReasons[reason] ?? reason} ${count}`).join(' · ') + '.';
}

const closeKinds: Record<string, string> = {
  HF_TIME_120: 'изход след 2 мин',
  VANISHED: 'изчезнал пул (−10%)',
  FEED_GAP: 'прекъсване на данните',
};

export function hfCloseKind(kind: string | undefined): string {
  return (kind && closeKinds[kind]) || kind || '—';
}

export function hfErrorText(snapshot: Pick<LabHighFrequencySnapshot, 'last_error'> | null): string | null {
  const error = snapshot?.last_error;
  if (!error || typeof error.text !== 'string' || !error.text) return null;
  const loops = finite(error.consecutive_loops) && error.consecutive_loops > 0
    ? ` (поредни цикли с грешка: ${error.consecutive_loops})` : '';
  return `${HF_ERROR_PREFIX}: ${error.text}${loops}. Данните по-долу може да не са актуални.`;
}

export function hfSnapshotOrNull(data: unknown): LabHighFrequencySnapshot | null {
  if (!data || typeof data !== 'object' || !Array.isArray((data as LabHighFrequencySnapshot).books)) return null;
  return data as LabHighFrequencySnapshot;
}
