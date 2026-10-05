import type { MarketSnapshot } from '../types';

const BASE_URL = 'https://api.dexscreener.com';

interface DexPair {
  chainId?: string;
  dexId?: string;
  pairAddress?: string;
  baseToken?: { address?: string; name?: string; symbol?: string };
  quoteToken?: { address?: string; name?: string; symbol?: string };
  priceUsd?: string;
  marketCap?: number;
  fdv?: number;
  liquidity?: { usd?: number };
  volume?: Record<string, number>;
  txns?: Record<string, { buys?: number; sells?: number }>;
  priceChange?: Record<string, number>;
  pairCreatedAt?: number;
  info?: {
    websites?: Array<{ url?: string }>;
    socials?: Array<{ platform?: string; handle?: string }>;
  };
  boosts?: { active?: number };
}

function numberOrZero(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

export async function fetchSolanaTokenMarket(tokenAddress: string): Promise<MarketSnapshot> {
  const response = await fetch(`${BASE_URL}/token-pairs/v1/solana/${encodeURIComponent(tokenAddress)}`);
  if (!response.ok) {
    throw new Error(`DEX Screener error ${response.status}`);
  }

  const payload = await response.json();
  const pairs: DexPair[] = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.pairs)
      ? payload.pairs
      : [];

  // DEX Screener pair fields (price, cap, txns, metadata) are expressed from the
  // base-token perspective. Never mix a quote-side pair with holder data for the
  // requested mint; only score pairs where the requested token is the base asset.
  const requestedAddress = tokenAddress.trim();
  const solanaPairs = pairs.filter(
    (pair) => pair?.chainId === 'solana' && pair?.baseToken?.address === requestedAddress,
  );

  if (!solanaPairs.length) {
    throw new Error('No active Solana pair with this mint as the base token was found. Quote-side pairs are ignored to avoid mixing market data from another asset.');
  }

  const best = [...solanaPairs].sort(
    (a, b) => numberOrZero(b?.liquidity?.usd) - numberOrZero(a?.liquidity?.usd),
  )[0];

  const socials = (best.info?.socials || [])
    .map((social) => social.platform || social.handle || '')
    .filter(Boolean);

  return {
    tokenAddress,
    pairAddress: best.pairAddress,
    name: best.baseToken?.name || 'Unknown token',
    symbol: best.baseToken?.symbol || tokenAddress.slice(0, 5).toUpperCase(),
    priceUsd: numberOrZero(best.priceUsd),
    marketCapUsd: numberOrZero(best.marketCap),
    fdvUsd: numberOrZero(best.fdv),
    liquidityUsd: numberOrZero(best.liquidity?.usd),
    volume5mUsd: numberOrZero(best.volume?.m5),
    volume1hUsd: numberOrZero(best.volume?.h1),
    volume24hUsd: numberOrZero(best.volume?.h24),
    buys5m: numberOrZero(best.txns?.m5?.buys),
    sells5m: numberOrZero(best.txns?.m5?.sells),
    buys1h: numberOrZero(best.txns?.h1?.buys),
    sells1h: numberOrZero(best.txns?.h1?.sells),
    priceChange5m: numberOrZero(best.priceChange?.m5),
    priceChange1h: numberOrZero(best.priceChange?.h1),
    priceChange24h: numberOrZero(best.priceChange?.h24),
    pairCreatedAt: best.pairCreatedAt,
    dexId: best.dexId,
    websiteCount: best.info?.websites?.length || 0,
    socials,
    boostsActive: numberOrZero(best.boosts?.active),
  };
}

export async function fetchLatestSolanaProfiles(limit = 20): Promise<Array<{ tokenAddress: string; description: string }>> {
  const response = await fetch(`${BASE_URL}/token-profiles/latest/v1`);
  if (!response.ok) return [];

  const payload = await response.json();
  if (!Array.isArray(payload)) return [];

  return payload
    .filter((item) => item?.chainId === 'solana' && item?.tokenAddress)
    .slice(0, limit)
    .map((item) => ({
      tokenAddress: String(item.tokenAddress),
      description: String(item.description || ''),
    }));
}
