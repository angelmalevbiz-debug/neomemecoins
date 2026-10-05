import type { DeviceSettings, HolderAccount, HolderSnapshot } from '../types';

interface RpcResponse<T> {
  jsonrpc: string;
  id: number;
  result?: T;
  error?: { code: number; message: string };
}

let rpcId = 1;

export async function rpcCall<T>(rpcUrl: string, method: string, params: unknown[]): Promise<T> {
  const response = await fetch(rpcUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: rpcId++, method, params }),
  });

  if (!response.ok) {
    throw new Error(`Solana RPC HTTP ${response.status}`);
  }

  const payload = (await response.json()) as RpcResponse<T>;
  if (payload.error) {
    throw new Error(`Solana RPC ${payload.error.code}: ${payload.error.message}`);
  }
  if (payload.result === undefined) {
    throw new Error(`Solana RPC returned no result for ${method}`);
  }
  return payload.result;
}

function toNumber(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

export async function getHolderSnapshot(
  tokenAddress: string,
  settings: DeviceSettings,
): Promise<HolderSnapshot> {
  const [supplyResult, largestResult] = await Promise.all([
    rpcCall<any>(settings.rpcUrl, 'getTokenSupply', [tokenAddress, { commitment: 'confirmed' }]),
    rpcCall<any>(settings.rpcUrl, 'getTokenLargestAccounts', [tokenAddress, { commitment: 'confirmed' }]),
  ]);

  const supply = toNumber(supplyResult?.value?.uiAmountString ?? supplyResult?.value?.uiAmount);
  const decimals = toNumber(supplyResult?.value?.decimals);
  const largest = Array.isArray(largestResult?.value) ? largestResult.value.slice(0, 20) : [];

  if (!supply || !largest.length) {
    throw new Error('Holder distribution is unavailable from the selected RPC endpoint.');
  }

  const tokenAccounts = largest.map((entry: any) => String(entry.address));
  let parsedAccounts: any[] = [];

  try {
    const parsed = await rpcCall<any>(settings.rpcUrl, 'getMultipleAccounts', [
      tokenAccounts,
      { encoding: 'jsonParsed', commitment: 'confirmed' },
    ]);
    parsedAccounts = Array.isArray(parsed?.value) ? parsed.value : [];
  } catch {
    parsedAccounts = [];
  }

  const holders: HolderAccount[] = largest.map((entry: any, index: number) => {
    const uiAmount = toNumber(entry.uiAmountString ?? entry.uiAmount);
    const parsedInfo = parsedAccounts[index]?.data?.parsed?.info;
    return {
      tokenAccount: String(entry.address),
      owner: parsedInfo?.owner ? String(parsedInfo.owner) : undefined,
      amountRaw: String(entry.amount || '0'),
      uiAmount,
      percentage: supply > 0 ? (uiAmount / supply) * 100 : 0,
    };
  });

  if (settings.deepWalletScan) {
    const sample = holders.filter((holder) => holder.owner).slice(0, 8);
    for (const holder of sample) {
      try {
        const freshness = await inspectWalletFreshness(holder.owner!, settings);
        holder.likelyFresh = freshness.likelyFresh;
        holder.sampledSignatureCount = freshness.sampledSignatureCount;
        holder.oldestSampledSignatureAt = freshness.oldestSampledSignatureAt;
      } catch {
        holder.likelyFresh = undefined;
      }
    }
  }

  const percentages = holders.map((holder) => holder.percentage);
  const sum = (count: number) => percentages.slice(0, count).reduce((total, value) => total + value, 0);

  const freshScanned = holders.filter((holder) => holder.likelyFresh !== undefined);
  const freshCount = freshScanned.filter((holder) => holder.likelyFresh).length;

  return {
    supply,
    decimals,
    holders,
    top1Pct: sum(1),
    top5Pct: sum(5),
    top10Pct: sum(10),
    sampledFreshWalletPct: freshScanned.length ? (freshCount / freshScanned.length) * 100 : undefined,
    dataComplete: parsedAccounts.length === tokenAccounts.length,
  };
}

export async function inspectWalletFreshness(
  walletAddress: string,
  settings: DeviceSettings,
): Promise<{
  likelyFresh: boolean;
  sampledSignatureCount: number;
  oldestSampledSignatureAt?: number;
}> {
  const limit = Math.min(Math.max(settings.freshWalletMaxSampledSignatures + 1, 10), 100);
  const signatures = await rpcCall<any[]>(settings.rpcUrl, 'getSignaturesForAddress', [
    walletAddress,
    { limit, commitment: 'confirmed' },
  ]);

  const validTimes = signatures
    .map((entry) => toNumber(entry?.blockTime))
    .filter((value) => value > 0);
  const oldestSeconds = validTimes.length ? Math.min(...validTimes) : undefined;
  const oldestSampledSignatureAt = oldestSeconds ? oldestSeconds * 1000 : undefined;
  const cutoff = Date.now() - settings.freshWalletMaxAgeHours * 60 * 60 * 1000;

  // We only call a wallet "likely fresh" when the sampled history is short AND
  // its oldest visible transaction is recent. A long sample is treated as established.
  const historyLooksShort = signatures.length <= settings.freshWalletMaxSampledSignatures;
  const oldestIsRecent = Boolean(oldestSampledSignatureAt && oldestSampledSignatureAt >= cutoff);

  return {
    likelyFresh: historyLooksShort && oldestIsRecent,
    sampledSignatureCount: signatures.length,
    oldestSampledSignatureAt,
  };
}
