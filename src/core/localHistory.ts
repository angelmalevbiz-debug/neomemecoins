import type { LocalPricePoint } from './types';

const PREFIX = 'neo-meme-history:';
const MAX_POINTS = 240;

function storageAvailable(): boolean {
  return typeof window !== 'undefined' && Boolean(window.localStorage);
}

export function recordPrice(tokenAddress: string, priceUsd: number): LocalPricePoint[] {
  if (!storageAvailable() || !Number.isFinite(priceUsd) || priceUsd <= 0) return [];
  const key = `${PREFIX}${tokenAddress}`;
  const current = getPriceHistory(tokenAddress);
  const next = [...current, { ts: Date.now(), priceUsd }].slice(-MAX_POINTS);
  localStorage.setItem(key, JSON.stringify(next));
  return next;
}

export function getPriceHistory(tokenAddress: string): LocalPricePoint[] {
  if (!storageAvailable()) return [];
  try {
    const raw = localStorage.getItem(`${PREFIX}${tokenAddress}`);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter((point) => Number.isFinite(point?.ts) && Number.isFinite(point?.priceUsd))
      : [];
  } catch {
    return [];
  }
}

export function detectSuspiciousStaircase(history: LocalPricePoint[]): {
  suspicious: boolean;
  score: number;
  detail: string;
} {
  if (history.length < 10) {
    return {
      suspicious: false,
      score: 0,
      detail: `Need at least 10 local price samples; currently ${history.length}.`,
    };
  }

  const returns: number[] = [];
  for (let i = 1; i < history.length; i += 1) {
    const prev = history[i - 1].priceUsd;
    const next = history[i].priceUsd;
    if (prev > 0) returns.push((next - prev) / prev);
  }

  const positives = returns.filter((value) => value > 0).length;
  const negatives = returns.filter((value) => value < 0).length;
  const positiveShare = returns.length ? positives / returns.length : 0;
  const negativeShare = returns.length ? negatives / returns.length : 0;
  const averagePositive = returns.filter((r) => r > 0).reduce((a, b) => a + b, 0) / Math.max(positives, 1);
  const maxPullback = Math.abs(Math.min(0, ...returns));

  let score = 0;
  if (positiveShare >= 0.8) score += 45;
  else if (positiveShare >= 0.7) score += 25;

  if (negativeShare <= 0.12) score += 25;
  if (averagePositive > 0 && maxPullback < averagePositive * 0.75) score += 20;
  if (returns.length >= 20) score += 10;

  score = Math.min(100, score);
  return {
    suspicious: score >= 65,
    score,
    detail: `Local pattern score ${score}/100 from ${history.length} samples. This is a heuristic, not proof of fake volume.`,
  };
}
