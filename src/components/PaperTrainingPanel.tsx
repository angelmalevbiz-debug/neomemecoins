type PostExitMark = { delta_to_executed_exit_usd: number | null; valuation: string };
type ExitAnalysis = {
  mfe_minus_realized_pct?: number; stop_gap_pct?: number;
  post_exit?: { status: string; window_ends_at: number; valid_observations: number;
    unavailable_observations: number; best: PostExitMark | null; worst: PostExitMark | null };
};
export type TrainingBook = {
  id: string; starting_balance: number; cash: number; equity: number; net_pnl_usd: number;
  return_pct: number; completed_trades: number; open_positions: number; pending_orders: number;
  unique_completed_episodes: number; wins: number; losses: number; max_drawdown_pct: number;
  failed_executions: number; feasibility: number | null; rejected_signals?: number;
  rejected_market_episodes?: number; evaluated_rejected_paths?: number;
  rejection_reasons: Record<string, number>; risk_halt: string | null;
  positions: { symbol: string; address: string; opened_at: number; valuation: string; committed_usd: number }[];
  recent_trades: { symbol: string; closed_at: number; pnl_usd: number; exit_reason: string; exit_analysis?: ExitAnalysis }[];
};
type Comparison = { net_improvement_usd: number; paired_episode_count: number;
  approximate_cluster_mean_95_ci: number[] | null; stress_net_pnl_usd: number };
export type PaperTrainingSnapshot = {
  status: string; paper_only: boolean; active_version?: string; simulation_count?: number;
  unique_market_episodes?: number; unique_observations?: number; duplicate_observations?: number;
  books?: TrainingBook[]; control_comparison?: Comparison; monitor_comparison?: Comparison | null;
  last_training?: { at: number; status: string; candidate?: string; rejected_checks?: string[] } | null;
  versions?: { id: string; status: string; at: number }[];
  quote_probe?: { status: string; reason?: string; attempts?: number; successes?: number; last_attempt_at?: number };
  recorder?: { backlog: number; dropped: number; dropped_total?: number; dropped_baseline?: number; processed: number; error: string | null };
  error?: string; reason?: string;
};
const money = (value = 0) => `$${value.toFixed(2)}`;
const stamp = (value?: number) => value ? new Date(value).toLocaleString('bg-BG') : '—';
const count = (value?: number) => value == null ? '—' : value.toLocaleString('bg-BG');
const reasonLabel = (reason: string) => ({
  safety_not_fresh_pass: 'Липсва свежа, пълна проверка за безопасност.',
  independent_price_not_fresh_pass: 'Липсва свежа независима проверка на цената.',
  rent_or_sol_cost_unknown: 'Разходите за мрежата и сметката не са потвърдени.',
  safety_unknown_or_rejected: 'Безопасността е неизвестна или проверката е отказана',
  stale_or_unknown_flow: 'Потокът от сделки е остарял или неизвестен',
  insufficient_flow_evidence: 'Няма достатъчно потвърдени сделки и участници',
  missing_executable_buy_evidence: 'Липсва изпълнима котировка за покупка',
  missing_executable_sell_evidence: 'Липсва изпълнима котировка за продажба',
  flow_ratio: 'Няма достатъчен превес на покупките',
  score: 'Сигналът не покрива изискваното качество',
  price_or_liquidity: 'Цената или ликвидността не покрива изискванията',
  roundtrip_cost: 'Разходите за покупка и продажба са твърде високи',
  stale_or_future_market: 'Пазарните данни не са свежи или имат невалидно време',
  adaptive_context_unknown_or_future: 'Липсва валиден пазарен контекст',
  signal_or_flow_expired: 'Сигналът или потвърденият поток е изтекъл.',
  signal_or_market_expired: 'Сигналът или пазарните данни са изтекли.',
  exact_pool_buy_unavailable: 'Не е намерена изпълнима покупка за същия пазар.',
  exact_pool_sell_unavailable: 'Не е намерена изпълнима продажба за същия пазар.',
  candidate_gate: 'Сигналът не покрива условията за изследване.',
} as Record<string, string>)[reason] || reason;
const trainingStatus = (status?: string) => ({
  insufficient_evidence_or_no_better_candidate: 'Недостатъчно доказателства или няма по-добър кандидат',
  candidate_frozen_awaiting_future_validation: 'Кандидатът чака проверка с бъдещи данни',
  rejected_future_validation: 'Кандидатът не е преминал проверката',
  approved_isolated_paper: 'Приет само за изследователския PAPER портфейл',
  initial: 'Начална версия без проверена доходност',
  automatic_rollback: 'Автоматично връщане към предишна версия',
} as Record<string, string>)[status || ''] || status || 'Няма проведена оценка';

export default function PaperTrainingPanel({ data }: { data?: PaperTrainingSnapshot }) {
  const books = data?.books || [];
  const completed = books.reduce((sum, book) => sum + book.completed_trades, 0);
  const open = books.reduce((sum, book) => sum + book.open_positions, 0);
  const pending = books.reduce((sum, book) => sum + book.pending_orders, 0);
  const hasBookReport = data?.books != null;
  const error = data?.error || data?.recorder?.error;
  const disabled = data?.status?.toLowerCase() === 'disabled';
  const currentVersion = data?.versions?.find(version => version.id === data.active_version);
  const baseline = currentVersion?.status === 'initial' || data?.active_version === 'v0-control';
  const referenceBook = books.find(book => book.id === 'CONTROL') || books[0];
  const refusals = Object.entries(referenceBook?.rejection_reasons || {}).filter(([, value]) => value > 0)
    .sort((a, b) => b[1] - a[1]).slice(0, 3);
  const comparison = data?.monitor_comparison || data?.control_comparison;
  const status = error ? 'Проблем с обучението' : disabled ? 'Обучението е изключено'
    : !hasBookReport ? 'Изчаква отчет' : open || pending ? 'Има активни симулации' : 'Изчаква подходящ сигнал';
  return <section className="mt-5 rounded-2xl border border-white/10 bg-[#0b0e11] p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><div className="text-[9px] font-black uppercase tracking-widest text-emerald-300">PAPER обучение</div>
        <h2 className="mt-1 text-lg font-black text-white">Изследване на стратегии</h2></div>
      <span className={`rounded-lg border border-white/10 px-3 py-2 text-xs ${error ? 'text-red-300' : 'text-slate-400'}`}>{status}</span>
    </div>
    <p className="mt-3 text-xs leading-5 text-slate-400">Тук се сравняват отделни симулации с отчетени разходи. Приетите версии се прилагат само в изследователския портфейл LEARNER. Наблюденията и отказаните сигнали не са сделки и не доказват печалба.</p>
    <div className="mt-3 grid gap-3 text-xs sm:grid-cols-4">
      <div className="rounded-xl bg-white/[0.03] p-3"><div className="text-slate-500">Затворени симулирани сделки</div><strong className="text-white">{hasBookReport ? count(completed) : '—'}</strong><div className="mt-1 text-[9px] text-slate-500">Общо записи в отделните портфейли</div></div>
      <div className="rounded-xl bg-white/[0.03] p-3"><div className="text-slate-500">Отворени / чакащи</div><strong className="text-white">{hasBookReport ? `${count(open)} / ${count(pending)}` : '—'}</strong></div>
      <div className="rounded-xl bg-white/[0.03] p-3"><div className="text-slate-500">Текуща версия</div><strong className="text-white">{data?.active_version || '—'}</strong><div className="mt-1 text-[9px] text-slate-400">{baseline ? 'Начална версия без проверена доходност' : currentVersion ? trainingStatus(currentVersion.status) : 'Няма отчет за проверката на тази версия'}</div></div>
      <div className="rounded-xl bg-white/[0.03] p-3"><div className="text-slate-500">Последна оценка</div><strong className="text-white">{stamp(data?.last_training?.at)}</strong><div className="mt-1 text-[9px] text-slate-400">{trainingStatus(data?.last_training?.status)}</div></div>
    </div>
    <div className={`mt-3 rounded-xl border p-3 text-xs leading-5 ${error ? 'border-red-400/20 bg-red-400/5 text-red-200' : 'border-amber-300/15 bg-amber-300/[0.03] text-slate-300'}`}>
      {error ? <p>{error}</p> : disabled ? <p>{data?.reason || 'Няма активен изследователски процес.'}</p>
        : !hasBookReport ? <p>Изчаква се отчет от изследователския процес.</p>
        : completed === 0 ? <p>{open || pending ? 'Има активни симулации, но още няма затворен резултат за оценка.' : 'Няма отворени или затворени симулирани сделки. Засега се записват наблюдения и откази.'} Няма достатъчно резултати за избор на нова версия.</p>
        : <p>Има {count(completed)} затворени симулирани сделки в отделните портфейли. Доходността се оценява за всеки портфейл поотделно.</p>}
      {!error && !disabled && data?.quote_probe?.reason && <p className="mt-1">Последна проверка за вход: {reasonLabel(data.quote_probe.reason)}</p>}
      {!error && !disabled && data?.quote_probe?.status === 'WAITING_FOR_QUALIFIED_FLOW' && !data.quote_probe.reason && <p className="mt-1">Изчаква се свеж, потвърден поток от сделки, който покрива условията за вход.</p>}
      {!error && !disabled && completed === 0 && refusals.length > 0 && <div className="mt-2 text-[10px] text-slate-400">Най-чести причини за отказ в {referenceBook.id}:{refusals.map(([reason]) => <div key={reason}>{reasonLabel(reason)}</div>)}</div>}
    </div>
    {data?.last_training?.candidate && <p className="mt-2 text-xs text-amber-200">Кандидат {data.last_training.candidate}{data.last_training.rejected_checks?.length ? ` · ${data.last_training.rejected_checks.join(', ')}` : ''}</p>}
    {comparison && comparison.paired_episode_count > 0 && <p className="mt-2 text-xs text-slate-400">Измерена разлика спрямо контрол: {money(comparison.net_improvement_usd)} · {comparison.paired_episode_count} сравнени епизода · неблагоприятни разходи: {money(comparison.stress_net_pnl_usd)} · 95% приближен интервал: {comparison.approximate_cluster_mean_95_ci?.map(v => money(v)).join(' … ') || 'недостатъчно данни'}.</p>}
    <details className="mt-3 text-xs text-slate-400"><summary className="cursor-pointer font-bold text-white">Резултати по изследователски портфейли ({books.length})</summary>
    <p className="mt-2 text-[10px] leading-5 text-slate-500">Всеки портфейл има собствен капитал и лимити. Балансите не се събират като доходност на една сметка. Оценките са симулации, а не наблюдавани изпълнения на пазара.</p>
    <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[850px] text-left text-[10px]">
      <thead className="text-slate-500"><tr>{['Портфейл', 'Начало', 'Свободни', 'Equity', 'Нетен PnL', 'Отворени / затворени', 'Уникални епизоди', 'Drawdown', 'Провали / откази'].map(h => <th key={h} className="px-2 py-3">{h}</th>)}</tr></thead>
      <tbody>{books.map(book => <tr key={book.id} className="border-t border-white/[0.05] text-slate-300">
        <td className="px-2 py-3 font-bold">{book.id}{book.risk_halt && <div className="text-red-300">{book.risk_halt}</div>}</td>
        <td className="px-2">{money(book.starting_balance)}</td><td className="px-2">{money(book.cash)}</td><td className="px-2">{money(book.equity)}</td>
        <td className={`px-2 ${book.net_pnl_usd >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{money(book.net_pnl_usd)} · {book.return_pct.toFixed(2)}%</td>
        <td className="px-2">{book.open_positions} / {book.completed_trades} · чакащи {book.pending_orders}</td>
        <td className="px-2">{book.unique_completed_episodes}</td><td className="px-2">{book.max_drawdown_pct.toFixed(2)}%</td>
        <td className="px-2">{book.failed_executions} / {book.rejected_signals ?? 0}<div className="text-[9px] text-slate-500">{book.rejected_market_episodes ?? 0} епизода · {book.evaluated_rejected_paths ?? 0} оценени</div></td>
      </tr>)}</tbody>
    </table></div>
    <div className="mt-3 grid gap-3 md:grid-cols-2">{books.filter(b => b.positions.length || b.recent_trades.length).map(book => <details key={book.id} className="rounded-xl border border-white/[0.06] p-3 text-[10px] text-slate-400">
      <summary className="cursor-pointer font-bold text-white">{book.id} · позиции и последни сделки</summary>
      {book.positions.map(p => <div key={p.address} className="mt-2">{p.symbol} · {money(p.committed_usd)} · {stamp(p.opened_at)} · {p.valuation}</div>)}
      {book.recent_trades.map((t, i) => <details key={`${t.closed_at}-${i}`} className="mt-2 rounded-lg bg-white/[0.02] p-2">
        <summary className="cursor-pointer">{t.symbol} · {money(t.pnl_usd)} · {t.exit_reason} · {stamp(t.closed_at)}</summary>
        {t.exit_analysis ? <div className="mt-2 space-y-1 leading-5">
          <div>Разлика между най-добра наблюдавана оценка през позицията и реализирания резултат: {t.exit_analysis.mfe_minus_realized_pct?.toFixed(2) ?? '—'} процентни пункта. Превишение на планирания стоп: {t.exit_analysis.stop_gap_pct?.toFixed(2) ?? '—'} процентни пункта.</div>
          {t.exit_analysis.post_exit && <>
            <div>След затварянето: {t.exit_analysis.post_exit.valid_observations} валидни оценки · {t.exit_analysis.post_exit.unavailable_observations} наблюдения без изпълнима оценка · до {stamp(t.exit_analysis.post_exit.window_ends_at)} · {t.exit_analysis.post_exit.status}.</div>
            <div>Разлика на по-късните оценки спрямо изпълнения изход: най-добра {t.exit_analysis.post_exit.best?.delta_to_executed_exit_usd != null ? money(t.exit_analysis.post_exit.best.delta_to_executed_exit_usd) : '—'} · най-лоша {t.exit_analysis.post_exit.worst?.delta_to_executed_exit_usd != null ? money(t.exit_analysis.post_exit.worst.delta_to_executed_exit_usd) : '—'}.</div>
            <div className="text-amber-200/80">Това са последващи хипотетични оценки за същото количество с отчетени разходи. Те не са изпълнени сделки, не променят PnL и не са били налични при решението.</div>
          </>}
        </div> : <div className="mt-2">Липсва подробна оценка в този запис.</div>}
      </details>)}
    </details>)}</div>
    </details>
    <details className="mt-3 text-[10px] text-slate-500"><summary className="cursor-pointer">Запис на данни и проверки за изпълнимост</summary>
      <div className="mt-2">Наблюдения {count(data?.unique_observations)} · пазарни епизоди {count(data?.unique_market_episodes)} · дублирани наблюдения {count(data?.duplicate_observations)} · отворени симулации общо {count(data?.simulation_count)}.</div>
      <p className="mt-1 leading-5">Епизодите групират token/pool/час; не доказват статистическа независимост. Наблюденията може да описват един и същи пазар многократно. Тези броячи не измерват доходност или напредък към печеливша версия.</p>
      {data?.quote_probe && <p className="mt-2">Записана двупосочна котировка {count(data.quote_probe.successes)} / {count(data.quote_probe.attempts)} опита · {data.quote_probe.status} · последен опит {stamp(data.quote_probe.last_attempt_at)}. Успешната проверка е котировка, не изпълнена сделка.</p>}
      {data?.recorder && <p className="mt-2">Опашка {count(data.recorder.backlog)} · записани {count(data.recorder.processed)} · пропуснати в текущото обучение {count(data.recorder.dropped)}{(data.recorder.dropped_baseline ?? 0) > 0 ? ` · архивирани предишни пропуски ${count(data.recorder.dropped_baseline)}` : ''}. Пропуските в текущото обучение намаляват покритието.</p>}
      {referenceBook && <div className="mt-2">Брой откази в {referenceBook.id} (един сигнал може да има няколко причини):{Object.entries(referenceBook.rejection_reasons).sort((a, b) => b[1] - a[1]).map(([reason, value]) => <div key={reason}>{reasonLabel(reason)} · {count(value)}</div>)}</div>}
      {data?.status && <div className="mt-2">Статус на процеса: {data.status}{data.reason ? ` · ${data.reason}` : ''}</div>}
    </details>
    <details className="mt-3 text-[10px] text-slate-500"><summary className="cursor-pointer">История на версиите и връщанията</summary>
      {(data?.versions || []).map(v => <div key={v.id} className="mt-1">{v.id} · {trainingStatus(v.status)} · {stamp(v.at)}</div>)}
    </details>
  </section>;
}
