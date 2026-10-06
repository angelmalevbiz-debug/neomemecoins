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
  recorder?: { backlog: number; dropped: number; dropped_total?: number; dropped_baseline?: number; processed: number; error: string | null };
  error?: string; reason?: string;
};
const money = (value = 0) => `$${value.toFixed(2)}`;
const stamp = (value?: number) => value ? new Date(value).toLocaleString('bg-BG') : '—';

export default function PaperTrainingPanel({ data }: { data?: PaperTrainingSnapshot }) {
  const comparison = data?.monitor_comparison || data?.control_comparison;
  return <section className="mt-5 rounded-2xl border border-white/10 bg-[#0b0e11] p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><div className="text-[9px] font-black uppercase tracking-widest text-emerald-300">PAPER обучение</div>
        <h2 className="mt-1 text-lg font-black text-white">Паралелни експерименти и проверени версии</h2></div>
      <span className="rounded-lg border border-white/10 px-3 py-2 text-xs text-slate-400">{data?.status || 'WAIT'} · {data?.active_version || 'v0-control'}</span>
    </div>
    <div className="mt-3 grid gap-3 text-xs sm:grid-cols-4">
      <div className="rounded-xl bg-white/[0.03] p-3"><div className="text-slate-500">Симулирани позиции</div><strong className="text-white">{data?.simulation_count ?? 0}</strong></div>
      <div className="rounded-xl bg-white/[0.03] p-3"><div className="text-slate-500">Уникални пазарни епизоди</div><strong className="text-white">{data?.unique_market_episodes ?? 0}</strong></div>
      <div className="rounded-xl bg-white/[0.03] p-3"><div className="text-slate-500">Уникални наблюдения</div><strong className="text-white">{data?.unique_observations ?? 0}</strong></div>
      <div className="rounded-xl bg-white/[0.03] p-3"><div className="text-slate-500">Последно обучение</div><strong className="text-white">{stamp(data?.last_training?.at)}</strong></div>
    </div>
    <p className="mt-3 text-[10px] leading-5 text-slate-500">Всеки портфейл има собствен капитал и лимити. Балансите не се събират като доходност на една сметка. Пазарните епизоди групират token/pool/час; не доказват статистическа независимост. Refused сигнали и пропуснати възможности не са изпълнени сделки.</p>
    {data?.last_training && <p className="mt-2 text-xs text-amber-200">{data.last_training.status} · кандидат {data.last_training.candidate || '—'}{data.last_training.rejected_checks?.length ? ` · ${data.last_training.rejected_checks.join(', ')}` : ''}</p>}
    {comparison && <p className="mt-2 text-xs text-slate-400">Измерена разлика спрямо контрол: {money(comparison.net_improvement_usd)} · {comparison.paired_episode_count} сравнени епизода · неблагоприятни разходи: {money(comparison.stress_net_pnl_usd)} · 95% приближен интервал: {comparison.approximate_cluster_mean_95_ci?.map(v => money(v)).join(' … ') || 'недостатъчно данни'}.</p>}
    {(data?.error || data?.recorder?.error) && <p className="mt-2 text-xs text-red-300">{data.error || data.recorder?.error}</p>}
    {data?.recorder && <p className="mt-2 text-[10px] text-slate-500">Опашка {data.recorder.backlog} · записани {data.recorder.processed} · пропуснати в текущото обучение {data.recorder.dropped}{(data.recorder.dropped_baseline ?? 0) > 0 ? ` · архивирани предишни пропуски ${data.recorder.dropped_baseline}` : ''}. Пропуските в текущото обучение намаляват покритието.</p>}
    <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[850px] text-left text-[10px]">
      <thead className="text-slate-500"><tr>{['Портфейл', 'Начало', 'Свободни', 'Equity', 'Нетен PnL', 'Отворени / затворени', 'Уникални епизоди', 'Drawdown', 'Провали / откази'].map(h => <th key={h} className="px-2 py-3">{h}</th>)}</tr></thead>
      <tbody>{(data?.books || []).map(book => <tr key={book.id} className="border-t border-white/[0.05] text-slate-300">
        <td className="px-2 py-3 font-bold">{book.id}{book.risk_halt && <div className="text-red-300">{book.risk_halt}</div>}</td>
        <td className="px-2">{money(book.starting_balance)}</td><td className="px-2">{money(book.cash)}</td><td className="px-2">{money(book.equity)}</td>
        <td className={`px-2 ${book.net_pnl_usd >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{money(book.net_pnl_usd)} · {book.return_pct.toFixed(2)}%</td>
        <td className="px-2">{book.open_positions} / {book.completed_trades} · чакащи {book.pending_orders}</td>
        <td className="px-2">{book.unique_completed_episodes}</td><td className="px-2">{book.max_drawdown_pct.toFixed(2)}%</td>
        <td className="px-2">{book.failed_executions} / {book.rejected_signals ?? 0}<div className="text-[9px] text-slate-500">{book.rejected_market_episodes ?? 0} епизода · {book.evaluated_rejected_paths ?? 0} оценени</div></td>
      </tr>)}</tbody>
    </table></div>
    <div className="mt-3 grid gap-3 md:grid-cols-2">{(data?.books || []).filter(b => b.positions.length || b.recent_trades.length).map(book => <details key={book.id} className="rounded-xl border border-white/[0.06] p-3 text-[10px] text-slate-400">
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
    <details className="mt-3 text-[10px] text-slate-500"><summary className="cursor-pointer">История на версиите и връщанията</summary>
      {(data?.versions || []).map(v => <div key={v.id} className="mt-1">{v.id} · {v.status} · {stamp(v.at)}</div>)}
    </details>
  </section>;
}
