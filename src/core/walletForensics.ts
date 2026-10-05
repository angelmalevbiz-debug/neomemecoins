import { rpcCall } from './providers/solanaRpc';
import type {
  DeviceSettings,
  FundingCluster,
  FundingTimeCluster,
  HolderSnapshot,
  WalletForensicsSnapshot,
  WalletFundingEvidence,
} from './types';

interface SignatureEntry {
  signature: string;
  blockTime?: number | null;
  err?: unknown;
}

function clamp(value: number, min = 0, max = 100): number {
  return Math.min(max, Math.max(min, value));
}

function toNumber(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function extractIncomingSystemTransfers(transaction: any, wallet: string): Array<{
  source: string;
  lamports: number;
}> {
  const result: Array<{ source: string; lamports: number }> = [];

  const inspectInstruction = (instruction: any) => {
    const parsed = instruction?.parsed;
    const info = parsed?.info;
    if (!parsed || !info) return;

    const type = String(parsed.type || '').toLowerCase();
    const program = String(instruction?.program || '').toLowerCase();
    if (program !== 'system' || !type.includes('transfer')) return;

    const destination = String(info.destination || info.to || '');
    const source = String(info.source || info.from || '');
    const lamports = toNumber(info.lamports);

    if (destination === wallet && source && source !== wallet && lamports > 0) {
      result.push({ source, lamports });
    }
  };

  const outer = transaction?.transaction?.message?.instructions;
  if (Array.isArray(outer)) outer.forEach(inspectInstruction);

  const innerGroups = transaction?.meta?.innerInstructions;
  if (Array.isArray(innerGroups)) {
    for (const group of innerGroups) {
      if (Array.isArray(group?.instructions)) group.instructions.forEach(inspectInstruction);
    }
  }

  return result;
}

async function inspectFundingEvidence(
  wallet: string,
  holderPercentage: number,
  settings: DeviceSettings,
): Promise<WalletFundingEvidence> {
  const signatureLimit = Math.min(Math.max(settings.freshWalletMaxSampledSignatures + 1, 20), 80);
  const signatures = await rpcCall<SignatureEntry[]>(settings.rpcUrl, 'getSignaturesForAddress', [
    wallet,
    { limit: signatureLimit, commitment: 'confirmed' },
  ]);

  const valid = signatures.filter((entry) => entry && !entry.err && entry.signature);
  const validTimes = valid
    .map((entry) => toNumber(entry.blockTime))
    .filter((value) => value > 0);

  const oldestSeconds = validTimes.length ? Math.min(...validTimes) : undefined;
  const oldestSampledSignatureAt = oldestSeconds ? oldestSeconds * 1000 : undefined;
  const freshCutoff = Date.now() - settings.freshWalletMaxAgeHours * 60 * 60 * 1000;
  const likelyFresh = valid.length <= settings.freshWalletMaxSampledSignatures
    && Boolean(oldestSampledSignatureAt && oldestSampledSignatureAt >= freshCutoff);

  const evidence: WalletFundingEvidence = {
    wallet,
    holderPercentage,
    likelyFresh,
    sampledSignatureCount: valid.length,
    oldestSampledSignatureAt,
  };

  // Funding-source inference is intentionally conservative and bounded. We only inspect
  // a handful of the oldest transactions visible in the local sample so this can run
  // from the browser without an owner-paid indexing API.
  const oldestCandidates = [...valid]
    .sort((a, b) => toNumber(a.blockTime) - toNumber(b.blockTime))
    .slice(0, 5);

  let earliest: { source: string; lamports: number; at: number } | undefined;

  for (const entry of oldestCandidates) {
    try {
      const transaction = await rpcCall<any>(settings.rpcUrl, 'getTransaction', [
        entry.signature,
        {
          encoding: 'jsonParsed',
          commitment: 'confirmed',
          maxSupportedTransactionVersion: 0,
        },
      ]);

      if (!transaction) continue;
      const at = toNumber(transaction?.blockTime || entry.blockTime) * 1000;
      const transfers = extractIncomingSystemTransfers(transaction, wallet);

      for (const transfer of transfers) {
        if (!earliest || (at > 0 && at < earliest.at)) {
          earliest = { source: transfer.source, lamports: transfer.lamports, at };
        }
      }
    } catch {
      // Public RPC nodes frequently prune or rate-limit historical transactions.
      // Missing one transaction must not fail the full token assessment.
    }
  }

  if (earliest) {
    evidence.firstFundingSource = earliest.source;
    evidence.firstFundingLamports = earliest.lamports;
    evidence.firstFundingAt = earliest.at;
  }

  return evidence;
}

function buildFundingClusters(evidence: WalletFundingEvidence[]): FundingCluster[] {
  const bySource = new Map<string, WalletFundingEvidence[]>();

  for (const item of evidence) {
    if (!item.firstFundingSource) continue;
    const existing = bySource.get(item.firstFundingSource) || [];
    existing.push(item);
    bySource.set(item.firstFundingSource, existing);
  }

  return [...bySource.entries()]
    .filter(([, wallets]) => wallets.length >= 2)
    .map(([source, wallets]) => {
      const fundingTimes = wallets
        .map((wallet) => wallet.firstFundingAt)
        .filter((value): value is number => Boolean(value));

      return {
        source,
        wallets: wallets.map((wallet) => wallet.wallet),
        holderSupplyPct: wallets.reduce((sum, wallet) => sum + wallet.holderPercentage, 0),
        firstFundingAtMin: fundingTimes.length ? Math.min(...fundingTimes) : undefined,
        firstFundingAtMax: fundingTimes.length ? Math.max(...fundingTimes) : undefined,
      };
    })
    .sort((a, b) => b.wallets.length - a.wallets.length || b.holderSupplyPct - a.holderSupplyPct);
}

function buildTimeClusters(
  evidence: WalletFundingEvidence[],
  windowMinutes: number,
): FundingTimeCluster[] {
  const withTime = evidence
    .filter((item): item is WalletFundingEvidence & { firstFundingAt: number } => Boolean(item.firstFundingAt))
    .sort((a, b) => a.firstFundingAt - b.firstFundingAt);

  const windowMs = Math.max(1, windowMinutes) * 60_000;
  const clusters = new Map<string, FundingTimeCluster>();

  for (let start = 0; start < withTime.length; start += 1) {
    const group = withTime.filter((item) => (
      item.firstFundingAt >= withTime[start].firstFundingAt
      && item.firstFundingAt - withTime[start].firstFundingAt <= windowMs
    ));

    if (group.length < 2) continue;
    const wallets = group.map((item) => item.wallet).sort();
    const key = wallets.join('|');
    const startAt = Math.min(...group.map((item) => item.firstFundingAt));
    const endAt = Math.max(...group.map((item) => item.firstFundingAt));

    clusters.set(key, {
      wallets,
      holderSupplyPct: group.reduce((sum, item) => sum + item.holderPercentage, 0),
      startAt,
      endAt,
      spreadMinutes: (endAt - startAt) / 60_000,
    });
  }

  return [...clusters.values()]
    .sort((a, b) => b.wallets.length - a.wallets.length || a.spreadMinutes - b.spreadMinutes)
    .filter((cluster, index, all) => !all.slice(0, index).some((bigger) => (
      cluster.wallets.every((wallet) => bigger.wallets.includes(wallet))
    )));
}

export async function analyzeWalletForensics(
  holders: HolderSnapshot,
  settings: DeviceSettings,
): Promise<WalletForensicsSnapshot> {
  const warnings: string[] = [];
  const uniqueOwners = holders.holders
    .filter((holder) => holder.owner)
    .filter((holder, index, all) => all.findIndex((item) => item.owner === holder.owner) === index)
    .slice(0, Math.min(Math.max(settings.maxForensicWallets, 2), 8));

  if (uniqueOwners.length < 2) {
    return {
      sampledWallets: uniqueOwners.length,
      walletsWithFundingEvidence: 0,
      freshWallets: uniqueOwners.filter((holder) => holder.likelyFresh).length,
      commonFundingClusters: [],
      synchronizedFundingClusters: [],
      linkedWalletPct: 0,
      linkedHolderSupplyPct: 0,
      confidence: 10,
      evidence: [],
      warnings: ['Not enough holder owner addresses were available for funding-link analysis.'],
    };
  }

  const evidence: WalletFundingEvidence[] = [];
  for (const holder of uniqueOwners) {
    try {
      evidence.push(await inspectFundingEvidence(holder.owner!, holder.percentage, settings));
    } catch (error) {
      const message = error instanceof Error ? error.message : 'RPC error';
      warnings.push(`${holder.owner!.slice(0, 5)}… funding scan failed: ${message}`);
    }
  }

  const commonFundingClusters = buildFundingClusters(evidence);
  const synchronizedFundingClusters = buildTimeClusters(evidence, settings.fundingTimeWindowMinutes);
  const linkedWallets = new Set(commonFundingClusters.flatMap((cluster) => cluster.wallets));
  const linkedEvidence = evidence.filter((item) => linkedWallets.has(item.wallet));

  const walletsWithFundingEvidence = evidence.filter((item) => item.firstFundingSource && item.firstFundingAt).length;
  const freshWallets = evidence.filter((item) => item.likelyFresh).length;
  const evidenceCoverage = evidence.length / uniqueOwners.length;
  const fundingCoverage = evidence.length ? walletsWithFundingEvidence / evidence.length : 0;
  const confidence = clamp(Math.round(20 + evidenceCoverage * 35 + fundingCoverage * 45));

  if (walletsWithFundingEvidence < Math.min(3, evidence.length)) {
    warnings.push('Funding-source coverage is partial. Public RPC history can be pruned or rate-limited.');
  }
  if (commonFundingClusters.length > 0) {
    warnings.push('A shared funding source is a coordination clue, not proof of a bundle: exchanges, bridges and funding services can create false positives.');
  }

  return {
    sampledWallets: evidence.length,
    walletsWithFundingEvidence,
    freshWallets,
    commonFundingClusters,
    synchronizedFundingClusters,
    linkedWalletPct: evidence.length ? (linkedWallets.size / evidence.length) * 100 : 0,
    linkedHolderSupplyPct: linkedEvidence.reduce((sum, item) => sum + item.holderPercentage, 0),
    confidence,
    evidence,
    warnings,
  };
}
