import { ExternalLink } from 'lucide-react';
import { moneyOrUnavailable, percentageOrUnavailable } from '../lib/paperDashboardState';

type HistoryTrade = {
  id?: string; trade_no?: number; strategy_id?: string; strategy_name?: string;
  symbol: string; address: string; pairAddress?: string; dex_url?: string;
  opened_at: number; closed_at?: number; entry_price?: number; exit_price?: number;
  execution_entry_price?: number; execution_exit_price?: number; notional_usd: number;
  pnl_usd?: number; pnl_pct?: number; balance_before?: number; balance_after?: number; exit_reason?: string;
};

export default function PaperPortfolioHistory({ trades, total, loaded, promoted, onSelectAddress, formatPrice, formatTime }: {
  trades: HistoryTrade[]; total?: number; loaded: boolean; promoted: boolean;
  onSelectAddress: (address: string) => void; formatPrice: (price: number) => string; formatTime: (stamp: number) => string;
}) {
  const price = (value?: number) => typeof value === 'number' && Number.isFinite(value) && value > 0 ? formatPrice(value) : '—';
  return <section data-testid="paper-portfolio-history" className="mt-4 overflow-hidden rounded-3xl border border-white/10 bg-[#0b0e11]">
    <div className="flex items-center justify-between gap-3 border-b border-white/[0.07] p-4">
      <div><div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">PAPER HISTORY</div><h2 className="mt-1 text-lg font-black text-white">Сделки</h2><div className="mt-1 text-[9px] text-slate-500">{promoted ? 'Главен PAPER портфейл' : 'Отделна PAPER сметка'}</div></div>
      <div className="text-[9px] text-slate-600">{loaded ? total ?? 0 : '—'} затворени</div>
    </div>
    <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left">
      <thead><tr className="border-b border-white/[0.06] text-[8px] font-black uppercase tracking-[0.14em] text-slate-700">
        {['Coin', 'Вход', 'Изход', 'Размер', 'PnL', promoted ? 'Баланс на стратегията' : 'Баланс на сметката', 'Причина', ''].map((label, index) => <th key={index} className="px-4 py-3">{label}</th>)}
      </tr></thead>
      <tbody>{trades.length === 0 ? <tr><td colSpan={8} className="px-4 py-8 text-center text-xs text-slate-600">{loaded ? 'Няма затворени PAPER сделки.' : 'Историята още не е заредена.'}</td></tr> : trades.slice(0, 100).map((trade, index) => {
        const dexUrl = trade.dex_url || (trade.pairAddress ? `https://dexscreener.com/solana/${encodeURIComponent(trade.pairAddress)}` : '');
        return <tr key={trade.id ?? `${trade.strategy_id}-${trade.trade_no ?? trade.opened_at}-${index}`} onClick={() => onSelectAddress(trade.address)} className="cursor-pointer border-b border-white/[0.04] text-xs hover:bg-white/[0.02]">
          <td className="px-4 py-3"><div className="font-black text-white">#{trade.trade_no ?? '—'} · ${trade.symbol}</div>{trade.strategy_name && <div className="mt-1 text-[9px] text-cyan-200/80">{trade.strategy_name}</div>}</td>
          <td className="px-4 py-3"><div className="text-slate-300">{price(trade.execution_entry_price ?? trade.entry_price)}</div><div className="mt-1 text-[9px] text-slate-600">{formatTime(trade.opened_at)}</div></td>
          <td className="px-4 py-3"><div className="text-slate-300">{price(trade.execution_exit_price ?? trade.exit_price)}</div><div className="mt-1 text-[9px] text-slate-600">{trade.closed_at ? formatTime(trade.closed_at) : '—'}</div></td>
          <td className="px-4 py-3 text-slate-400">{moneyOrUnavailable(trade.notional_usd)}</td>
          <td className={`px-4 py-3 font-black ${trade.pnl_usd == null ? 'text-slate-500' : trade.pnl_usd >= 0 ? 'text-emerald-300' : 'text-red-300'}`}><div>{moneyOrUnavailable(trade.pnl_usd, true)}</div><div className="mt-1 text-[9px]">{percentageOrUnavailable(trade.pnl_pct)}</div></td>
          <td className="px-4 py-3"><div className="text-slate-500">{moneyOrUnavailable(trade.balance_before)}</div><div className="mt-1 font-black text-white">→ {moneyOrUnavailable(trade.balance_after)}</div></td>
          <td className="px-4 py-3 text-slate-400">{trade.exit_reason || '—'}</td>
          <td className="px-4 py-3">{dexUrl ? <a onClick={event => event.stopPropagation()} href={dexUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1.5 text-[9px] font-black text-slate-400 hover:text-white">CHART <ExternalLink className="h-3 w-3" /></a> : '—'}</td>
        </tr>;
      })}</tbody>
    </table></div>
  </section>;
}
