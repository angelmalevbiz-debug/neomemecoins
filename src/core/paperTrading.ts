import type { RiskAssessment } from './types';

export interface PaperRiskConfig {
  startingBalanceUsd: number;
  riskPerTradePct: number;
  maxAllocationPct: number;
  stopLossPct: number;
  takeProfitPct: number;
  trailingStopPct: number;
  maxOpenPositions: number;
  maxDailyLossPct: number;
  maxHoldMinutes: number;
  minQualityScore: number;
  minConfidenceScore: number;
  minLiquidityUsd: number;
}

export const DEFAULT_PAPER_RISK_CONFIG: PaperRiskConfig = {
  startingBalanceUsd: 1_000,
  riskPerTradePct: 1,
  maxAllocationPct: 12,
  stopLossPct: 8,
  takeProfitPct: 16,
  trailingStopPct: 6,
  maxOpenPositions: 2,
  maxDailyLossPct: 3,
  maxHoldMinutes: 45,
  minQualityScore: 72,
  minConfidenceScore: 65,
  minLiquidityUsd: 20_000,
};export type PaperExitReason =
  | 'STOP_LOSS'
  | 'TRAILING_STOP'
  | 'TAKE_PROFIT'
  | 'MAX_HOLD'
  | 'MANUAL';

export interface EntryAudit {
  posture: RiskAssessment['posture'];
  riskScore: number;
  qualityScore: number;
  confidenceScore: number;
  summary: string;
  signalIds: string[];
  positiveSignals: string[];
  riskSignals: string[];
}

export interface PaperPosition {
  id: string;
  tokenAddress: string;
  pairAddress?: string;
  symbol: string;
  name: string;
  entryPrice: number;
  currentPrice: number;
  peakPrice: number;
  quantity: number;
  notionalUsd: number;
  stopPrice: number;
  takeProfitPrice: number;
  openedAt: number;
  updatedAt: number;
  audit: EntryAudit;
}export interface PaperTrade extends PaperPosition {
  closedAt: number;
  exitPrice: number;
  pnlUsd: number;
  pnlPct: number;
  exitReason: PaperExitReason;
}

export interface PaperState {
  dayKey: string;
  balanceUsd: number;
  realizedPnlUsd: number;
  dailyPnlUsd: number;
  positions: PaperPosition[];
  trades: PaperTrade[];
  lastEvent?: string;
}

export interface EntryDecision {
  allowed: boolean;
  reasons: string[];
}

const STORAGE_KEY = 'neo-meme-paper-state-v1';
const MAX_TRADE_HISTORY = 100;

function todayKey(): string {
  return new Date().toISOString().slice(0, 10);
}

function storageAvailable(): boolean {
  return typeof window !== 'undefined' && Boolean(window.localStorage);
}export function createPaperState(config = DEFAULT_PAPER_RISK_CONFIG): PaperState {
  return {
    dayKey: todayKey(),
    balanceUsd: config.startingBalanceUsd,
    realizedPnlUsd: 0,
    dailyPnlUsd: 0,
    positions: [],
    trades: [],
    lastEvent: 'Paper engine initialized.',
  };
}

export function normalizePaperDay(state: PaperState): PaperState {
  if (state.dayKey === todayKey()) return state;
  return {
    ...state,
    dayKey: todayKey(),
    dailyPnlUsd: 0,
    lastEvent: 'New UTC paper-trading day started; daily loss counter reset.',
  };
}

export function loadPaperState(config = DEFAULT_PAPER_RISK_CONFIG): PaperState {
  if (!storageAvailable()) return createPaperState(config);
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return createPaperState(config);
    const parsed = JSON.parse(raw) as PaperState;
    if (!Array.isArray(parsed.positions) || !Array.isArray(parsed.trades)) return createPaperState(config);
    return normalizePaperDay(parsed);
  } catch {
    return createPaperState(config);
  }
}export function savePaperState(state: PaperState): PaperState {
  const normalized = normalizePaperDay(state);
  if (storageAvailable()) localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
  return normalized;
}

export function canOpenPaperTrade(
  assessment: RiskAssessment,
  state: PaperState,
  config = DEFAULT_PAPER_RISK_CONFIG,
): EntryDecision {
  const reasons: string[] = [];
  const normalized = normalizePaperDay(state);
  const criticalSignals = assessment.signals.filter((signal) => signal.severity === 'critical');
  const dailyLossLimitUsd = config.startingBalanceUsd * (config.maxDailyLossPct / 100);

  if (assessment.posture !== 'SETUP') reasons.push(`Posture is ${assessment.posture}, not SETUP.`);
  if (assessment.qualityScore < config.minQualityScore) reasons.push(`Quality ${assessment.qualityScore} < ${config.minQualityScore}.`);
  if (assessment.confidenceScore < config.minConfidenceScore) reasons.push(`Confidence ${assessment.confidenceScore}% < ${config.minConfidenceScore}%.`);
  if (criticalSignals.length > 0) reasons.push(`${criticalSignals.length} critical risk signal(s) present.`);
  if (assessment.market.liquidityUsd < config.minLiquidityUsd) reasons.push(`Liquidity below $${config.minLiquidityUsd.toLocaleString()}.`);
  if (!Number.isFinite(assessment.market.priceUsd) || assessment.market.priceUsd <= 0) reasons.push('Valid market price is unavailable.');
  if (normalized.positions.length >= config.maxOpenPositions) reasons.push('Maximum simultaneous paper positions reached.');
  if (normalized.positions.some((position) => position.tokenAddress === assessment.tokenAddress)) reasons.push('Token already has an open paper position.');
  if (normalized.dailyPnlUsd <= -dailyLossLimitUsd) reasons.push('Daily paper loss limit reached.');

  return { allowed: reasons.length === 0, reasons };
}export function openPaperTrade(
  assessment: RiskAssessment,
  state: PaperState,
  config = DEFAULT_PAPER_RISK_CONFIG,
): PaperState {
  const normalized = normalizePaperDay(state);
  const decision = canOpenPaperTrade(assessment, normalized, config);
  if (!decision.allowed) return { ...normalized, lastEvent: `Entry blocked: ${decision.reasons.join(' ')}` };

  const entryPrice = assessment.market.priceUsd;
  const riskBudgetUsd = Math.max(0, normalized.balanceUsd * (config.riskPerTradePct / 100));
  const stopFraction = config.stopLossPct / 100;
  const rawNotionalUsd = stopFraction > 0 ? riskBudgetUsd / stopFraction : 0;
  const maxAllocationUsd = normalized.balanceUsd * (config.maxAllocationPct / 100);
  const notionalUsd = Math.max(0, Math.min(rawNotionalUsd, maxAllocationUsd));
  const quantity = entryPrice > 0 ? notionalUsd / entryPrice : 0;
  const now = Date.now();
  const position: PaperPosition = {
    id: `${assessment.tokenAddress}:${now}`,
    tokenAddress: assessment.tokenAddress,
    pairAddress: assessment.market.pairAddress,
    symbol: assessment.market.symbol,
    name: assessment.market.name,
    entryPrice,
    currentPrice: entryPrice,
    peakPrice: entryPrice,
    quantity,
    notionalUsd,
    stopPrice: entryPrice * (1 - stopFraction),
    takeProfitPrice: entryPrice * (1 + config.takeProfitPct / 100),
    openedAt: now,
    updatedAt: now,
    audit: {
      posture: assessment.posture,
      riskScore: assessment.riskScore,
      qualityScore: assessment.qualityScore,
      confidenceScore: assessment.confidenceScore,
      summary: assessment.summary,
      signalIds: assessment.signals.map((signal) => signal.id),
      positiveSignals: assessment.signals.filter((signal) => signal.severity === 'positive').map((signal) => signal.title).slice(0, 5),
      riskSignals: assessment.signals.filter((signal) => signal.riskPoints > 0).map((signal) => signal.title).slice(0, 5),
    },
  };

  return savePaperState({
    ...normalized,
    positions: [...normalized.positions, position],
    lastEvent: `Opened PAPER ${position.symbol} at $${entryPrice.toPrecision(6)} with $${notionalUsd.toFixed(2)} notional.`,
  });
}function closePosition(
  position: PaperPosition,
  exitPrice: number,
  exitReason: PaperExitReason,
): PaperTrade {
  const pnlUsd = position.quantity * (exitPrice - position.entryPrice);
  const pnlPct = position.entryPrice > 0 ? ((exitPrice - position.entryPrice) / position.entryPrice) * 100 : 0;
  return {
    ...position,
    currentPrice: exitPrice,
    updatedAt: Date.now(),
    closedAt: Date.now(),
    exitPrice,
    pnlUsd,
    pnlPct,
    exitReason,
  };
}

export function updatePaperPositions(
  state: PaperState,
  prices: Record<string, number>,
  config = DEFAULT_PAPER_RISK_CONFIG,
): PaperState {
  const normalized = normalizePaperDay(state);
  const stillOpen: PaperPosition[] = [];
  const closed: PaperTrade[] = [];
  const now = Date.now();

  for (const position of normalized.positions) {
    const price = prices[position.tokenAddress];
    if (!Number.isFinite(price) || price <= 0) {
      stillOpen.push(position);
      continue;
    }

    const peakPrice = Math.max(position.peakPrice, price);
    const trailingArmed = peakPrice >= position.entryPrice * (1 + config.trailingStopPct / 100);
    const trailingPrice = trailingArmed
      ? peakPrice * (1 - config.trailingStopPct / 100)
      : position.stopPrice;
    const effectiveStop = Math.max(position.stopPrice, trailingPrice);
    const holdMinutes = (now - position.openedAt) / 60_000;

    let reason: PaperExitReason | null = null;
    if (price <= position.stopPrice) reason = 'STOP_LOSS';
    else if (trailingArmed && price <= effectiveStop) reason = 'TRAILING_STOP';
    else if (price >= position.takeProfitPrice) reason = 'TAKE_PROFIT';
    else if (holdMinutes >= config.maxHoldMinutes) reason = 'MAX_HOLD';

    if (reason) closed.push(closePosition({ ...position, peakPrice }, price, reason));
    else stillOpen.push({ ...position, currentPrice: price, peakPrice, updatedAt: now });
  }

  const realizedDelta = closed.reduce((sum, trade) => sum + trade.pnlUsd, 0);
  const next = {
    ...normalized,
    balanceUsd: normalized.balanceUsd + realizedDelta,
    realizedPnlUsd: normalized.realizedPnlUsd + realizedDelta,
    dailyPnlUsd: normalized.dailyPnlUsd + realizedDelta,
    positions: stillOpen,
    trades: [...closed, ...normalized.trades].slice(0, MAX_TRADE_HISTORY),
    lastEvent: closed.length
      ? `${closed.length} paper position(s) closed. Realized ${realizedDelta >= 0 ? '+' : ''}$${realizedDelta.toFixed(2)}.`
      : normalized.lastEvent,
  };
  return savePaperState(next);
}export function closePaperPositionManually(
  state: PaperState,
  positionId: string,
): PaperState {
  const normalized = normalizePaperDay(state);
  const position = normalized.positions.find((item) => item.id === positionId);
  if (!position) return normalized;
  const closed = closePosition(position, position.currentPrice, 'MANUAL');
  return savePaperState({
    ...normalized,
    balanceUsd: normalized.balanceUsd + closed.pnlUsd,
    realizedPnlUsd: normalized.realizedPnlUsd + closed.pnlUsd,
    dailyPnlUsd: normalized.dailyPnlUsd + closed.pnlUsd,
    positions: normalized.positions.filter((item) => item.id !== positionId),
    trades: [closed, ...normalized.trades].slice(0, MAX_TRADE_HISTORY),
    lastEvent: `Closed PAPER ${closed.symbol} manually: ${closed.pnlUsd >= 0 ? '+' : ''}$${closed.pnlUsd.toFixed(2)}.`,
  });
}

export function resetPaperState(config = DEFAULT_PAPER_RISK_CONFIG): PaperState {
  const next = createPaperState(config);
  return savePaperState(next);
}

export function getPaperStats(state: PaperState) {
  const wins = state.trades.filter((trade) => trade.pnlUsd > 0).length;
  const losses = state.trades.filter((trade) => trade.pnlUsd < 0).length;
  const closed = state.trades.length;
  const winRate = closed > 0 ? (wins / closed) * 100 : 0;
  const unrealizedPnlUsd = state.positions.reduce(
    (sum, position) => sum + position.quantity * (position.currentPrice - position.entryPrice),
    0,
  );
  return { wins, losses, closed, winRate, unrealizedPnlUsd };
}
