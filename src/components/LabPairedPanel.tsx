type Arm = 'CONTROL' | 'EARLY' | 'PROTECT';
type Exit = { symbol: string; pnl_usd: number; pnl_pct: number; reason: string; closed_at: number; episode_id: string };
type Stats = {
  trades: number; wins: number; losses: number; breakeven: number; win_rate: number;
  profit_factor: number | null; gross_profit: number; gross_loss: number; realized_pnl: number;
  equity: number; mean_pnl_usd: number | null; mean_roundtrip_cost_pct: number | null;
  average_hold_seconds: number | null; stop_overshoots: number; valuation_stale: boolean;
};
type Comparison = { paired_episodes: number; mean_delta_usd: number | null; total_delta_usd: number; improved: number; worse: number; equal: number; independent_tokens: number };
type Group = {
  id: string; name: string; parent: string; max_cost_pct: number;
  arms: { arm: Arm; label: string; stats: Stats; position: { symbol: string; pnl_pct: number; notional_usd: number; quote_status: string } | null; last_exits: Exit[] }[];
  comparisons: Record<'EARLY' | 'PROTECT', Comparison>;
  diagnostics: { checked_at?: number; eligible?: number; rejections?: Record<string, number> };
};
export type LabPairedSnapshot = {
  version: string; status: string; started_at: number; updated_at: number;
  paper_only: boolean; execution_basis: string; groups: Group[]; error?: string | null;
};

const statuses: Record<string, string> = { online: 'Работи', starting: 'Стартира', degraded: 'Забавени данни', stale: 'Остарели данни', stopped: 'Спрян' };
const reasons: Record<string, string> = {
  parent_signal: 'Няма подходящ вход', stale_or_invalid_feed: 'Остарели или непълни пазарни данни',
  network_price_unknown: 'Непотвърден мрежов разход', age_cap: 'Твърде стар токен', overextended: 'Прекалено движение за 5 минути',
  flow_activity: 'Недостатъчно активни покупки', negative_observed_flow: 'Продажбите надделяват',
  stale_flow_data: 'Остарял поток от сделки', reference_unavailable: 'Липсва независима ценова проверка',
  roundtrip_cost: 'Входът и изходът са твърде скъпи', balance: 'Недостатъчен тестов баланс',
  cooldown: 'Изчаква нов сигнал след предишната сделка', paired_episode_open: 'Следи текущото сравнение',
  experiment_daily_limit: 'Достигнат лимит на тестовата загуба', price_reference_expired: 'Ценовата проверка е остаряла',
  price_source_disagreement: 'Ценовите източници се разминават', price_unavailable: 'Непотвърдена цена',
  price_identity_mismatch: 'Несъответстващ токен или пул', invalid_identity: 'Невалиден токен или пул',
};
const exits: Record<string, string> = {
  STOP_LOSS_3_NET: 'Стоп', TAKE_PROFIT_10_NET: 'Цел +10% нето', MAX_HOLD_60: 'Максимално време',
  FLOW_DETERIORATION: 'Потокът отслабна', NET_PROFIT_PROTECTION: 'Защита на печалбата', STALE_DATA_RECOVERY: 'Възстановени ценови данни',
};
const number = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) ? value : 0;
const money = (value: number | null | undefined) => `${number(value) < 0 ? '−' : '+'}$${Math.abs(number(value)).toFixed(2)}`;
const color = (value: number | null | undefined) => number(value) > 0 ? 'text-emerald-300' : number(value) < 0 ? 'text-red-300' : 'text-slate-300';

export default function LabPairedPanel({ data }: { data?: LabPairedSnapshot }) {
  if (!data) return null;
  const stale = Date.now() - number(data.updated_at) > 20_000 || data.status === 'stale';
  const status = stale ? 'stale' : data.status;
  return <section data-testid="paired-lab" className="m-4 overflow-hidden rounded-2xl border border-sky-400/20 bg-sky-400/[0.025]">
    <header className="border-b border-white/[0.07] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h3 className="text-base font-bold text-white">Сравнителни тестове на изходите</h3>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-300">Три варианта получават еднакъв токен, момент, размер и входна цена. Различава се само изходът. Следващ вход има чак след приключване и на трите.</p>
        </div>
        <span className={`shrink-0 rounded-lg border px-3 py-1.5 text-xs font-bold ${status === 'online' ? 'border-sky-400/25 text-sky-200' : 'border-amber-400/25 text-amber-200'}`}>{statuses[status] || 'Изчакване'}</span>
      </div>
      <p className="mt-3 text-xs leading-5 text-amber-100">Само симулация с оценени разходи — не Jupiter изпълнения. Старите 33 стратегии и Astra са отделно и не са променени. Тези тестове не доказват доходност.</p>
      <div className="mt-2 text-[11px] leading-5 text-slate-400">Защита: след +2% нето → праг +0,5%; след +3% → +1%; след +5% → поне +2,5%. Това са сигнали за изход, не гарантирани цени.</div>
    </header>
    {data.groups.map(group => {
      const paired = group.comparisons?.PROTECT;
      const rejected = Object.entries(group.diagnostics?.rejections || {}).sort((a,b) => b[1]-a[1]).slice(0,2);
      return <article key={group.id} data-testid={`paired-${group.id}`} className="border-b border-white/[0.07] last:border-0">
        <div className="flex flex-wrap items-start justify-between gap-2 px-4 pb-2 pt-4">
          <div><h4 className="text-sm font-bold text-white">{group.name}</h4>
            <p className="mt-1 text-[11px] leading-5 text-slate-400">Максимален разход за вход + изход: {group.max_cost_pct.toFixed(2)}%. {group.id === 'MICRO_BREAKOUT_V3' ? 'Възраст до 12 часа, поне 5 сделки и 3 портфейла, покупки ≥ $150, движение до +18%.' : group.id === 'ULTRA_PRECISION_V3' ? 'Отказ при наблюдаван отрицателен поток; движение до +18%. Липсващите данни не се броят за отрицателен поток.' : 'Контрол на изходите върху текущия Lab Order Flow сигнал.'}</p>
          </div>
          <div className="text-[11px] text-slate-300">{paired?.paired_episodes ?? 0} завършени сравнения · {paired?.independent_tokens ?? 0} токена</div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-xs">
            <thead className="text-[10px] text-slate-400"><tr><th className="px-4 py-2">Вариант</th><th className="px-3 py-2">Нетен резултат</th><th className="px-3 py-2">Сделки</th><th className="px-3 py-2">Успеваемост</th><th className="px-3 py-2">Печалби / загуби</th><th className="px-3 py-2">Спрямо контрола*</th><th className="px-3 py-2">Позиция</th></tr></thead>
            <tbody>{group.arms.map(arm => {
              const s=arm.stats;const comparison=arm.arm==='CONTROL' ? null : group.comparisons[arm.arm];
              return <tr key={arm.arm} className="border-t border-white/[0.05]">
                <td className="px-4 py-3 font-semibold text-white">{arm.label}</td>
                <td className={`px-3 py-3 font-semibold ${color(s.realized_pnl)}`}>{money(s.realized_pnl)}</td>
                <td className="px-3 py-3 text-slate-300">{s.trades}<div className="mt-1 text-[10px] text-slate-400">{s.wins} печ. / {s.losses} заг.</div></td>
                <td className="px-3 py-3 text-white">{s.trades ? `${s.win_rate.toFixed(1)}%` : '—'}</td>
                <td className="px-3 py-3 text-slate-300">{s.profit_factor != null ? s.profit_factor.toFixed(2) : s.gross_profit > 0 ? 'Без загуби' : '—'}</td>
                <td className={`px-3 py-3 font-semibold ${color(comparison?.total_delta_usd)}`}>{comparison?.paired_episodes ? money(comparison.total_delta_usd) : '—'}</td>
                <td className="px-3 py-3 text-slate-300">{arm.position ? <><span className="font-semibold text-white">{arm.position.symbol}</span><div className={`mt-1 text-[10px] ${arm.position.quote_status === 'fresh' ? color(arm.position.pnl_pct) : 'text-amber-200'}`}>{arm.position.quote_status === 'fresh' ? `${arm.position.pnl_pct.toFixed(2)}% · $${arm.position.notional_usd.toFixed(2)}` : 'Остаряла оценка'}</div></> : 'Изчаква'}</td>
              </tr>;
            })}</tbody>
          </table>
        </div>
        <div className="px-4 pb-4 pt-2">
          <p className="text-[11px] leading-5 text-slate-400">{rejected.map(([reason,count]) => `${reasons[reason] || 'Изчаква проверка'}: ${count}`).join(' · ') || 'Събира нови наблюдения.'}</p>
          <details className="mt-2 text-xs text-slate-300"><summary className="cursor-pointer py-1 text-sky-200">Последни изходи и качество на извадката</summary>
            <p className="mt-2 leading-5 text-slate-400">Сравняваме само завършени тройки от една група. Един токен може да се повтори, затова броят сделки не е брой независими доказателства. Няма автоматично прехвърляне към основния engine.</p>
            <div className="mt-3 grid gap-3 lg:grid-cols-3">{group.arms.map(arm => <div key={arm.arm} className="min-w-0 rounded-xl border border-white/[0.07] p-3"><div className="font-semibold text-white">{arm.label}</div>
              <div className="mt-1 text-[11px] text-slate-400">Стопове под −3,5%: {arm.stats.stop_overshoots} · Среден разход: {arm.stats.mean_roundtrip_cost_pct == null ? '—' : `${arm.stats.mean_roundtrip_cost_pct.toFixed(2)}%`}</div>
              {arm.last_exits.length === 0 ? <p className="mt-2 text-slate-400">Още няма затворени сделки.</p> : arm.last_exits.slice(0,5).map(exit => <div key={exit.episode_id} className="mt-2 border-t border-white/[0.05] pt-2"><div className="flex justify-between gap-2"><span className="break-all">{exit.symbol}</span><span className={`whitespace-nowrap ${color(exit.pnl_usd)}`}>{money(exit.pnl_usd)}</span></div><div className="mt-1 text-[11px] text-slate-400">{exits[exit.reason] || 'Изход'} · {new Date(exit.closed_at).toLocaleTimeString('bg-BG')}</div></div>)}
            </div>)}</div>
          </details>
        </div>
      </article>;
    })}
    <footer className="px-4 py-3 text-[11px] leading-5 text-slate-400">* Общата разлика е спрямо контролния вариант само за завършените сравнения. Начало: {new Date(data.started_at).toLocaleString('bg-BG')} · Нови, отделни тестови баланси по $500. Изчакване за същия токен: 20 минути.</footer>
  </section>;
}
