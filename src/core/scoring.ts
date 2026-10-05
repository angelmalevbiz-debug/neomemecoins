import type {
  AnalysisSignal,
  HolderSnapshot,
  MarketSnapshot,
  NarrativeSnapshot,
  RiskAssessment,
  TradePosture,
  WalletForensicsSnapshot,
} from './types';
import { detectSuspiciousStaircase } from './localHistory';
import type { LocalPricePoint } from './types';

function clamp(value: number, min = 0, max = 100): number {
  return Math.min(max, Math.max(min, value));
}

function pct(value: number): string {
  return `${value.toFixed(value >= 10 ? 1 : 2)}%`;
}

function money(value: number): string {
  if (!Number.isFinite(value)) return '$0';
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(1)}K`;
  return `$${value.toFixed(0)}`;
}

export function scoreToken(input: {
  market: MarketSnapshot;
  holders?: HolderSnapshot;
  forensics?: WalletForensicsSnapshot;
  narrative: NarrativeSnapshot;
  priceHistory?: LocalPricePoint[];
  dataWarnings?: string[];
}): RiskAssessment {
  const {
    market,
    holders,
    forensics,
    narrative,
    priceHistory = [],
    dataWarnings = [],
  } = input;
  const signals: AnalysisSignal[] = [];

  const push = (signal: AnalysisSignal) => signals.push(signal);

  let risk = 8;
  let confidence = 35;

  const cap = market.marketCapUsd || market.fdvUsd;
  const liquidityRatio = cap > 0 ? (market.liquidityUsd / cap) * 100 : 0;

  if (market.liquidityUsd < 5_000) {
    push({
      id: 'liquidity-absolute-critical',
      title: 'Very thin liquidity',
      detail: `${money(market.liquidityUsd)} liquidity leaves the token vulnerable to severe slippage and fast price collapse.`,
      severity: 'critical',
      riskPoints: 24,
      confidence: 95,
    });
  } else if (market.liquidityUsd < 15_000) {
    push({
      id: 'liquidity-absolute-low',
      title: 'Low liquidity',
      detail: `${money(market.liquidityUsd)} liquidity is still fragile for active trading.`,
      severity: 'warning',
      riskPoints: 13,
      confidence: 95,
    });
  } else if (market.liquidityUsd >= 50_000) {
    push({
      id: 'liquidity-absolute-good',
      title: 'Meaningful liquidity',
      detail: `${money(market.liquidityUsd)} of visible pair liquidity gives the market more depth.`,
      severity: 'positive',
      riskPoints: -4,
      confidence: 90,
    });
  }

  if (cap > 0) {
    confidence += 8;
    if (liquidityRatio < 2) {
      push({
        id: 'liquidity-ratio-critical',
        title: 'Liquidity is tiny vs market cap',
        detail: `Liquidity / market cap is only ${pct(liquidityRatio)}. Small sells can move price violently.`,
        severity: 'critical',
        riskPoints: 24,
        confidence: 95,
      });
    } else if (liquidityRatio < 5) {
      push({
        id: 'liquidity-ratio-low',
        title: 'Weak liquidity ratio',
        detail: `Liquidity / market cap is ${pct(liquidityRatio)}. Treat the 10–20% idea as a reference, not a hard rule.`,
        severity: 'warning',
        riskPoints: 14,
        confidence: 90,
      });
    } else if (liquidityRatio < 10) {
      push({
        id: 'liquidity-ratio-medium',
        title: 'Liquidity ratio needs caution',
        detail: `Liquidity / market cap is ${pct(liquidityRatio)}.`,
        severity: 'warning',
        riskPoints: 7,
        confidence: 85,
      });
    } else {
      push({
        id: 'liquidity-ratio-good',
        title: 'Healthy visible liquidity ratio',
        detail: `Liquidity / market cap is ${pct(liquidityRatio)}. This is supportive, but does not prove safety.`,
        severity: 'positive',
        riskPoints: -5,
        confidence: 85,
      });
    }
  }

  if (market.pairCreatedAt) {
    confidence += 5;
    const ageMinutes = Math.max(0, (Date.now() - market.pairCreatedAt) / 60_000);
    if (ageMinutes < 5) {
      push({
        id: 'pair-age-new',
        title: 'Extremely new pair',
        detail: `Pair age is about ${ageMinutes.toFixed(1)} minutes. Data is too young to trust strongly.`,
        severity: 'warning',
        riskPoints: 8,
        confidence: 90,
      });
    } else if (ageMinutes < 30) {
      push({
        id: 'pair-age-early',
        title: 'Very early market',
        detail: `Pair age is about ${ageMinutes.toFixed(0)} minutes. Early upside comes with much less evidence.`,
        severity: 'info',
        riskPoints: 4,
        confidence: 85,
      });
    }
  }

  const txns1h = market.buys1h + market.sells1h;
  if (txns1h > 0) {
    confidence += 5;
    const buyShare = market.buys1h / txns1h;
    if (buyShare > 0.92 || buyShare < 0.08) {
      push({
        id: 'txn-imbalance',
        title: 'Extreme transaction imbalance',
        detail: `${(buyShare * 100).toFixed(0)}% of 1h transactions are buys. Extreme one-sided flow can be organic, botted, or manipulated.`,
        severity: 'warning',
        riskPoints: 8,
        confidence: 70,
      });
    }
  }

  if (market.liquidityUsd > 0 && market.volume1hUsd > 0) {
    const turnover = market.volume1hUsd / market.liquidityUsd;
    if (turnover > 15) {
      push({
        id: 'volume-liquidity-extreme',
        title: 'Extreme volume vs liquidity',
        detail: `1h volume is ${turnover.toFixed(1)}× visible liquidity. This deserves fake-volume/bot scrutiny.`,
        severity: 'warning',
        riskPoints: 10,
        confidence: 75,
      });
    }
  }

  if (holders) {
    confidence += 24;
    if (holders.top1Pct >= 20) {
      push({
        id: 'holder-top1-critical',
        title: 'Single-account concentration is very high',
        detail: `Largest raw token account holds ${pct(holders.top1Pct)}. Raw RPC data may include treasury/burn/LP accounts, so verify the owner before concluding it is malicious.`,
        severity: 'critical',
        riskPoints: 22,
        confidence: 82,
      });
    } else if (holders.top1Pct >= 10) {
      push({
        id: 'holder-top1-high',
        title: 'Large top holder',
        detail: `Largest raw token account holds ${pct(holders.top1Pct)}.`,
        severity: 'warning',
        riskPoints: 12,
        confidence: 82,
      });
    }

    if (holders.top5Pct >= 55) {
      push({
        id: 'holder-top5-critical',
        title: 'Top holders control most supply',
        detail: `Top 5 raw token accounts hold ${pct(holders.top5Pct)} combined.`,
        severity: 'critical',
        riskPoints: 20,
        confidence: 82,
      });
    } else if (holders.top5Pct >= 35) {
      push({
        id: 'holder-top5-high',
        title: 'Concentrated holder distribution',
        detail: `Top 5 raw token accounts hold ${pct(holders.top5Pct)} combined.`,
        severity: 'warning',
        riskPoints: 10,
        confidence: 82,
      });
    } else {
      push({
        id: 'holder-top5-distributed',
        title: 'Top accounts are relatively distributed',
        detail: `Top 5 raw token accounts hold ${pct(holders.top5Pct)} combined.`,
        severity: 'positive',
        riskPoints: -4,
        confidence: 75,
      });
    }

    if (holders.sampledFreshWalletPct !== undefined) {
      confidence += 6;
      if (holders.sampledFreshWalletPct >= 60) {
        push({
          id: 'fresh-wallets-critical',
          title: 'Many sampled top wallets look fresh',
          detail: `${pct(holders.sampledFreshWalletPct)} of sampled top-holder owners have short, recent visible histories. This can indicate coordinated wallet creation, but it is not proof.`,
          severity: 'critical',
          riskPoints: 18,
          confidence: 70,
        });
      } else if (holders.sampledFreshWalletPct >= 35) {
        push({
          id: 'fresh-wallets-warning',
          title: 'Fresh-wallet concentration detected',
          detail: `${pct(holders.sampledFreshWalletPct)} of sampled top-holder owners look fresh by the local heuristic.`,
          severity: 'warning',
          riskPoints: 9,
          confidence: 65,
        });
      }
    }
  } else {
    dataWarnings.push('Holder data was not available. Risk confidence is reduced.');
  }

  if (forensics) {
    confidence += Math.round((forensics.confidence / 100) * 12);

    const biggestFundingCluster = forensics.commonFundingClusters[0];
    if (biggestFundingCluster) {
      if (biggestFundingCluster.wallets.length >= 3) {
        push({
          id: 'common-funder-critical',
          title: 'Multiple top wallets share one funding source',
          detail: `${biggestFundingCluster.wallets.length} sampled holder wallets were first funded by the same visible source and together represent about ${pct(biggestFundingCluster.holderSupplyPct)} of supply. This is a strong coordination clue, but exchange/bridge funding can create false positives.`,
          severity: 'critical',
          riskPoints: 22,
          confidence: Math.max(60, forensics.confidence),
        });
      } else {
        push({
          id: 'common-funder-warning',
          title: 'Two top wallets share a funding source',
          detail: `Two sampled holder wallets share the same visible funding source and represent about ${pct(biggestFundingCluster.holderSupplyPct)} of supply. Verify whether the source is an exchange, bridge or private funder.`,
          severity: 'warning',
          riskPoints: 9,
          confidence: Math.max(55, forensics.confidence),
        });
      }
    } else if (forensics.walletsWithFundingEvidence >= 3) {
      push({
        id: 'common-funder-clear',
        title: 'No common funder found in the sampled wallets',
        detail: `${forensics.walletsWithFundingEvidence} wallets had usable first-funding evidence and no shared source cluster was found.`,
        severity: 'positive',
        riskPoints: -3,
        confidence: forensics.confidence,
      });
    }

    const biggestTimeCluster = forensics.synchronizedFundingClusters[0];
    if (biggestTimeCluster && biggestTimeCluster.wallets.length >= 3) {
      push({
        id: 'funding-time-cluster',
        title: 'Synchronized wallet funding detected',
        detail: `${biggestTimeCluster.wallets.length} sampled wallets were funded within ${biggestTimeCluster.spreadMinutes.toFixed(1)} minutes and together represent about ${pct(biggestTimeCluster.holderSupplyPct)} of supply. Time correlation alone is not proof of common control.`,
        severity: 'warning',
        riskPoints: 14,
        confidence: Math.max(55, forensics.confidence - 5),
      });
    }

    if (forensics.linkedWalletPct >= 50 && forensics.commonFundingClusters.length > 0) {
      push({
        id: 'linked-wallet-share',
        title: 'Large part of the sample is funding-linked',
        detail: `${pct(forensics.linkedWalletPct)} of sampled wallets are in a shared-funder cluster, representing roughly ${pct(forensics.linkedHolderSupplyPct)} of token supply.`,
        severity: forensics.linkedWalletPct >= 70 ? 'critical' : 'warning',
        riskPoints: forensics.linkedWalletPct >= 70 ? 12 : 7,
        confidence: forensics.confidence,
      });
    }
  }

  const chartPattern = detectSuspiciousStaircase(priceHistory);
  if (chartPattern.suspicious) {
    push({
      id: 'staircase-pattern',
      title: 'Suspicious staircase-like local price pattern',
      detail: chartPattern.detail,
      severity: 'warning',
      riskPoints: 10,
      confidence: 55,
    });
  } else if (priceHistory.length >= 10) {
    push({
      id: 'staircase-clear',
      title: 'No strong staircase signal in local samples',
      detail: chartPattern.detail,
      severity: 'positive',
      riskPoints: -2,
      confidence: 50,
    });
  } else {
    push({
      id: 'chart-learning',
      title: 'Local chart pattern is still learning',
      detail: chartPattern.detail,
      severity: 'info',
      riskPoints: 0,
      confidence: 100,
    });
  }

  if (market.websiteCount === 0 && market.socials.length === 0) {
    push({
      id: 'metadata-empty',
      title: 'No visible project links',
      detail: 'DEX metadata contains no website or social links. This is a weak signal by itself, but reduces available evidence.',
      severity: 'warning',
      riskPoints: 5,
      confidence: 70,
    });
  } else {
    push({
      id: 'metadata-present',
      title: 'Project links are present',
      detail: `${market.websiteCount} website link(s), ${market.socials.length} social reference(s). Presence does not prove legitimacy.`,
      severity: 'info',
      riskPoints: 0,
      confidence: 90,
    });
  }

  push({
    id: 'narrative',
    title: `Narrative: ${narrative.category.replace('-', ' ')}`,
    detail: narrative.explanation,
    severity: 'info',
    riskPoints: 0,
    confidence: narrative.confidence,
  });

  for (const signal of signals) risk += signal.riskPoints;
  risk = clamp(Math.round(risk));
  confidence = clamp(Math.round(confidence));
  const qualityScore = clamp(100 - risk);

  let posture: TradePosture;
  if (risk >= 65) posture = 'SKIP';
  else if (risk >= 45) posture = 'WAIT';
  else if (risk >= 25 || confidence < 60) posture = 'WATCH';
  else posture = 'SETUP';

  const summaryByPosture: Record<TradePosture, string> = {
    SKIP: 'Risk signals are too strong for the current evidence. Avoid forcing a trade and inspect the critical flags first.',
    WAIT: 'There are meaningful red flags or missing evidence. Wait for cleaner distribution, liquidity or history before considering a setup.',
    WATCH: 'No decisive critical failure is visible, but the evidence is not strong enough for an automatic entry. Keep it on watch.',
    SETUP: 'The current deterministic checks are relatively clean. This means “eligible for your strategy setup”, not “guaranteed buy”.',
  };

  return {
    tokenAddress: market.tokenAddress,
    generatedAt: Date.now(),
    market,
    holders,
    forensics,
    narrative,
    riskScore: risk,
    qualityScore,
    confidenceScore: confidence,
    posture,
    signals: signals.sort((a, b) => b.riskPoints - a.riskPoints),
    summary: summaryByPosture[posture],
    dataWarnings,
  };
}
