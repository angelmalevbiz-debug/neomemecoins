import { Brain, ShieldCheck, Timer, Zap } from 'lucide-react';

export type AstraPosition = {
  id: string; symbol: string; address: string; notional_usd: number;
  pnl_pct: number; pnl_usd: number; target_net_pct: number;
  opened_at: number; quote_at: number; quote_age_seconds?: number;
  quote_status?: string; execution_entry_price: number;
  current_execution_price: number; exit_reason?: string; closed_at?: number;
  entry_network_fee_usd: number; entry_rent_reserved_usd: number;
  entry_roundtrip_pnl_pct: number;
  costs?: { network_fee_usd: number; execution_buffer_usd: number; net_proceeds_usd: number };
};
export type AstraSnapshot = {
  updated_at: number; status: string;
  book: { balance: number; starting_balance: number; positions: AstraPosition[]; history: AstraPosition[] };
  stats: { equity: number; available_usd: number; realized_pnl: number; trades: number; wins: number; win_rate: number; open_positions: number };
  diagnostics: { scanned: number; qualified: number; quote_attempts: number; message: string; rejections: Record<string, number> };
  config: { max_positions: number; min_notional_usd: number; max_notional_usd: number; stop_loss_net_pct: number; daily_loss_limit_usd: number; max_entry_cost_pct: number };
  quote_requests: number;
};
const money = (v = 0) => `$${v.toFixed(2)}`;
const signed = (v = 0) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
const reason = (v = '') => v.startsWith('STOP_LOSS') ? 'Стоп — нетна загуба' : v.startsWith('TAKE_PROFIT') ? 'Достигната нетна цел' : v === 'PROFIT_TRAIL_NET' ? 'Защита на печалбата' : 'Максимално време';

export default function AstraBrainPanel({ data }: { data?: AstraSnapshot }) {
  const stale = !data || Date.now() - data.updated_at > 15_000;
  const positive = (data?.stats.realized_pnl || 0) >= 0;
  return <section aria-label="Astra 6 Brain" data-testid="astra-brain" className="m-3 overflow-hidden rounded-2xl border border-sky-400/25 bg-gradient-to-br from-sky-950/40 to-[#0a1016] sm:m-4">
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.08] p-4 sm:p-5">
      <div className="flex items-center gap-3"><div className="rounded-xl border border-sky-300/20 bg-sky-400/10 p-2.5 text-sky-200"><Brain className="h-6 w-6" /></div><div><p className="text-[9px] font-bold uppercase tracking-[.18em] text-sky-300">Отделна експериментална стратегия</p><h3 className="mt-1 text-xl font-black text-white">Astra 6 Brain</h3></div></div>
      <span className={`rounded-lg border px-3 py-1.5 text-[10px] font-bold ${stale ? 'border-amber-400/30 text-amber-200' : 'border-emerald-400/25 text-emerald-200'}`}>{stale ? 'Изчаква обновяване' : 'Активен тест · не е реална търговия'}</span>
    </div>
    <div className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-4 sm:p-5">
      {[
        ['Капитал с отворените позиции', money(data?.stats.equity ?? 500)],
        ['Свободен капитал', money(data?.stats.available_usd ?? 500)],
        ['Затворени сделки', `${data?.stats.trades ?? 0} · ${data?.stats.wins ?? 0} печеливши`],
        ['Реализиран резултат', money(data?.stats.realized_pnl ?? 0)],
      ].map(([label, value], i) => <div key={label} className="min-w-0 rounded-xl border border-white/[.06] bg-black/15 p-3"><div className="text-[10px] leading-4 text-slate-400">{label}</div><div className={`mt-2 break-words text-base font-extrabold ${i === 3 ? positive ? 'text-emerald-300' : 'text-red-300' : 'text-white'}`}>{value}</div></div>)}
    </div>
    <div className="flex flex-wrap gap-x-5 gap-y-2 px-4 pb-4 text-[11px] text-slate-300 sm:px-5">
      <span className="flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5 text-sky-300" /> Стоп −2% нето · цели +10 / +15 / +20%</span>
      <span className="flex items-center gap-1.5"><Zap className="h-3.5 w-3.5 text-sky-300" /> До 3 позиции · $25–$75 на вход</span>
      <span className="flex items-center gap-1.5"><Timer className="h-3.5 w-3.5 text-sky-300" /> Проверени токени: {data?.diagnostics.scanned ?? 0} · кандидати: {data?.diagnostics.qualified ?? 0}</span>
    </div>
    <div className="mx-4 mb-4 rounded-xl border border-sky-400/15 bg-sky-400/[.04] p-3 text-[11px] leading-5 text-sky-100 sm:mx-5">{data?.diagnostics.message || 'Подготвя се отделният процес за котировки.'}</div>
    <div className="grid gap-3 px-4 pb-4 sm:px-5 lg:grid-cols-3">
      {(data?.book.positions || []).map(p => {
        const age = Math.max(0, (Date.now() - p.quote_at) / 1000);
        return <article key={p.id} className="rounded-xl border border-white/10 bg-black/20 p-4">
          <div className="flex items-center justify-between gap-2"><strong className="break-all text-sm text-white">${p.symbol}</strong><span className="text-xs text-slate-400">{money(p.notional_usd)}</span></div>
          <div className={`mt-2 text-lg font-black ${p.pnl_pct >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{signed(p.pnl_pct)} <span className="text-xs">({money(p.pnl_usd)})</span></div>
          <div className="mt-2 text-[10px] leading-5 text-slate-400">Нетна цел: +{p.target_net_pct}% · стоп: −2%<br />Вход + изход при покупката: {signed(p.entry_roundtrip_pnl_pct)}</div>
          <p className={`mt-2 text-[10px] ${age > 8 ? 'text-amber-200' : 'text-slate-400'}`}>{age > 8 ? 'Остаряла оценка' : 'Jupiter котировка'} · преди {Math.round(age)} сек.</p>
        </article>;
      })}
      {!data?.book.positions?.length && <p className="col-span-full py-2 text-xs text-slate-400">Няма отворени позиции. Не се създава сделка без подходящ сигнал, потвърден route и допустими разходи.</p>}
    </div>
    <details className="border-t border-white/[.06] px-4 py-3 sm:px-5">
      <summary className="cursor-pointer text-xs font-bold text-white">Сделки и разходи на Astra ({data?.stats.trades ?? 0})</summary>
      <div className="mt-3 space-y-2">
        {(data?.book.history || []).slice(0, 30).map(p => <div key={p.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-black/20 p-3 text-xs"><div><b className="text-white">${p.symbol}</b><div className="mt-1 text-[10px] text-slate-400">{reason(p.exit_reason)} · {money(p.notional_usd)}</div></div><div className={`font-bold ${p.pnl_usd >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{money(p.pnl_usd)} · {signed(p.pnl_pct)}</div></div>)}
        {!data?.book.history?.length && <p className="text-xs text-slate-400">Все още няма затворени сделки. Няма измислена начална успеваемост.</p>}
      </div>
    </details>
    <p className="border-t border-white/[.06] px-4 py-3 text-[10px] leading-5 text-slate-400 sm:px-5"><b className="text-slate-200">Как се смята:</b> Jupiter котировки с включени AMM такси + допускане 0.10% изпълнителен разход на страна + бюджет за мрежа/приоритет + резерв за нов token акаунт. Максимален прогнозен разход при вход: 1.25%. Дневен лимит на този тест: $25. Стопът не гарантира изпълнение точно на −2%. Това не е изпълнен swap или гаранция за печалба. Сумите са в USDC, показани с $.</p>
  </section>;
}
