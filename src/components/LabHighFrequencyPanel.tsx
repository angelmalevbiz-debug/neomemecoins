import {
  HF_BADGE, HF_EMPTY, HF_NOTE, HF_TITLE, hfCancelText, hfCapBar, hfCloseKind, hfGapText, hfKillText, hfMoney, hfPct,
  hfSnapshotOrNull, hfStatusText, hfStatusTone, type LabHighFrequencyBook,
} from '../lib/labHighFrequencyView';

const toneClass = {
  ok: 'border-emerald-400/25 text-emerald-200',
  wait: 'border-amber-400/25 text-amber-200',
  stop: 'border-red-400/25 text-red-200',
};
const color = (value: number | null | undefined) =>
  typeof value === 'number' && value > 0 ? 'text-emerald-300' : typeof value === 'number' && value < 0 ? 'text-red-300' : 'text-slate-300';

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return <div className="min-w-0"><div className="text-[9px] text-slate-500">{label}</div><div className={`mt-0.5 text-[11px] font-semibold ${tone ?? 'text-white'}`}>{value}</div></div>;
}

function BookCard({ book }: { book: LabHighFrequencyBook }) {
  const cap = hfCapBar(book);
  const gap = hfGapText(book);
  const perHour = book.usd_per_hour_today;
  return <article data-testid={`hf-${book.id}`} className="border-t border-white/[0.07] px-4 py-3">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0">
        <h4 className="text-sm font-bold text-white">{book.name}</h4>
        <div className="mt-0.5 text-[10px] text-slate-500">{book.id}{book.role === 'random_control' ? ' · контрола' : ` · хипотеза, контрола ${book.control ?? '—'}`}</div>
      </div>
      <span data-testid={`hf-status-${book.id}`} className={`shrink-0 rounded-lg border px-2 py-1 text-[10px] font-bold ${toneClass[hfStatusTone(book.status)]}`}>{hfStatusText(book)}</span>
    </div>
    {book.control_retired && <p className="mt-2 text-[10px] text-amber-200">Контролата е спряна; хипотезата продължава без сравнение за същия период.</p>}
    <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
      <Metric label="Сделки за 60 мин" value={`${book.trades_last_60m ?? 0}`} />
      <Metric label="Сделки днес" value={`${book.trades_today ?? 0}`} />
      <Metric label="Отворени слотове" value={`${book.open_slots ?? 0}/${book.max_slots ?? 3}`} />
      <Metric label="Днес (записано / net50)" value={`${hfMoney(book.booked_today_usd)} / ${hfMoney(book.net50_today_usd)}`} tone={color(book.booked_today_usd)} />
      <Metric label="Общо (записано / net50)" value={`${hfMoney(book.booked_total_usd)} / ${hfMoney(book.net50_total_usd)}`} tone={color(book.booked_total_usd)} />
      <Metric label="Средно на сделка" value={`${hfPct(book.mean_booked_pct)} / ${hfPct(book.mean_net50_pct)}`} tone={color(book.mean_net50_pct)} />
      <Metric label="Печеливши (net50)" value={book.win_rate_net50_pct == null ? '—' : `${book.win_rate_net50_pct.toFixed(1)}%`} />
      <Metric label="$/час днес" value={perHour ? `${hfMoney(perHour.booked)} / ${hfMoney(perHour.net50)}` : '—'} tone={color(perHour?.booked)} />
      <Metric label="Баланс / капитал" value={`${hfMoney(book.balance_usd)} / ${hfMoney(book.equity_usd)}`} />
      <Metric label="Дял на най-честия пул" value={book.top_pair_share == null ? '—' : `${(book.top_pair_share * 100).toFixed(1)}% · ${book.pairs ?? 0} пула`} />
    </div>
    <div className="mt-3">
      <div className="flex justify-between text-[10px] text-slate-400"><span>{cap.text}</span><span>{cap.usedPct.toFixed(0)}%</span></div>
      <div className="mt-1 h-1.5 overflow-hidden rounded bg-white/[0.06]"><div data-testid={`hf-cap-${book.id}`} className={`h-full ${cap.usedPct >= 100 ? 'bg-red-400/70' : 'bg-amber-300/60'}`} style={{ width: `${cap.usedPct}%` }} /></div>
    </div>
    <p className="mt-2 text-[10px] leading-5 text-slate-400">{hfKillText(book)}</p>
    {gap && <p className="text-[10px] leading-5 text-slate-400">{gap}</p>}
    <p className="text-[10px] leading-5 text-slate-500">{hfCancelText(book.cancels_today)}</p>
    <details className="mt-1 text-[11px] text-slate-300"><summary className="cursor-pointer py-1 text-amber-200">Последни 10 сделки</summary>
      {(book.last_closes ?? []).length === 0 ? <p className="mt-1 text-slate-500">Още няма затворени сделки.</p> : <div className="mt-1 overflow-x-auto">
        <table className="w-full min-w-[520px] text-left text-[10px]">
          <thead className="text-slate-500"><tr><th className="py-1 pr-2">Токен</th><th className="pr-2">Изход</th><th className="pr-2">Записано</th><th className="pr-2">net50</th><th className="pr-2">Такса</th><th className="pr-2">Задържане</th><th>Флагове</th></tr></thead>
          <tbody>{(book.last_closes ?? []).slice(0, 10).map((close, index) => <tr key={`${close.seq ?? index}`} className="border-t border-white/[0.05]">
            <td className="py-1 pr-2 text-white">{close.symbol ?? '—'}</td>
            <td className="pr-2">{hfCloseKind(close.kind)}</td>
            <td className={`pr-2 ${color(close.pnl_usd)}`}>{hfMoney(close.pnl_usd)} ({hfPct(close.pnl_pct)})</td>
            <td className={`pr-2 ${color(close.net50_usd)}`}>{hfMoney(close.net50_usd)}</td>
            <td className="pr-2">{close.fee_bps == null ? '—' : `${close.fee_bps} bps`}</td>
            <td className="pr-2">{close.hold_s == null ? '—' : `${close.hold_s.toFixed(0)} с`}</td>
            <td className="text-slate-500">{(close.flags ?? []).join(', ') || '—'}</td>
          </tr>)}</tbody>
        </table>
      </div>}
    </details>
  </article>;
}

export default function LabHighFrequencyPanel({ data }: { data?: unknown }) {
  const snapshot = hfSnapshotOrNull(data);
  return <section data-testid="lab-high-frequency" className="border-b border-white/[0.06]">
    <header className="px-4 pb-2 pt-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-bold text-white">{HF_TITLE}</h3>
        <span data-testid="hf-badge" className="rounded-md border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 text-[9px] font-black tracking-wide text-amber-200">{HF_BADGE}</span>
      </div>
      <p data-testid="hf-note" className="mt-2 max-w-4xl text-[11px] leading-5 text-amber-100/80">{HF_NOTE}</p>
    </header>
    {!snapshot ? <p data-testid="hf-empty" className="px-4 pb-4 text-[11px] text-slate-400">{HF_EMPTY}</p>
      : snapshot.books.length === 0 ? <p data-testid="hf-empty" className="px-4 pb-4 text-[11px] text-slate-400">{HF_EMPTY}</p>
        : snapshot.books.map(book => <BookCard key={book.id} book={book} />)}
  </section>;
}
