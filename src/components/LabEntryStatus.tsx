import { entryRejectionRows, labEntryView, type LabEntryViewDiagnostics, type StrategyLifecycle } from '../lib/labStrategyView';

type Props = {
  book: {
    id: string; starting_balance: number; balance: number; portfolio_group?: string;
    runtime_compatibility?: { status?: string }; strategy_lifecycle?: StrategyLifecycle;
    entry_diagnostics?: LabEntryViewDiagnostics;
  };
  backendAvailable: boolean;
};

export default function LabEntryStatus({ book, backendAvailable }: Props) {
  const view = labEntryView(book, backendAvailable);
  const diagnostics = book.entry_diagnostics;
  const screen = diagnostics?.market_screen;
  const market = entryRejectionRows(screen?.primary_rejections);
  const defense = entryRejectionRows(diagnostics?.defensive_entry?.primary_rejections);
  const evidence = entryRejectionRows(diagnostics?.promoted_block_reasons);
  return <div data-testid="lab-entry-status" className="max-w-64 text-[9px] leading-4">
    <span className={view.costLimited ? 'text-amber-200' : 'text-slate-400'}>{view.status}</span>
    {view.detail && <p className="mt-1 text-[8px] text-slate-500">{view.detail}</p>}
    {backendAvailable && screen && <details className="mt-2 text-slate-500" data-testid="funded-entry-funnel">
      <summary className="cursor-pointer text-cyan-200">Защо няма вход? Пазарни кандидати {screen.passed ?? '—'}/{screen.evaluated ?? '—'}</summary>
      <p className="mt-1">Последно сканиране{diagnostics?.at ? ` ${new Date(diagnostics.at).toLocaleTimeString('bg-BG')}` : ''}; не са брой сделки. Вече отворени токени, пропуснати преди проверките: {screen.already_open ?? '—'}.</p>
      <p className="mt-1">Преди подбора: {screen.feed_rows ?? '—'} реда от feed; {screen.unusable_feed_rows ?? '—'} без валидна идентичност, цена, минимална ликвидност или свежест; {screen.duplicate_usable_rows ?? '—'} повторения на точния pool.</p>
      <p className="mt-1">Първа причина за пазарен отказ (всеки токен се брои веднъж):</p>
      {market.map(row => <p key={row.code}>{row.label}: {row.count}</p>)}
      {!market.length && <p>Няма пазарни откази в тази снимка.</p>}
      <p className="mt-1">След пазарния подбор — първи отказ в защитния слой:</p>
      {defense.map(row => <p key={row.code}>{row.label}: {row.count}</p>)}
      <p className="mt-1">Последващи проверки на поток, качество, риск и разходи:</p>
      {evidence.map(row => <p key={row.code}>{row.label}: {row.count}</p>)}
      <p className="mt-1">Неизвестна мрежова цена: {diagnostics?.network_cost_rejected ?? '—'}; цена отказана: {diagnostics?.price_verification_rejected ?? '—'}; цена чака проверка: {diagnostics?.price_crosscheck_pending ?? '—'}; пауза за повторен вход: {diagnostics?.cooldown_rejected ?? '—'}; моделирани разходи/достъпност отказани: {diagnostics?.cost_rejected ?? '—'}.</p>
      <p className="mt-1">Допустими за избор след всички проверки: {diagnostics?.affordable_candidates ?? '—'}. След избора има повторна проверка; допустим кандидат не означава изпълнен вход.</p>
      <p className="mt-1">Отказ при повторната проверка: {diagnostics?.commit_recheck_rejected ?? 0}; отворени в това сканиране: {diagnostics?.opened_this_refresh ?? '—'}.</p>
    </details>}
  </div>;
}
