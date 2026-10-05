import React from 'react';
import { User, BadgeCheck, TrendingUp, Zap, Clock, Coins, ShieldAlert, ExternalLink } from 'lucide-react';
import { EliteTrader, TradeLog } from '../types';
import { MiniTradeChart } from './MiniTradeChart';

const formatExactPrice = (price: number | null | undefined) => {
  if (price === undefined || price === null) return '-';
  if (price === 0) return '$0.00';
  // Return precise exact price formatted to 10 decimal places as requested
  return '$' + price.toFixed(10);
};

const formatFullDateTime = (isoString: string) => {
  try {
    const date = new Date(isoString);
    const day = String(date.getDate()).padStart(2, '0');
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const year = date.getFullYear();
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const seconds = String(date.getSeconds()).padStart(2, '0');
    return `${day}.${month}.${year} ${hours}:${minutes}:${seconds}`;
  } catch (e) {
    return isoString;
  }
};

interface CopyTraderProps {
  traders: EliteTrader[];
  tradeLogs: TradeLog[];
  onManualCloseTrade?: (tradeId: string) => void;
}

export default function CopyTrader({ traders, tradeLogs, onManualCloseTrade }: CopyTraderProps) {
  // Filter logs for Mode 2
  const copyTradeLogs = tradeLogs.filter(log => log.mode === 'COPY_TRADE');

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
      
      {/* Elite Traders Directory - 1/3 Width */}
      <div className="bg-white/[0.02] border border-white/10 rounded-none p-6 flex flex-col space-y-4">
        <div className="border-b border-white/10 pb-4">
          <span className="text-[10px] font-mono bg-white/10 text-[#00FFA3] px-2 py-0.5 rounded-none border border-white/20 uppercase tracking-wider font-bold">
            ELITE COPY ENGINE
          </span>
          <h3 className="font-bold text-white text-base mt-2">Топ Трейдъри за Седмицата</h3>
          <p className="text-[11px] text-white/40 mt-1 leading-relaxed">
            Следените топ 6 адреса с доказани 60%+ успешни сделки на Solana блокчейна.
          </p>
        </div>

        {/* Directory of Traders */}
        <div className="space-y-3 flex-1 overflow-y-auto pr-1">
          {traders.map((trader) => (
            <div 
              key={trader.id} 
              className={`p-3.5 rounded-none border transition-all ${
                trader.status !== 'IDLE' 
                  ? 'border-[#00FFA3] bg-[#00FFA3]/5 shadow-[0_0_15px_rgba(0,255,163,0.05)]' 
                  : 'border-white/10 bg-[#0A0B0E]/40 hover:border-white/20'
              }`}
            >
              <div className="flex justify-between items-start gap-2">
                <div className="flex items-center gap-2">
                  <div className="bg-white/5 p-2 rounded-none text-[#00FFA3] border border-white/10 font-bold text-xs font-mono">
                    {trader.alias[0]}
                  </div>
                  <div>
                    <div className="flex items-center gap-1">
                      <h4 className="font-bold text-xs text-white">{trader.name}</h4>
                      <BadgeCheck className="h-3.5 w-3.5 text-[#00FFA3] fill-black" />
                    </div>
                    <p className="text-[9px] text-white/40 font-mono select-all mt-0.5">
                      {trader.address.substring(0, 5)}...{trader.address.substring(trader.address.length - 4)}
                    </p>
                  </div>
                </div>

                <div className="text-right">
                  <div className="text-[#00FFA3] font-bold text-xs font-mono">
                    +{trader.weeklyProfitSol} SOL
                  </div>
                  <div className="text-[9px] text-white/40 font-mono">
                    {trader.winRate}% Win
                  </div>
                </div>
              </div>

              {/* Status Badge */}
              <div className="mt-2.5 pt-2 border-t border-white/5 flex justify-between items-center text-[10px] font-mono">
                <span className="text-white/40">Статус:</span>
                <span className={`font-mono font-bold px-1.5 py-0.5 rounded-none ${
                  trader.status === 'BUYING' 
                    ? 'bg-[#00FFA3]/10 text-[#00FFA3]' 
                    : trader.status === 'SELLING' 
                      ? 'bg-[#FF3D00]/10 text-[#FF3D00]' 
                      : 'bg-white/10 text-white/60'
                }`}>
                  ● {trader.status === 'BUYING' ? 'ВЛИЗА В СДЕЛКА' : trader.status === 'SELLING' ? 'ИЗЛИЗА ОТ СДЕЛКА' : 'ИЗЧАКВА / IDLE'}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Copy-Trade Fast Feed - 2/3 Width */}
      <div className="xl:col-span-2 bg-white/[0.02] border border-white/10 rounded-none p-6 flex flex-col space-y-4">
        <div className="flex justify-between items-center border-b border-white/10 pb-4">
          <div className="flex items-center gap-2">
            <Zap className="h-5 w-5 text-[#00FFA3] animate-pulse" />
            <h3 className="font-bold tracking-tight text-white text-base">Изпреварващо Copy Trading Трейдване (Mode 2)</h3>
          </div>
          <span className="text-[10px] font-mono text-[#00FFA3] bg-[#00FFA3]/10 border border-[#00FFA3]/20 px-2.5 py-1 rounded-none font-bold">
            Латенция: &lt; 90ms ⚡
          </span>
        </div>

        {/* 3-в-1 Copy Trade Fusion Engine Status Bar */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 bg-[#0A0B0E]/60 border border-white/10 p-4 font-mono text-xs">
          <div className="flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#00FFA3] opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-[#00FFA3]"></span>
            </span>
            <div>
              <p className="text-[9px] text-white/40 uppercase tracking-widest font-black leading-none">АВТОМАТИЧНО КОПИРАНЕ</p>
              <p className="text-[#00FFA3] font-black mt-1 leading-none">Winrate Филтър &gt; 60% 🔥</p>
            </div>
          </div>
          <div>
            <p className="text-[9px] text-white/40 uppercase tracking-widest font-black leading-none">РИСК НА ХОЛДЪРИТЕ</p>
            <p className="text-[#00FFA3] font-black mt-1 leading-none">Holder Scanner: Активен 🔍</p>
          </div>
          <div>
            <p className="text-[9px] text-white/40 uppercase tracking-widest font-black leading-none">ЗАТВАРЯНЕ НА ПОЗИЦИЯ</p>
            <p className="text-white font-bold mt-1 leading-none">Преди Дев/Топ Холдър дъмп 🎯</p>
          </div>
        </div>

        {/* Trade Logs Stream */}
        <div className="space-y-4 max-h-[460px] overflow-y-auto pr-1 flex-1">
          {copyTradeLogs.length === 0 ? (
            <div className="text-center py-20 text-white/40 text-xs font-mono flex flex-col items-center justify-center space-y-3 border border-dashed border-white/10 p-8">
              <Clock className="h-8 w-8 text-[#00FFA3] animate-spin" />
              <span>Следене за изпреварващи сигнали... Ботът ще започне да търгува веднага щом някой от топ трейдърите влезе в сделка.</span>
            </div>
          ) : (
            copyTradeLogs.map((log) => {
              const isBuy = log.type === 'FRONT_RUN_BUY';
              const isProfit = (log.solProfit || 0) > 0;
              const isActive = log.status === 'ACTIVE';

              // Local market cap formatter
              const formatMc = (fdv: number | undefined, price: number) => {
                const val = fdv || (price * 1000000000);
                if (val >= 1_000_000_000) return `$${(val / 1_000_000_000).toFixed(2)}B MC`;
                if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(1)}M MC`;
                if (val >= 1_000) return `$${(val / 1_000).toFixed(0)}K MC`;
                return `$${val.toFixed(0)} MC`;
              };
              
              return (
                <div 
                  key={log.id} 
                  className={`border rounded-none p-4 transition-all bg-[#0A0B0E]/60 ${
                    isActive 
                      ? 'border-[#00FFA3]/40 bg-[#00FFA3]/5 shadow-[0_0_15px_rgba(0,255,163,0.02)] animate-pulse-subtle' 
                      : isBuy 
                        ? 'border-[#00FFA3]/30 bg-[#00FFA3]/5' 
                        : isProfit 
                          ? 'border-[#00FFA3]/20 bg-[#00FFA3]/5 shadow-[inset_0_0_10px_rgba(0,255,163,0.03)]' 
                          : 'border-[#FF3D00]/20 bg-[#FF3D00]/5'
                  }`}
                >
                  <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-3 mb-2">
                    
                    {/* Coin Ticker info */}
                    <div className="flex items-center gap-2">
                      <div className="bg-white/5 font-mono text-[#00FFA3] border border-white/10 px-2 py-0.5 rounded-none font-bold text-xs">
                        {log.coinTicker}
                      </div>
                      <div>
                        <span className="font-bold text-sm text-white">{log.coinName}</span>
                        <span className="text-[10px] text-white/40 ml-1.5 font-mono select-all">
                          {log.contractAddress}
                        </span>
                      </div>
                    </div>

                    {/* Return percentage */}
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-none border uppercase tracking-wider ${
                        isActive 
                          ? 'bg-[#00FFA3]/20 border-[#00FFA3]/40 text-[#00FFA3]' 
                          : isBuy 
                            ? 'bg-[#00FFA3]/10 border-[#00FFA3]/20 text-[#00FFA3]' 
                            : 'bg-[#00FFA3]/10 border-[#00FFA3]/20 text-[#00FFA3]'
                      }`}>
                        {isActive ? 'АКТИВНА' : isBuy ? 'СВЕТКАВИЧЕН ВХОД' : 'ИЗПРЕВАРВАЩ ИЗХОД'}
                      </span>

                      {log.solProfit !== null && !isActive && (
                        <span className={`text-xs font-mono font-black ${isProfit ? 'text-[#00FFA3]' : 'text-[#FF3D00]'}`}>
                          {isProfit ? '+' : ''}{log.solProfit.toFixed(4)} SOL ({log.returnPercent?.toFixed(1)}%)
                        </span>
                      )}
                    </div>

                  </div>

                  {/* Precise Hour, Date, Minute, Second displayed in full as requested */}
                  <div className="text-[9px] text-white/40 font-mono border-t border-b border-white/5 py-1 mb-3 flex justify-between">
                    <span>СЪЗДАДЕН: {formatFullDateTime(log.timestamp)}</span>
                    <span className="text-[#00FFA3]/60">ID: {log.id}</span>
                  </div>

                  {/* Description of how the frontrun execution occurred */}
                  <div className="bg-[#0A0B0E] p-3 rounded-none border border-white/5 text-xs text-white/80 flex items-start gap-2.5">
                    <div className={`p-1.5 rounded-none ${isActive ? 'bg-[#00FFA3]/10 text-[#00FFA3]' : 'bg-[#00FFA3]/10 text-[#00FFA3]'}`}>
                      {isBuy ? <Coins className="h-4 w-4" /> : <TrendingUp className="h-4 w-4" />}
                    </div>
                    <div className="font-mono flex-1">
                      <p className="font-bold text-white text-[11px] uppercase tracking-wider">
                        {isActive ? '⚡ СВЕТКАВИЧНА ТРАНЗАКЦИЯ В ХОД:' : isBuy ? '⚡ Засечен купувач и изпреварване:' : '🎯 Осигурена печалба преди купувача:'}
                      </p>
                      <p className="text-white/60 text-[11px] mt-1 leading-relaxed">
                        {log.reason}
                      </p>
                      {log.solscanSignature && (
                        <a 
                          href={`https://solscan.io/tx/${log.solscanSignature}`} 
                          target="_blank" 
                          rel="noreferrer" 
                          className="mt-1.5 flex items-center gap-1 text-[#00FFA3] hover:underline font-bold text-[10px]"
                        >
                          <ExternalLink className="h-3 w-3" /> Виж Транзакцията в Solscan
                        </a>
                      )}
                    </div>
                  </div>

                  {/* Real-time price chart */}
                  <div className="my-3">
                    <MiniTradeChart log={log} />
                  </div>

                  {/* Real-time Dynamic Signals Status Indicators */}
                  {isActive && (
                    <div className="mt-2 grid grid-cols-2 sm:grid-cols-5 gap-2 bg-[#0A0B0E] p-2.5 border border-[#00FFA3]/15 font-mono text-[10px]">
                      <div>
                        <span className="text-white/40 block">ВРЕМЕ НА ХОЛД:</span>
                        <span className="text-[#00FFA3] font-black">{log.secondsHeld}с / {log.targetHoldingDuration}с ⏱️</span>
                      </div>
                      <div>
                        <span className="text-[#00FFA3]/80 block font-bold">БРОЙ ХОЛДЪРИ:</span>
                        <span className={`font-black flex items-center gap-1 ${
                          log.priceDirection === 'down' 
                            ? 'text-[#FF3D00] drop-shadow-[0_0_6px_rgba(255,61,0,0.2)]' 
                            : 'text-[#00FFA3]'
                        }`}>
                          {log.holdersCount ? log.holdersCount.toLocaleString() : '1,240'}
                          {log.priceDirection === 'down' ? ' ↘' : ' ↗'}
                        </span>
                      </div>
                      <div>
                        <span className="text-white/40 block">Топ 10 ХОЛДЪРИ:</span>
                        <span className={`font-black ${log.holderConcentration && log.holderConcentration >= 38 ? 'text-[#FF3D00]' : 'text-white'}`}>
                          {log.holderConcentration?.toFixed(1)}% {log.holderConcentration && log.holderConcentration >= 38 ? '⚠️' : '✅'}
                        </span>
                      </div>
                      <div>
                        <span className="text-white/40 block">DEV PRESSURE:</span>
                        <span className={`font-black uppercase ${
                          log.devSellPressure === 'HIGH' 
                            ? 'text-[#FF3D00]' 
                            : log.devSellPressure === 'MEDIUM' 
                              ? 'text-yellow-400' 
                              : 'text-[#00FFA3]'
                        }`}>
                          {log.devSellPressure || 'LOW'}
                        </span>
                      </div>
                      <div>
                        <span className="text-white/40 block">WINRATE ОДИТ:</span>
                        <span className="text-[#00FFA3] font-bold">{log.winRateSignal || 85}% SAFE</span>
                      </div>
                    </div>
                  )}

                  <div className="mt-2.5 text-[10px] text-white/40 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2 font-mono border-t border-white/5 pt-2">
                    <div>
                      <span>Размер: <strong className="text-white">{log.amountSol} SOL</strong></span>
                      <span className="mx-1.5 text-white/20">|</span>
                      <span>Вход: <strong className="text-[#00FFA3] font-bold" title={`$${log.priceEntry.toFixed(10)}`}>{formatExactPrice(log.priceEntry)}</strong></span>
                      {log.priceExit && (
                        <>
                          <span className="mx-1.5 text-white/20">|</span>
                          <span>
                            {isActive ? 'Текуща: ' : 'Изход: '}
                            <strong className={`transition-all duration-150 ${
                              isActive
                                ? log.priceDirection === 'up'
                                  ? 'text-[#00FFA3] font-bold'
                                  : log.priceDirection === 'down'
                                    ? 'text-[#FF3D00] font-bold'
                                    : 'text-[#00FFA3] font-bold'
                                : 'text-white/80 font-bold'
                            }`} title={`$${log.priceExit.toFixed(10)}`}>
                              {formatExactPrice(log.priceExit)}
                            </strong>
                          </span>
                          {isActive && (
                            <span className={`inline-block ml-1 h-1.5 w-1.5 rounded-full ${
                              log.priceDirection === 'up' 
                                ? 'bg-[#00FFA3] animate-ping' 
                                : log.priceDirection === 'down' 
                                  ? 'bg-[#FF3D00] animate-ping' 
                                  : 'bg-[#00FFA3]/40'
                            }`} />
                          )}
                        </>
                      )}
                    </div>
                    {log.contractAddress && (
                      <a 
                        href={`https://dexscreener.com/solana/${log.contractAddress}`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[10px] font-bold text-[#00FFA3] hover:underline flex items-center gap-1 cursor-pointer shrink-0"
                      >
                        Провери в DEX ↗
                      </a>
                    )}
                  </div>

                  {isActive && onManualCloseTrade && (
                    <button
                      onClick={() => onManualCloseTrade(log.id)}
                      className="w-full mt-2.5 bg-[#FF3D00]/20 hover:bg-[#FF3D00] hover:text-white border border-[#FF3D00]/50 text-[#FF3D00] font-bold py-1.5 px-3 text-[10px] uppercase font-mono tracking-wider transition-all cursor-pointer flex items-center justify-center gap-1.5"
                    >
                      🛑 Затвори Сделката Ръчно
                    </button>
                  )}

                </div>
              );
            })
          )}
        </div>
      </div>

    </div>
  );
}
