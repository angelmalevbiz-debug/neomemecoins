import { moneyOrUnavailable } from '../lib/paperDashboardState';

type ExitResearchProfile = {
  id: string; observed_closes: number; wins: number; win_rate_pct: number | null;
  modeled_net_usd: number; median_hold_minutes: number | null; censored: number; pending: number;
  eligible_paired_closes: number; stressed_mean_net_usd: number | null; status: string;
  average_win_usd?: number | null; average_loss_usd?: number | null; profit_factor?: number | null;
  unresolved_last_mark_net_usd?: number; unresolved_unpriced?: number;
};
export type PaperExitResearchSnapshot = {
  version: string; status: string; error?: string | null; active_episodes: number; observations: number;
  resumed_episodes: number; full_entry_episodes: number; paired_mature_entries: number;
  unique_pools: number; utc_days: number; coverage_complete: boolean;
  censored_or_unresolved_mature_entries: number; capacity_refusals: number;
  review_gate: { paired_entries: number; unique_pools: number; utc_days: number };
  profiles: ExitResearchProfile[];
};

const names: Record<string, string> = {
  BASELINE_30_LOCK4: 'Референтен изход v5 · цел +$30 / защита от +$4',
  QUICK_6_LOCK2: 'Бърз вариант · цел +$6 / защита от +$2',
  RUNNER_LOCK4: 'Следване на движението · без горна цел / защита от +$4',
};

export default function PaperExitResearchPanel({ data, error, connected }: {
  data?: PaperExitResearchSnapshot; error?: string; connected: boolean;
}) {
  if (!data && !error) return null;
  return <section data-testid="paper-exit-research" className="border-b border-cyan-300/10 px-4 py-4 text-[10px] leading-5 text-slate-300">
    <h3 className="font-black text-cyan-200">Развитие на изходите · търсене на по-бърза нетна печалба</h3>
    <p className="mt-1 text-amber-100">Сравнение, не сделки или допълнителен баланс. Трите варианта наблюдават еднакви входове и бъдещи цени с такси, impact и забавяне. Няма автоматично включване и няма доказана печалба.</p>
    <p className="mt-1 text-slate-400">Нетно е сбор в USD, не процент печалба на портфейла. Моделните изходи не са реално отворени позиции за затваряне. Референтният v5 е замразен; текущите SCALP / QUICK / SWING / HOLDER финансови правила са показани при всяка стратегия и не се сменят от това сравнение.</p>
    {!connected && <p className="mt-2 text-amber-200">Backend не е свързан: показаните наблюдения не са актуални.</p>}
    {(error || data?.error) && <p className="mt-2 text-red-300">Изследването е спряно или има пропуски: {error || data?.error}. Това не разрешава нови сделки.</p>}
    {data && <>
      <p className="mt-2">{data.active_episodes} активни наблюдения · {data.observations} пресни ценови точки · {data.full_entry_episodes} проследени от нов качествен вход · {data.resumed_episodes} заварени позиции (изключени от оценката за подобрение).</p>
      <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[620px] text-left">
        <thead className="text-slate-500"><tr><th>Вариант</th><th>Моделни изходи</th><th>Нетно</th><th>Време*</th><th>Чака / непълен</th></tr></thead>
        <tbody>{data.profiles.map(profile => <tr key={profile.id} className="border-t border-white/5">
          <td className="py-2 pr-3">{names[profile.id] ?? profile.id}<div className="text-[9px] text-slate-500">{profile.status === 'CANDIDATE_FOR_MANUAL_REVIEW_NOT_VALIDATED' ? 'Кандидат за преглед — не е валидиран' : 'Няма прието подобрение'}</div></td>
          <td>{profile.observed_closes} · {profile.win_rate_pct == null ? 'WR —' : `${profile.win_rate_pct.toFixed(1)}% WR`}</td>
          <td>{profile.observed_closes ? moneyOrUnavailable(profile.modeled_net_usd,true) : '—'}
            {profile.unresolved_last_mark_net_usd != null && <div className="text-[9px] text-slate-500">Неприключили: последен сбор {moneyOrUnavailable(profile.unresolved_last_mark_net_usd,true)} (не е прибрано){profile.unresolved_unpriced ? ` · без цена ${profile.unresolved_unpriced}` : ''}</div>}
            {profile.observed_closes > 0 && <div className="text-[9px] text-slate-500">Средно печалба {moneyOrUnavailable(profile.average_win_usd)} / загуба {moneyOrUnavailable(profile.average_loss_usd)} · PF {profile.profit_factor == null ? '—' : profile.profit_factor.toFixed(2)}</div>}
          </td>
          <td>{profile.median_hold_minutes == null ? '—' : `${profile.median_hold_minutes.toFixed(1)} мин`}</td>
          <td>{profile.pending} / {profile.censored}</td>
        </tr>)}</tbody>
      </table></div>
      <p className="mt-2 text-slate-500">*Медиана от началото на наблюдението; за заварените позиции не е цялото времетраене на сделката. Изходът се оценява на следваща прясна променена цена поне 2 секунди след сигнала — не на удобния праг и не с изпълнима wallet котировка. Неприключилите наблюдения не са печеливши сделки.</p>
      <p className="mt-2">Преди преглед: {data.paired_mature_entries}/{data.review_gate.paired_entries} завършени съпоставими нови входа · {data.unique_pools}/{data.review_gate.unique_pools} различни pool-а · {data.utc_days}/{data.review_gate.utc_days} UTC дни; допълнителни разходи и сравнение със сегашния изход. {data.coverage_complete ? 'Засега няма установени пропуски в зрелите сравнения.' : 'Има пропуски — приемане на вариант е блокирано.'}</p>
      {(data.censored_or_unresolved_mature_entries > 0 || data.capacity_refusals > 0) && <p className="text-amber-200">Непълни зрели сравнения {data.censored_or_unresolved_mature_entries} · отказани наблюдения по капацитет {data.capacity_refusals}. Те не са скрити или отчетени като нулева загуба.</p>}
    </>}
  </section>;
}
