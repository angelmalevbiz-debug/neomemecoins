import { labEntryView, type LabEntryViewDiagnostics, type StrategyLifecycle } from '../lib/labStrategyView';

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
  return <div data-testid="lab-entry-status" className="max-w-64 text-[9px] leading-4">
    <span className={view.costLimited ? 'text-amber-200' : 'text-slate-400'}>{view.status}</span>
    {view.detail && <p className="mt-1 text-[8px] text-slate-500">{view.detail}</p>}
  </div>;
}
