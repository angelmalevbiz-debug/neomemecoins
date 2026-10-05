import React from 'react';
import { Bot, Wallet, TrendingUp, AlertCircle, Play, Square, RefreshCw, Radio } from 'lucide-react';
import { BotSettings, TradeLog } from '../types';

interface HeaderProps {
  settings: BotSettings;
  setSettings: React.Dispatch<React.SetStateAction<BotSettings>>;
  tradeLogs: TradeLog[];
  resetDemo: () => void;
}

export default function DashboardHeader({ settings, setSettings, tradeLogs, resetDemo }: HeaderProps) {
  // Compute metrics from trade logs
  const closedTrades = tradeLogs.filter(log => log.status === 'CLOSED' || log.type.includes('SELL') || log.type.includes('RUG'));
  const profitableTrades = closedTrades.filter(log => (log.solProfit || 0) > 0);
  const winRate = closedTrades.length > 0 
    ? Math.round((profitableTrades.length / closedTrades.length) * 100) 
    : 0;
  
  const totalProfitSol = tradeLogs.reduce((acc, log) => acc + (log.solProfit || 0), 0);

  const toggleAutoTrade = () => {
    setSettings(prev => ({
      ...prev,
      autoTrade: !prev.autoTrade
    }));
  };

  const togglePhantom = async () => {
    if (settings.phantomConnected) {
      setSettings(prev => ({
        ...prev,
        phantomConnected: false,
        walletBalanceSol: 0.0,
        realWalletAddress: undefined
      }));
      return;
    }

    try {
      const provider = (window as any).solana;
      if (provider && typeof provider.connect === 'function') {
        const response = await provider.connect();
        const address = response.publicKey.toString();
        
        let realBalance = 0;
        try {
          const balanceRes = await fetch('https://api.mainnet-beta.solana.com', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              jsonrpc: '2.0',
              id: 1,
              method: 'getBalance',
              params: [address]
            })
          });
          const balanceData = await balanceRes.json();
          if (balanceData.result && balanceData.result.value !== undefined) {
            realBalance = balanceData.result.value / 1e9;
          }
        } catch (e) {
          console.error("Failed to fetch balance from Solana mainnet RPC:", e);
        }

        setSettings(prev => ({
          ...prev,
          phantomConnected: true,
          walletBalanceSol: realBalance,
          realWalletAddress: address
        }));
      } else {
        // Fallback for simulation if Phantom is not installed or in cross-origin iframe
        console.log("Phantom not detected or blocked by iframe cross-origin sandbox. Initializing simulated premier account.");
        setSettings(prev => ({
          ...prev,
          phantomConnected: true,
          walletBalanceSol: 5.20,
          realWalletAddress: 'DEvWaf78yy9gy6yP7V88g7v98yU67yYHgHg11'
        }));
      }
    } catch (err: any) {
      console.error("Phantom Wallet Connection Error:", err);
      // Fallback on error (e.g., user rejected, or cross-origin restriction in iframe)
      setSettings(prev => ({
        ...prev,
        phantomConnected: true,
        walletBalanceSol: 5.20,
        realWalletAddress: 'DEvWaf78yy9gy6yP7V88g7v98yU67yYHgHg11'
      }));
    }
  };

  return (
    <div className="bg-[#0A0B0E] border-2 border-white/10 p-6 text-slate-100 shadow-[0_0_30px_rgba(0,0,0,0.8)] relative">
      <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-6">
        
        {/* Title & Brand */}
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 bg-[#00FFA3] rounded-sm flex items-center justify-center shrink-0 relative shadow-[0_0_15px_rgba(0,255,163,0.3)]">
            <div className="w-7 h-7 border-2 border-black rotate-45"></div>
            <span className="absolute -top-1 -right-1 flex h-3 w-3">
              <span className="animate-ping absolute inline-flex h-full w-full rounded bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex rounded h-3 w-3 bg-[#00FFA3]"></span>
            </span>
          </div>
          <div>
            <h1 className="text-2xl font-black tracking-tighter text-white flex items-center gap-2">
              SOLANA MEME AI SNIPER <span className="text-xs font-mono font-bold bg-white/10 text-[#00FFA3] px-2 py-0.5 rounded-none border border-white/20">EX-1000 PREMIER</span>
            </h1>
            <p className="text-[10px] uppercase tracking-[0.2em] text-[#00FFA3] font-bold mt-1 flex items-center gap-1.5">
              <Radio className="h-3.5 w-3.5 text-[#00FFA3] animate-pulse" />
              HIGH-FREQUENCY AUTOPILOT MEME COIN ANALYZER & COPY TRADER
            </p>
          </div>
        </div>

        {/* Quick Actions & Wallet */}
        <div className="flex flex-wrap items-center gap-3 w-full lg:w-auto">
          {/* Phantom Wallet Status */}
          <button 
            onClick={togglePhantom}
            className={`flex items-center gap-2 text-xs font-mono px-4 py-2.5 rounded-none border transition-all ${
              settings.phantomConnected 
                ? 'bg-purple-950/40 border-purple-800 text-purple-300 hover:bg-purple-900/30 shadow-[0_0_15px_rgba(147,51,234,0.15)]' 
                : 'bg-white/5 border-white/10 text-slate-400 hover:border-white/20'
            }`}
          >
            <Wallet className={`h-4 w-4 ${settings.phantomConnected ? 'text-purple-400' : 'text-slate-500'}`} />
            {settings.phantomConnected 
              ? `Connected: ${settings.walletBalanceSol.toFixed(3)} SOL ($${(settings.walletBalanceSol * 150).toFixed(2)})` 
              : 'Connect Phantom Wallet'}
          </button>

          {/* Autopilot Status Control */}
          <button 
            onClick={toggleAutoTrade}
            disabled={!settings.phantomConnected}
            className={`flex items-center gap-2 text-xs font-black px-4 py-2.5 rounded-none transition-all shadow-md ${
              !settings.phantomConnected
                ? 'bg-slate-900/40 border border-white/5 text-slate-600 cursor-not-allowed'
                : settings.autoTrade 
                  ? 'bg-rose-600 hover:bg-rose-500 text-white' 
                  : 'bg-[#00FFA3] hover:bg-[#00FFA3]/90 text-black shadow-[0_0_15px_rgba(0,255,163,0.2)]'
            }`}
          >
            {settings.autoTrade ? (
              <>
                <Square className="h-4 w-4 fill-white shrink-0" />
                СПРИ АВТОПИЛОТА
              </>
            ) : (
              <>
                <Play className="h-4 w-4 fill-black animate-pulse shrink-0" />
                ПУСНИ АВТОПИЛОТ
              </>
            )}
          </button>

          {/* Reset Demo Button */}
          <button 
            onClick={resetDemo}
            title="Reset simulation database"
            className="p-2.5 bg-white/5 border border-white/10 rounded-none text-slate-400 hover:text-white hover:border-white/20 transition-all"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Stats Ribbon */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mt-6 pt-6 border-t border-white/10">
        
        {/* Total Sol Profit */}
        <div className="bg-white/[0.02] border border-white/10 rounded-none p-4">
          <p className="text-[10px] uppercase tracking-wider text-white/50 font-medium">Обща Печалба (SOL / USD)</p>
          <div className="flex flex-col mt-1">
            <span className={`text-xl font-bold font-mono leading-none ${totalProfitSol >= 0 ? 'text-[#00FFA3]' : 'text-[#FF3D00]'}`}>
              {totalProfitSol >= 0 ? '+' : ''}{totalProfitSol.toFixed(3)} SOL
            </span>
            <span className={`text-xs font-mono mt-1 ${totalProfitSol >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {totalProfitSol >= 0 ? '+' : ''}${(totalProfitSol * 150).toFixed(2)} USD
            </span>
          </div>
        </div>

        {/* Current Balance */}
        <div className="bg-white/[0.02] border border-white/10 rounded-none p-4">
          <p className="text-[10px] uppercase tracking-wider text-white/50 font-medium">Наличен Баланс (SOL / USD)</p>
          <div className="flex flex-col mt-1">
            <span className="text-xl font-bold font-mono text-white leading-none">
              {settings.walletBalanceSol.toFixed(3)} SOL
            </span>
            <span className="text-xs font-mono text-slate-400 mt-1">
              ${(settings.walletBalanceSol * 150).toFixed(2)} USD
            </span>
          </div>
        </div>

        {/* Win Rate */}
        <div className="bg-white/[0.02] border border-white/10 rounded-none p-4">
          <p className="text-[10px] uppercase tracking-wider text-white/50 font-medium">Успешни Сделки (Win Rate)</p>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-xl font-bold font-mono text-[#00FFA3]">
              {winRate}%
            </span>
            <span className="text-[9px] uppercase font-mono text-white/30">от {closedTrades.length} затворени</span>
          </div>
        </div>

        {/* Trade Counter */}
        <div className="bg-white/[0.02] border border-white/10 rounded-none p-4">
          <p className="text-[10px] uppercase tracking-wider text-white/50 font-medium">Активни / Общо Сделки</p>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-xl font-bold font-mono text-white">
              {tradeLogs.filter(log => log.status === 'ACTIVE').length} / {tradeLogs.length}
            </span>
            <span className="text-[9px] uppercase font-mono text-white/30">симулирани</span>
          </div>
        </div>

      </div>

      {!settings.phantomConnected && (
        <div className="mt-4 bg-amber-500/10 border border-amber-500/20 rounded-none p-3.5 flex items-center gap-2.5 text-xs text-amber-300">
          <AlertCircle className="h-4 w-4 shrink-0 text-amber-400" />
          <span>Свържете симулирания <strong className="text-white font-semibold">Phantom Wallet</strong>, за да активирате автоматичното купуване и продаване на меме коинове с демо средства.</span>
        </div>
      )}
    </div>
  );
}
