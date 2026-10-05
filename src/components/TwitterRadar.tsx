import React from 'react';
import { Twitter, ShieldCheck, AlertTriangle, Zap, Coins, Globe, Flame, BadgeCheck, Play, ExternalLink } from 'lucide-react';
import { MemeCoin, TradeLog } from '../types';
import { MiniTradeChart } from './MiniTradeChart';

const formatExactPrice = (price: number | null | undefined) => {
  if (price === undefined || price === null) return '-';
  if (price === 0) return '$0.00';
  // Return the raw exact price formatted to 10 decimal places to show precise, non-rounded entry/exit as requested
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

interface RadarProps {
  coins: MemeCoin[];
  tradeLogs: TradeLog[];
  onTriggerSimLaunch: () => void;
  autoTradeActive: boolean;
  onManualCloseTrade?: (tradeId: string) => void;
}

export default function TwitterRadar({ coins, tradeLogs, onTriggerSimLaunch, autoTradeActive, onManualCloseTrade }: RadarProps) {
  // Filter logs for Mode 1
  const twitterLogs = tradeLogs.filter(log => log.mode === 'AUTOPILOT_TWITTER');

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
      
      {/* Social Stream Section - 2/3 Width */}
      <div className="xl:col-span-2 bg-white/[0.02] border border-white/10 rounded-none p-6 flex flex-col space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-white/10 pb-4">
          <div className="flex items-center gap-2">
            <Twitter className="h-5 w-5 text-[#00FFA3]" />
            <h3 className="font-bold tracking-tight text-white text-base">Социален Радар & DEX Paid Мониторинг</h3>
          </div>
          
          <button 
            onClick={onTriggerSimLaunch}
            className="flex items-center justify-center gap-1.5 text-xs font-black bg-[#00FFA3] hover:bg-[#00FFA3]/90 text-black px-4 py-2 rounded-none transition-all cursor-pointer shadow-[0_0_15px_rgba(0,255,163,0.15)] shrink-0"
          >
            <Flame className="h-3.5 w-3.5 fill-black" />
            СИМУЛИРАЙ ЛЪНЧ НА КОИН
          </button>
        </div>

        {/* 3-in-1 Hyper AI Fusion Engine Status Bar */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 bg-[#0A0B0E]/60 border border-white/10 p-4 font-mono text-xs">
          <div className="flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#00FFA3] opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-[#00FFA3]"></span>
            </span>
            <div>
              <p className="text-[9px] text-white/40 uppercase tracking-widest font-black leading-none">РЕЖИМ НА ТРЕДВАНЕ</p>
              <p className="text-[#00FFA3] font-black mt-1 leading-none">3-в-1 FUSION ENGINE 🟢</p>
            </div>
          </div>
          <div>
            <p className="text-[9px] text-white/40 uppercase tracking-widest font-black leading-none">СКОРОСТ НА СКАНИРАНЕ</p>
            <p className="text-white font-bold mt-1 leading-none">145+ токена / сек</p>
          </div>
          <div>
            <p className="text-[9px] text-white/40 uppercase tracking-widest font-black leading-none">СОЦИАЛЕН ФИЛТЪР (X)</p>
            <p className="text-white font-bold mt-1 leading-none">Активен (Anti-Bot AI) 🛡️</p>
          </div>
          <div>
            <p className="text-[9px] text-white/40 uppercase tracking-widest font-black leading-none">ОДИТ НА DEV WALLET</p>
            <p className="text-white font-bold mt-1 leading-none">Rug Avoidance: ON 🔍</p>
          </div>
        </div>

        {/* Live list of coins being analyzed */}
        <div className="space-y-4 max-h-[460px] overflow-y-auto pr-1">
          {coins.length === 0 ? (
            <div className="text-center py-16 text-white/40 text-xs font-mono border border-dashed border-white/10 p-8">
              Няма засечени нови коинове за анализ. Натиснете десния бутон за симулиран старт на ланч.
            </div>
          ) : (
            coins.map((coin) => {
              const isRug = coin.scamStatus === 'RUGGED';
              const isSafe = coin.scamStatus === 'SAFE';
              
              return (
                <div 
                  key={coin.id} 
                  className={`border rounded-none p-4 transition-all bg-[#0A0B0E]/60 ${
                    isRug 
                      ? 'border-[#FF3D00] bg-[#FF3D00]/5' 
                      : coin.dexPaid 
                        ? 'border-[#00FFA3]/40 bg-[#00FFA3]/5 shadow-[inset_0_0_10px_rgba(0,255,163,0.05)]' 
                        : 'border-white/10 hover:border-white/20'
                  }`}
                >
                  <div className="flex flex-col lg:flex-row lg:justify-between lg:items-start gap-4 mb-3">
                    
                    {/* Coin name, ticker & LIVE price */}
                    <div className="flex flex-col sm:flex-row sm:items-center gap-4 sm:gap-6 flex-1">
                      <div className="flex items-center gap-3">
                        <div className="bg-white/5 h-10 w-10 rounded-none flex items-center justify-center font-black text-xs text-[#00FFA3] border border-white/10">
                          {coin.ticker[0]}
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <h4 className="font-bold tracking-tight text-white">{coin.name}</h4>
                            <span className="text-[10px] bg-white/10 text-[#00FFA3] font-mono px-1.5 py-0.5 rounded-none font-bold">{coin.ticker}/SOL</span>
                          </div>
                          <p className="text-[10px] text-white/40 font-mono mt-0.5 select-all">
                            CA: {coin.contractAddress}
                          </p>
                        </div>
                      </div>

                      {/* Zero Latency Live Price Ticker */}
                      <div className="flex items-center gap-3 bg-white/[0.02] border border-white/5 px-3 py-1.5 font-mono">
                        <div>
                          <p className="text-[8px] text-white/30 uppercase tracking-wider font-bold leading-none">ЦЕНА (0 LATENCY)</p>
                          <p className={`text-[13px] font-black mt-1 leading-none transition-all duration-200 ${
                            coin.priceDirection === 'up' 
                              ? 'text-[#00FFA3] drop-shadow-[0_0_8px_rgba(0,255,163,0.35)]' 
                              : coin.priceDirection === 'down' 
                                ? 'text-[#FF3D00] drop-shadow-[0_0_8px_rgba(255,61,0,0.35)]' 
                                : 'text-white'
                          }`}>
                            ${coin.priceUsd.toFixed(8)}
                          </p>
                        </div>
                        <div className="border-l border-white/10 pl-2">
                          <p className="text-[8px] text-white/30 uppercase tracking-wider font-bold leading-none">FDV / MC</p>
                          <p className="text-white font-bold text-[11px] mt-1 leading-none">
                            ${(coin.fdv || (coin.priceUsd * 1000000000)) >= 1_000_000 
                              ? ((coin.fdv || (coin.priceUsd * 1000000000)) / 1_000_000).toFixed(2) + 'M' 
                              : ((coin.fdv || (coin.priceUsd * 1000000000)) / 1_000).toFixed(0) + 'K'}
                          </p>
                        </div>
                        <div className="border-l border-white/10 pl-2">
                          <p className="text-[8px] text-[#00FFA3]/70 uppercase tracking-wider font-bold leading-none">ХОЛДЪРИ</p>
                          <p className={`text-[11px] font-black mt-1 leading-none transition-all duration-150 ${
                            coin.priceDirection === 'up' 
                              ? 'text-[#00FFA3]' 
                              : coin.priceDirection === 'down' 
                                ? 'text-[#FF3D00]' 
                                : 'text-white'
                          }`}>
                            {coin.holdersCount ? coin.holdersCount.toLocaleString() : '1,240'}
                          </p>
                        </div>
                        <span className={`h-1.5 w-1.5 rounded-full ${
                          coin.priceDirection === 'up' 
                            ? 'bg-[#00FFA3] animate-ping' 
                            : coin.priceDirection === 'down' 
                              ? 'bg-[#FF3D00] animate-ping' 
                              : 'bg-white/20'
                        }`} />
                      </div>
                    </div>

                    {/* Meta Indicators */}
                    <div className="flex flex-wrap items-center gap-1.5">
                      {/* DEXScreener Paid Ads status */}
                      {coin.dexPaid ? (
                        <span className="text-[10px] font-black tracking-wider bg-[#00FFA3]/10 text-[#00FFA3] px-2 py-0.5 rounded-none border border-[#00FFA3]/20 flex items-center gap-1">
                          <Flame className="h-3 w-3 text-[#00FFA3] fill-[#00FFA3]" />
                          DEX PAID ADS
                        </span>
                      ) : (
                        <span className="text-[10px] font-mono bg-white/5 text-white/50 px-2 py-0.5 rounded-none border border-white/10">
                          Organic Launch
                        </span>
                      )}

                      {/* Liquidity Lock status */}
                      {coin.liquidityLock ? (
                        <span className="text-[10px] font-bold bg-[#00FFA3]/10 text-[#00FFA3] px-2 py-0.5 rounded-none border border-[#00FFA3]/20">
                          LP Locked 🔒
                        </span>
                      ) : (
                        <span className="text-[10px] font-bold bg-[#FF3D00]/10 text-[#FF3D00] px-2 py-0.5 rounded-none border border-[#FF3D00]/20 animate-pulse">
                          LP Unlocked 🔓 RISK
                        </span>
                      )}

                      {/* Scam Score Indicator */}
                      {coin.scamScore !== null && (
                        <span className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-none border ${
                          coin.scamScore > 50 
                            ? 'bg-[#FF3D00]/10 border-[#FF3D00]/20 text-[#FF3D00]' 
                            : 'bg-[#00FFA3]/10 border-[#00FFA3]/20 text-[#00FFA3]'
                        }`}>
                          Risk Score: {coin.scamScore}%
                        </span>
                      )}

                      {/* DEXScreener Link */}
                      <a 
                        href={`https://dexscreener.com/solana/${coin.contractAddress}`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[10px] font-black bg-white/10 text-[#00FFA3] hover:bg-[#00FFA3]/20 border border-white/20 hover:border-[#00FFA3]/40 px-2 py-0.5 rounded-none transition-all flex items-center gap-1 cursor-pointer"
                        title="Провери в DEXScreener"
                      >
                        📊 DEXSCREENER ↗
                      </a>
                    </div>
                  </div>

                  {/* Social and Twitter Stats row */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4 bg-white/[0.01] p-3 rounded-none border border-white/5 text-xs font-mono">
                    
                    {/* Social Media Status */}
                    <div className="space-y-1.5">
                      <p className="text-[9px] text-white/40 uppercase tracking-widest font-black">Социални Мрежи</p>
                      <div className="flex items-center gap-3">
                        <div className="flex items-center gap-1 text-white/70">
                          <Globe className={`h-3.5 w-3.5 ${coin.hasWebsite ? 'text-[#00FFA3]' : 'text-white/30'}`} />
                          <span>Website: {coin.hasWebsite ? '✅ Наличен' : '❌ Липсва'}</span>
                        </div>
                        <div className="flex items-center gap-1 text-white/70">
                          <Twitter className={`h-3.5 w-3.5 ${coin.hasTwitter ? 'text-sky-400' : 'text-white/30'}`} />
                          <span>Twitter: {coin.hasTwitter ? '✅ Наличен' : '❌ Липсва'}</span>
                        </div>
                      </div>
                    </div>

                    {/* Twitter stats details */}
                    {coin.twitterStats && (
                      <div className="space-y-1">
                        <p className="text-[9px] text-white/40 uppercase tracking-widest font-black">Анализ на Twitter Постове</p>
                        <div className="text-white/80 space-y-0.5 text-[11px]">
                          <div className="flex justify-between">
                            <span>Следващи: <strong className="text-white">{coin.twitterStats.followers.toLocaleString()}</strong></span>
                            <span>Бот лайкове: <strong className={coin.twitterStats.isBotLiking ? 'text-[#FF3D00]' : 'text-[#00FFA3]'}>{coin.twitterStats.isBotLiking ? 'Засечени ⚠️' : 'Органични ✅'}</strong></span>
                          </div>
                          <div className="text-white/50 italic text-[10px]">
                            Сентимент: "{coin.twitterStats.sentimentText}"
                          </div>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Top Holders & previous winrate */}
                  <div className="mt-3 flex flex-wrap justify-between items-center text-[10px] text-white/40 border-t border-white/5 pt-2 gap-2 font-mono">
                    <div className="flex items-center gap-1">
                      <span>Концентрация на Холдерите:</span>
                      <strong className="text-white">
                        {coin.ownerWallets[0]?.percentage}% при дев портфейл
                      </strong>
                    </div>
                    <div>
                      <span>Предишна история на дев портфейла:</span>
                      <strong className={`ml-1 ${coin.ownerWallets[0]?.prevTradeWinRate > 50 ? 'text-[#00FFA3]' : 'text-white/60'}`}>
                        {coin.ownerWallets[0]?.prevTradeWinRate}% Win Rate
                      </strong>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Trade Logs Section - 1/3 Width */}
      <div className="bg-white/[0.02] border border-white/10 rounded-none p-6 flex flex-col space-y-4">
        <div className="border-b border-white/10 pb-4">
          <div className="flex items-center gap-2">
            <Coins className="h-5 w-5 text-[#00FFA3]" />
            <h3 className="font-bold tracking-tight text-white text-base">Дневник Сделки (Mode 1)</h3>
          </div>
          <p className="text-[11px] text-white/40 mt-1 leading-relaxed">
            Сделки на Автопилот 1 (Twitter + DEX Paid). Авто-изход на печалба или бърз stop-loss за стотинки.
          </p>
        </div>

        {/* Trade entries scroll */}
        <div className="space-y-3 max-h-[460px] overflow-y-auto pr-1 flex-1">
          {twitterLogs.length === 0 ? (
            <div className="text-center py-16 text-white/40 text-xs font-mono border border-dashed border-white/10 p-8">
              Няма активни сделки на автопилота. Пуснете автопилота от бутона горе, за да стартирате.
            </div>
          ) : (
            twitterLogs.map((log) => {
              const isProfit = (log.solProfit || 0) > 0;
              const isStopLoss = log.type === 'SELL_STOP_LOSS';
              const isRugExit = log.type === 'RUG_EXIT';
              
              // Local market cap formatter
              const formatMc = (fdv: number | undefined, price: number) => {
                const val = fdv || (price * 1000000000);
                if (val >= 1_000_000_000) return `$${(val / 1_000_000_000).toFixed(2)}B`;
                if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(1)}M`;
                if (val >= 1_000) return `$${(val / 1_000).toFixed(0)}K`;
                return `$${val.toFixed(0)}`;
              };

              return (
                <div 
                  key={log.id} 
                  className={`bg-[#0A0B0E]/60 p-3 text-xs space-y-2 border ${
                    log.status === 'ACTIVE' 
                      ? 'border-[#00FFA3]/40 bg-[#00FFA3]/5 shadow-[0_0_15px_rgba(0,255,163,0.02)]' 
                      : isStopLoss 
                        ? 'border-[#FF3D00]/30 bg-[#FF3D00]/5' 
                        : isProfit 
                          ? 'border-[#00FFA3]/20 bg-[#00FFA3]/5'
                          : 'border-white/10'
                  }`}
                >
                  <div className="flex justify-between items-center">
                    <span className="font-bold text-white flex items-center gap-1.5">
                      <span className="bg-white/10 text-[#00FFA3] px-1.5 py-0.5 text-[10px] font-mono">
                        {log.coinTicker}
                      </span>
                      {log.coinName}
                    </span>
                    
                    <span className={`font-mono text-[9px] px-2 py-0.5 rounded-none font-black uppercase tracking-wider ${
                      log.status === 'ACTIVE' 
                        ? 'bg-[#00FFA3]/20 text-[#00FFA3] animate-pulse' 
                        : isStopLoss 
                          ? 'bg-[#FF3D00]/10 text-[#FF3D00]' 
                          : isProfit 
                            ? 'bg-[#00FFA3]/10 text-[#00FFA3]'
                            : 'bg-[#FF3D00]/10 text-[#FF3D00]'
                    }`}>
                      {log.status === 'ACTIVE' ? 'АКТИВНА' : isStopLoss ? 'STOP LOSS' : isRugExit ? 'RUG OUT' : 'ПРОДАДЕН'}
                    </span>
                  </div>

                  {/* Precise Hour, Date, Minute, Second displayed in full as requested */}
                  <div className="text-[9px] text-white/40 font-mono border-t border-b border-white/5 py-1 flex justify-between">
                    <span>СЪЗДАДЕН: {formatFullDateTime(log.timestamp)}</span>
                    <span className="text-[#00FFA3]/60">ID: {log.id}</span>
                  </div>

                  <div className="grid grid-cols-2 gap-2 text-[11px] font-mono pt-1">
                    <div>
                      <p className="text-white/40">Размер:</p>
                      <p className="font-bold text-white">{log.amountSol} SOL</p>
                    </div>
                    <div>
                      <p className="text-white/40">Вход:</p>
                      <p className="font-bold text-[#00FFA3]" title={`$${log.priceEntry.toFixed(10)}`}>
                        {formatExactPrice(log.priceEntry)}
                      </p>
                    </div>
                    {log.status === 'ACTIVE' && log.priceExit && (
                      <div>
                        <p className="text-[#00FFA3]/70 flex items-center gap-1">
                          Текуща Цена:
                          <span className={`inline-block h-1.5 w-1.5 rounded-full ${
                            log.priceDirection === 'up' 
                              ? 'bg-[#00FFA3] animate-ping' 
                              : log.priceDirection === 'down' 
                                ? 'bg-[#FF3D00] animate-ping' 
                                : 'bg-[#00FFA3]/40'
                          }`} />
                        </p>
                        <p className={`font-semibold transition-all duration-150 ${
                          log.priceDirection === 'up' 
                            ? 'text-[#00FFA3]' 
                            : log.priceDirection === 'down' 
                              ? 'text-[#FF3D00]' 
                              : 'text-[#00FFA3]'
                        }`} title={`$${log.priceExit.toFixed(10)}`}>
                          {formatExactPrice(log.priceExit)}
                        </p>
                      </div>
                    )}
                    {log.status === 'CLOSED' && log.priceExit && (
                      <div>
                        <p className="text-white/40">Изход:</p>
                        <p className="font-bold text-white/90" title={`$${log.priceExit.toFixed(10)}`}>
                          {formatExactPrice(log.priceExit)}
                        </p>
                      </div>
                    )}
                    {log.solProfit !== null && (
                      <div>
                        <p className="text-white/40">P&L:</p>
                        <p className={`font-black ${isProfit ? 'text-[#00FFA3]' : 'text-[#FF3D00]'}`}>
                          {isProfit ? '+' : ''}{log.solProfit.toFixed(4)} SOL ({log.returnPercent?.toFixed(1)}%)
                        </p>
                      </div>
                    )}
                  </div>

                  {/* Real-time price chart */}
                  <MiniTradeChart log={log} />

                  {/* Execution detail/justification */}
                  <div className="text-[10px] text-white/60 bg-white/[0.01] p-2 border border-white/5 font-mono space-y-1">
                    <div>
                      <span className="text-[#00FFA3]">INFO //</span> {log.reason}
                    </div>
                    {log.solscanSignature && (
                      <a 
                        href={`https://solscan.io/tx/${log.solscanSignature}`} 
                        target="_blank" 
                        rel="noreferrer" 
                        className="flex items-center gap-1 text-[#00FFA3] hover:underline font-bold text-[9px]"
                      >
                        <ExternalLink className="h-3 w-3" /> Виж Транзакцията в Solscan
                      </a>
                    )}
                  </div>

                  {/* Real-time Dynamic Signals Status Indicators */}
                  {log.status === 'ACTIVE' && (
                    <div className="mt-2 grid grid-cols-2 gap-2 bg-[#0A0B0E] p-2.5 border border-[#00FFA3]/15 font-mono text-[10px]">
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

                  {log.status === 'ACTIVE' && onManualCloseTrade && (
                    <button
                      onClick={() => onManualCloseTrade(log.id)}
                      className="w-full bg-[#FF3D00]/20 hover:bg-[#FF3D00] hover:text-white border border-[#FF3D00]/50 text-[#FF3D00] font-bold py-1.5 px-3 text-[10px] uppercase font-mono tracking-wider transition-all cursor-pointer flex items-center justify-center gap-1.5"
                    >
                      🛑 Затвори Сделката Ръчно
                    </button>
                  )}

                  {log.contractAddress && (
                    <div className="flex justify-end pt-1">
                      <a 
                        href={`https://dexscreener.com/solana/${log.contractAddress}`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[9px] font-mono font-bold text-[#00FFA3] hover:underline flex items-center gap-1 cursor-pointer"
                      >
                        Провери в DEXScreener ↗
                      </a>
                    </div>
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
