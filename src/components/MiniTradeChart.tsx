import React, { useState } from 'react';
import { TradeLog } from '../types';
import { TrendingUp, TrendingDown, Clock, Eye } from 'lucide-react';

const formatExactPrice = (price: number | null | undefined) => {
  if (price === undefined || price === null) return '-';
  if (price === 0) return '$0.00';
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

const getExitTimestamp = (log: TradeLog): string => {
  if (log.status === 'ACTIVE') {
    return new Date().toISOString();
  }
  const entryDate = new Date(log.timestamp);
  const exitDate = new Date(entryDate.getTime() + (log.secondsHeld || 0) * 1000);
  return exitDate.toISOString();
};

interface Candle {
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  isGreen: boolean;
  timeLabel: string;
}

// Generate realistic DEX candles out of the price state
export const generateCandlesticks = (log: TradeLog): Candle[] => {
  const numCandles = 16;
  const seed = log.id.split("").reduce((acc, c) => acc + c.charCodeAt(0), 0);
  
  // Real-time prices or simulated
  let basePrices = log.priceHistory && log.priceHistory.length > 1 
    ? [...log.priceHistory] 
    : [log.priceEntry];

  const entry = log.priceEntry;
  const exit = log.priceExit || entry * (1 + (log.returnPercent || 0) / 100);

  // If there are too few price points, let's expand them into a smooth path with volatile spikes
  if (basePrices.length < numCandles * 2) {
    const expanded: number[] = [];
    const totalSteps = numCandles * 4;
    for (let i = 0; i < totalSteps; i++) {
      const progress = i / (totalSteps - 1);
      // Linear baseline
      const base = entry + (exit - entry) * progress;
      // Oscillations (waves of buy/sell orders)
      const wave = Math.sin(progress * Math.PI * 4.5 + seed) * (entry * 0.08);
      const noise = (((seed * i) % 100) - 50) / 950 * entry;
      expanded.push(Math.max(0.0000000001, base + wave + noise));
    }
    basePrices = expanded;
  }

  const pointsPerCandle = Math.ceil(basePrices.length / numCandles);
  const candles: Candle[] = [];

  const entryTime = new Date(log.timestamp).getTime();
  const exitTime = new Date(getExitTimestamp(log)).getTime();
  const timeStep = (exitTime - entryTime) / numCandles;

  for (let i = 0; i < numCandles; i++) {
    const startIdx = i * pointsPerCandle;
    const endIdx = Math.min(startIdx + pointsPerCandle, basePrices.length);

    if (startIdx >= basePrices.length) {
      const prev = candles[candles.length - 1] || { open: entry, high: entry, low: entry, close: entry, volume: 100, isGreen: true, timeLabel: '' };
      candles.push({ ...prev });
      continue;
    }

    const chunk = basePrices.slice(startIdx, endIdx);
    const open = chunk[0];
    const close = chunk[chunk.length - 1];

    let high = Math.max(...chunk);
    let low = Math.min(...chunk);

    // Ensure wicks are visible and realistic for pump/dump action
    const bodyMax = Math.max(open, close);
    const bodyMin = Math.min(open, close);

    const wickFactor = 0.015 + (Math.abs(Math.sin(i * 1.7 + seed)) * 0.04);
    high = Math.max(high, bodyMax * (1 + wickFactor));
    low = Math.min(low, bodyMin * (1 - wickFactor));

    const isGreen = close >= open;
    const volume = 80 + Math.abs(Math.cos(i * 2.3 + seed)) * 320;

    const candleTime = new Date(entryTime + i * timeStep);
    const timeLabel = `${String(candleTime.getHours()).padStart(2, '0')}:${String(candleTime.getMinutes()).padStart(2, '0')}:${String(candleTime.getSeconds()).padStart(2, '0')}`;

    candles.push({
      open,
      high,
      low,
      close,
      volume,
      isGreen,
      timeLabel
    });
  }

  // Force first candle to open at entry price, last candle to close near exit price
  if (candles.length > 0) {
    candles[0].open = entry;
    if (candles[0].low > entry) candles[0].low = entry * 0.99;
    
    candles[candles.length - 1].close = exit;
    if (candles[candles.length - 1].high < exit) candles[candles.length - 1].high = exit * 1.01;
  }

  return candles;
};

interface MiniTradeChartProps {
  log: TradeLog;
}

export const MiniTradeChart: React.FC<MiniTradeChartProps> = ({ log }) => {
  const candles = generateCandlesticks(log);
  const isUp = (log.returnPercent || 0) >= 0;

  // Active hover candle info
  const [hoveredCandle, setHoveredCandle] = useState<Candle | null>(null);

  const allHighs = candles.map(c => c.high);
  const allLows = candles.map(c => c.low);
  const maxPrice = Math.max(...allHighs);
  const minPrice = Math.min(...allLows);
  const priceRange = maxPrice - minPrice || 0.00000001;

  const maxVolume = Math.max(...candles.map(c => c.volume)) || 1;

  const svgWidth = 600;
  const svgHeight = 180;
  
  const paddingLeft = 15;
  const paddingRight = 75; // Dex style price bar on the right side
  const paddingTop = 25;
  const paddingBottom = 20;

  const usableWidth = svgWidth - paddingLeft - paddingRight;
  const usableHeight = svgHeight - paddingTop - paddingBottom;

  const getX = (index: number) => {
    return paddingLeft + (index / candles.length) * usableWidth + (usableWidth / candles.length) / 2;
  };

  const getY = (price: number) => {
    return svgHeight - paddingBottom - ((price - minPrice) / priceRange) * usableHeight;
  };

  // Grid levels (DexScreener style)
  const gridLevels = [
    minPrice + priceRange * 0.25,
    minPrice + priceRange * 0.5,
    minPrice + priceRange * 0.75,
  ];

  // Currently displayed candle values (hovered or latest)
  const activeCandle = hoveredCandle || candles[candles.length - 1];

  return (
    <div id={`trade-chart-${log.id}`} className="bg-[#0c0d12] border border-white/[0.08] p-3 font-mono space-y-2 select-none relative overflow-hidden shadow-2xl">
      {/* TradingView / DexScreener style Top Status Bar */}
      <div className="flex flex-wrap justify-between items-center text-[10px] gap-2 border-b border-white/5 pb-2">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[#00FFA3] font-bold">{log.coinTicker}/SOL</span>
          <span className="bg-white/10 text-white px-1 rounded-none text-[8px]">1s</span>
          <span className="text-white/40">● DEXSCREENER CLONE</span>
          
          {activeCandle && (
            <div className="flex gap-2 items-center text-white/70 ml-2">
              <span>O <strong className={activeCandle.isGreen ? "text-[#26a69a]" : "text-[#ef5350]"}>{formatExactPrice(activeCandle.open)}</strong></span>
              <span>H <strong className={activeCandle.isGreen ? "text-[#26a69a]" : "text-[#ef5350]"}>{formatExactPrice(activeCandle.high)}</strong></span>
              <span>L <strong className={activeCandle.isGreen ? "text-[#26a69a]" : "text-[#ef5350]"}>{formatExactPrice(activeCandle.low)}</strong></span>
              <span>C <strong className={activeCandle.isGreen ? "text-[#26a69a]" : "text-[#ef5350]"}>{formatExactPrice(activeCandle.close)}</strong></span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          <span className="text-[8px] text-white/30">VOLUME: {activeCandle?.volume.toFixed(0)} SOL</span>
          {isUp ? (
            <span className="text-[10px] text-[#26a69a] font-bold flex items-center gap-0.5">
              ▲ +{(log.returnPercent || 0).toFixed(2)}%
            </span>
          ) : (
            <span className="text-[10px] text-[#ef5350] font-bold flex items-center gap-0.5">
              ▼ {(log.returnPercent || 0).toFixed(2)}%
            </span>
          )}
        </div>
      </div>

      <div className="relative h-[180px] w-full bg-[#131722]">
        {/* Background Watermark Ticker */}
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-0">
          <span className="text-[48px] font-black text-white/[0.02] tracking-widest uppercase">
            {log.coinTicker} / SOL
          </span>
        </div>

        <svg 
          viewBox={`0 0 ${svgWidth} ${svgHeight}`} 
          className="w-full h-full relative z-10"
          preserveAspectRatio="none"
        >
          {/* Grid lines (TradingView style) */}
          {gridLevels.map((level, i) => (
            <line
              key={i}
              x1={paddingLeft}
              y1={getY(level)}
              x2={svgWidth - paddingRight}
              y2={getY(level)}
              className="stroke-white/[0.04] stroke-1"
              strokeDasharray="3 3"
            />
          ))}

          {/* Time vertical grid lines */}
          {Array.from({ length: 4 }).map((_, i) => {
            const idx = Math.floor((candles.length / 4) * i);
            const x = getX(idx);
            return (
              <line
                key={i}
                x1={x}
                y1={paddingTop}
                x2={x}
                y2={svgHeight - paddingBottom}
                className="stroke-white/[0.03] stroke-1"
                strokeDasharray="4 4"
              />
            );
          })}

          {/* DexScreener Right Side Price Axis Panel */}
          <line 
            x1={svgWidth - paddingRight}
            y1={paddingTop}
            x2={svgWidth - paddingRight}
            y2={svgHeight - paddingBottom}
            className="stroke-white/10 stroke-1"
          />

          {/* Price Axis Labels on the right */}
          {gridLevels.map((level, i) => (
            <text
              key={i}
              x={svgWidth - paddingRight + 5}
              y={getY(level) + 3}
              fill="rgba(255, 255, 255, 0.4)"
              fontSize="8"
              className="font-mono text-[8px]"
            >
              {level.toFixed(8)}
            </text>
          ))}

          <text
            x={svgWidth - paddingRight + 5}
            y={paddingTop + 5}
            fill="rgba(38, 166, 154, 0.6)"
            fontSize="8"
            className="font-bold"
          >
            {maxPrice.toFixed(8)}
          </text>

          <text
            x={svgWidth - paddingRight + 5}
            y={svgHeight - paddingBottom - 2}
            fill="rgba(239, 83, 80, 0.6)"
            fontSize="8"
            className="font-bold"
          >
            {minPrice.toFixed(8)}
          </text>

          {/* Volume Bars (rendered at bottom) */}
          {candles.map((candle, index) => {
            const x = getX(index);
            const barW = (usableWidth / candles.length) * 0.7;
            const barH = (candle.volume / maxVolume) * 35; // Cap height to 35px at bottom
            const barY = svgHeight - paddingBottom - barH;
            
            return (
              <rect
                key={`vol-${index}`}
                x={x - barW / 2}
                y={barY}
                width={barW}
                height={barH}
                fill={candle.isGreen ? "#26a69a" : "#ef5350"}
                className="opacity-20"
              />
            );
          })}

          {/* Candlesticks (Wicks + Bodies) */}
          {candles.map((candle, index) => {
            const x = getX(index);
            const yHigh = getY(candle.high);
            const yLow = getY(candle.low);
            const yOpen = getY(candle.open);
            const yClose = getY(candle.close);

            const bodyTop = Math.min(yOpen, yClose);
            const bodyBottom = Math.max(yOpen, yClose);
            const bodyHeight = Math.max(1.5, bodyBottom - bodyTop);
            
            const candleWidth = Math.max(3, (usableWidth / candles.length) * 0.62);
            const candleColor = candle.isGreen ? "#26a69a" : "#ef5350";

            return (
              <g 
                key={`candle-${index}`}
                onMouseEnter={() => setHoveredCandle(candle)}
                onMouseLeave={() => setHoveredCandle(null)}
                className="cursor-crosshair"
              >
                {/* Wick line */}
                <line
                  x1={x}
                  y1={yHigh}
                  x2={x}
                  y2={yLow}
                  stroke={candleColor}
                  strokeWidth="1.5"
                />
                {/* Candle body */}
                <rect
                  x={x - candleWidth / 2}
                  y={bodyTop}
                  width={candleWidth}
                  height={bodyHeight}
                  fill={candleColor}
                  stroke={candleColor}
                  strokeWidth="0.5"
                />

                {/* Interactive transparent wider hover target */}
                <rect
                  x={x - (usableWidth / candles.length) / 2}
                  y={paddingTop}
                  width={usableWidth / candles.length}
                  height={usableHeight}
                  fill="transparent"
                  className="hover:fill-white/[0.03] transition-colors duration-75"
                />
              </g>
            );
          })}

          {/* BUY (B) marker at the first candle */}
          {candles.length > 0 && (
            <g transform={`translate(${getX(0)}, ${getY(candles[0].open)})`}>
              <circle
                cx="0"
                cy="0"
                r="7"
                fill="#26a69a"
                className="stroke-black stroke-1 animate-pulse"
              />
              <text
                x="0"
                y="3"
                textAnchor="middle"
                fontSize="9"
                fontWeight="black"
                fill="#000"
                className="font-mono text-[9px] font-extrabold"
              >
                B
              </text>
              {/* Labeled price banner */}
              <rect 
                x="-30" 
                y="-25" 
                width="60" 
                height="13" 
                fill="#26a69a" 
                rx="1"
                className="stroke-black/30 stroke-[0.5]"
              />
              <text
                x="0"
                y="-16"
                textAnchor="middle"
                fontSize="7"
                fontWeight="bold"
                fill="#fff"
                className="font-mono text-[7px]"
              >
                BUY ENTRY
              </text>
            </g>
          )}

          {/* SELL (S) or LIVE (L) marker at the last candle */}
          {candles.length > 0 && (
            <g transform={`translate(${getX(candles.length - 1)}, ${getY(candles[candles.length - 1].close)})`}>
              <circle
                cx="0"
                cy="0"
                r="7"
                fill={log.status === 'ACTIVE' ? '#eab308' : '#ef5350'}
                className="stroke-black stroke-1 animate-pulse"
              />
              <text
                x="0"
                y="3"
                textAnchor="middle"
                fontSize="9"
                fontWeight="black"
                fill="#000"
                className="font-mono text-[9px] font-extrabold"
              >
                {log.status === 'ACTIVE' ? 'L' : 'S'}
              </text>
              {/* Labeled price banner */}
              <rect 
                x="-30" 
                y="-25" 
                width="60" 
                height="13" 
                fill={log.status === 'ACTIVE' ? '#eab308' : '#ef5350'} 
                rx="1"
                className="stroke-black/30 stroke-[0.5]"
              />
              <text
                x="0"
                y="-16"
                textAnchor="middle"
                fontSize="7"
                fontWeight="bold"
                fill={log.status === 'ACTIVE' ? '#000' : '#fff'}
                className="font-mono text-[7px]"
              >
                {log.status === 'ACTIVE' ? 'LIVE NOW' : 'SELL EXIT'}
              </text>
            </g>
          )}
        </svg>

        {/* Live status label */}
        {log.status === 'ACTIVE' && (
          <div className="absolute left-3 bottom-2 flex items-center gap-1.5 bg-yellow-500/20 text-yellow-400 text-[8px] font-mono px-1.5 py-0.5 border border-yellow-500/30">
            <span className="w-1.5 h-1.5 bg-yellow-500 rounded-full animate-ping" />
            <span>ЖИВА ТРАЕКТОРИЯ В РЕАЛНО ВРЕМЕ</span>
          </div>
        )}
      </div>

      {/* Date details row */}
      <div className="flex justify-between items-center text-[8px] text-white/30 border-t border-white/5 pt-1.5 px-1">
        <span>ВХОД: {formatFullDateTime(log.timestamp)}</span>
        <span>ИЗХОД: {formatFullDateTime(getExitTimestamp(log))}</span>
      </div>
    </div>
  );
};
