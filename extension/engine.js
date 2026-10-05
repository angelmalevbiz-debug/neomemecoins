const DEFAULT_RPC = 'https://api.mainnet.solana.com';
let rpcId = 1;

function n(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function clamp(value, min = 0, max = 100) {
  return Math.min(max, Math.max(min, value));
}

function pct(value, digits = value >= 10 ? 1 : 2) {
  return `${n(value).toFixed(digits)}%`;
}

function money(value) {
  const v = n(value);
  if (v >= 1_000_000_000) return `$${(v / 1_000_000_000).toFixed(2)}B`;
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

async function rpcCall(rpcUrl, method, params) {
  const response = await fetch(rpcUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: rpcId++, method, params }),
  });
  if (!response.ok) throw new Error(`RPC HTTP ${response.status}`);
  const payload = await response.json();
  if (payload.error) throw new Error(`RPC ${payload.error.code}: ${payload.error.message}`);
  if (payload.result === undefined) throw new Error(`RPC returned no result for ${method}`);
  return payload.result;
}

async function mapLimit(items, limit, worker) {
  const results = new Array(items.length);
  let cursor = 0;
  async function run() {
    while (cursor < items.length) {
      const index = cursor++;
      try {
        results[index] = { status: 'fulfilled', value: await worker(items[index], index) };
      } catch (error) {
        results[index] = { status: 'rejected', reason: error };
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, run));
  return results;
}

export async function resolveTokenAddress(input) {
  const clean = String(input || '').trim();
  if (!clean) throw new Error('Missing token address.');

  try {
    const url = new URL(clean);
    if (url.hostname.includes('dexscreener.com')) {
      const parts = url.pathname.split('/').filter(Boolean);
      const solanaIndex = parts.indexOf('solana');
      const pairId = solanaIndex >= 0 ? parts[solanaIndex + 1] : null;
      if (pairId) {
        const response = await fetch(`https://api.dexscreener.com/latest/dex/pairs/solana/${encodeURIComponent(pairId)}`);
        if (response.ok) {
          const data = await response.json();
          const pair = Array.isArray(data?.pairs) ? data.pairs[0] : null;
          if (pair?.baseToken?.address) return String(pair.baseToken.address);
        }
      }
    }
  } catch {
    // Normal token address.
  }

  return clean;
}

function normalizeSocialLink(raw, platform = '') {
  const value = String(raw || '').trim();
  if (value.startsWith('http://') || value.startsWith('https://')) return value;
  const handle = value.replace(/^@/, '');
  const p = String(platform || '').toLowerCase();
  if (!handle) return '';
  if (p.includes('twitter') || p === 'x') return `https://x.com/${handle}`;
  if (p.includes('telegram')) return `https://t.me/${handle}`;
  if (p.includes('discord')) return value;
  return '';
}

async function fetchMarket(tokenAddress) {
  const response = await fetch(`https://api.dexscreener.com/token-pairs/v1/solana/${encodeURIComponent(tokenAddress)}`);
  if (!response.ok) throw new Error(`DEX Screener HTTP ${response.status}`);
  const payload = await response.json();
  const pairs = Array.isArray(payload) ? payload : Array.isArray(payload?.pairs) ? payload.pairs : [];
  const matching = pairs
    .filter((pair) => pair?.chainId === 'solana' && pair?.baseToken?.address === tokenAddress)
    .sort((a, b) => n(b?.liquidity?.usd) - n(a?.liquidity?.usd));

  if (!matching.length) {
    throw new Error('No active Solana pair was found where this mint is the base token.');
  }

  const pair = matching[0];
  const socialLinks = (pair.info?.socials || [])
    .map((item) => normalizeSocialLink(item?.url || item?.handle, item?.platform))
    .filter(Boolean);
  const websites = (pair.info?.websites || []).map((item) => String(item?.url || '')).filter(Boolean);

  return {
    name: pair.baseToken?.name || 'Unknown token',
    symbol: pair.baseToken?.symbol || tokenAddress.slice(0, 5).toUpperCase(),
    tokenAddress,
    pairAddress: pair.pairAddress || '',
    dexId: pair.dexId || '',
    priceUsd: n(pair.priceUsd),
    marketCapUsd: n(pair.marketCap || pair.fdv),
    liquidityUsd: n(pair.liquidity?.usd),
    volume5mUsd: n(pair.volume?.m5),
    volume1hUsd: n(pair.volume?.h1),
    volume24hUsd: n(pair.volume?.h24),
    buys5m: n(pair.txns?.m5?.buys),
    sells5m: n(pair.txns?.m5?.sells),
    buys1h: n(pair.txns?.h1?.buys),
    sells1h: n(pair.txns?.h1?.sells),
    priceChange5m: n(pair.priceChange?.m5),
    priceChange1h: n(pair.priceChange?.h1),
    priceChange24h: n(pair.priceChange?.h24),
    pairCreatedAt: n(pair.pairCreatedAt),
    boostsActive: n(pair.boosts?.active),
    socialLinks,
    websites,
  };
}

function extractIncomingSolTransfers(transaction, wallet) {
  const hits = [];
  const inspect = (instruction) => {
    const parsed = instruction?.parsed;
    const info = parsed?.info;
    if (!parsed || !info) return;
    if (String(instruction?.program || '').toLowerCase() !== 'system') return;
    if (!String(parsed.type || '').toLowerCase().includes('transfer')) return;
    const destination = String(info.destination || info.to || '');
    const source = String(info.source || info.from || '');
    const lamports = n(info.lamports);
    if (destination === wallet && source && source !== wallet && lamports > 0) {
      hits.push({ source, lamports });
    }
  };

  const outer = transaction?.transaction?.message?.instructions;
  if (Array.isArray(outer)) outer.forEach(inspect);
  const inner = transaction?.meta?.innerInstructions;
  if (Array.isArray(inner)) {
    for (const group of inner) {
      if (Array.isArray(group?.instructions)) group.instructions.forEach(inspect);
    }
  }
  return hits;
}

async function inspectWallet(wallet, holderPct, rpcUrl) {
  const signatures = await rpcCall(rpcUrl, 'getSignaturesForAddress', [
    wallet,
    { limit: 41, commitment: 'confirmed' },
  ]);

  const valid = Array.isArray(signatures)
    ? signatures.filter((entry) => entry?.signature && !entry.err)
    : [];
  const times = valid.map((entry) => n(entry.blockTime)).filter((value) => value > 0);
  const oldestVisibleAt = times.length ? Math.min(...times) * 1000 : null;
  const likelyFresh = valid.length <= 40 && Boolean(oldestVisibleAt && oldestVisibleAt >= Date.now() - 24 * 60 * 60 * 1000);

  const oldestCandidates = [...valid]
    .sort((a, b) => n(a.blockTime) - n(b.blockTime))
    .slice(0, 4);

  let funder = '';
  let fundedAt = 0;
  let fundingSol = 0;

  for (const sig of oldestCandidates) {
    const tx = await rpcCall(rpcUrl, 'getTransaction', [
      sig.signature,
      { encoding: 'jsonParsed', maxSupportedTransactionVersion: 0, commitment: 'confirmed' },
    ]);
    if (!tx) continue;
    const transfers = extractIncomingSolTransfers(tx, wallet).sort((a, b) => b.lamports - a.lamports);
    if (transfers.length) {
      funder = transfers[0].source;
      fundingSol = transfers[0].lamports / 1_000_000_000;
      fundedAt = n(tx.blockTime) * 1000;
      break;
    }
  }

  return {
    wallet,
    holderPct,
    signatureCount: valid.length,
    oldestVisibleAt: oldestVisibleAt || 0,
    likelyFresh,
    funder,
    fundedAt,
    fundingSol,
  };
}

async function fetchHoldersAndForensics(tokenAddress, rpcUrl) {
  const [supplyResult, largestResult] = await Promise.all([
    rpcCall(rpcUrl, 'getTokenSupply', [tokenAddress, { commitment: 'confirmed' }]),
    rpcCall(rpcUrl, 'getTokenLargestAccounts', [tokenAddress, { commitment: 'confirmed' }]),
  ]);

  const supply = n(supplyResult?.value?.uiAmountString ?? supplyResult?.value?.uiAmount);
  const largest = Array.isArray(largestResult?.value) ? largestResult.value.slice(0, 20) : [];
  if (!supply || !largest.length) throw new Error('Holder distribution unavailable.');

  const tokenAccounts = largest.map((entry) => String(entry.address));
  let parsedAccounts = [];
  try {
    const parsed = await rpcCall(rpcUrl, 'getMultipleAccounts', [
      tokenAccounts,
      { encoding: 'jsonParsed', commitment: 'confirmed' },
    ]);
    parsedAccounts = Array.isArray(parsed?.value) ? parsed.value : [];
  } catch {
    parsedAccounts = [];
  }

  const rows = largest.map((entry, index) => {
    const amount = n(entry.uiAmountString ?? entry.uiAmount);
    const owner = parsedAccounts[index]?.data?.parsed?.info?.owner || '';
    return {
      tokenAccount: String(entry.address),
      owner: String(owner || ''),
      percentage: supply > 0 ? (amount / supply) * 100 : 0,
    };
  });

  const percentages = rows.map((entry) => entry.percentage);
  const sum = (count) => percentages.slice(0, count).reduce((a, b) => a + b, 0);
  const uniqueOwners = [];
  const seen = new Set();
  for (const row of rows) {
    if (row.owner && !seen.has(row.owner)) {
      seen.add(row.owner);
      uniqueOwners.push({ wallet: row.owner, holderPct: row.percentage });
    }
    if (uniqueOwners.length >= 6) break;
  }

  const inspected = await mapLimit(uniqueOwners, 2, (item) => inspectWallet(item.wallet, item.holderPct, rpcUrl));
  const walletEvidence = inspected.filter((item) => item.status === 'fulfilled').map((item) => item.value);
  const freshKnown = walletEvidence.filter((item) => typeof item.likelyFresh === 'boolean');
  const freshPct = freshKnown.length
    ? (freshKnown.filter((item) => item.likelyFresh).length / freshKnown.length) * 100
    : null;

  const funderMap = new Map();
  for (const item of walletEvidence) {
    if (!item.funder) continue;
    const list = funderMap.get(item.funder) || [];
    list.push(item);
    funderMap.set(item.funder, list);
  }
  const sharedFunderClusters = [...funderMap.entries()]
    .map(([funder, wallets]) => ({
      funder,
      walletCount: wallets.length,
      holderPct: wallets.reduce((sumPct, wallet) => sumPct + wallet.holderPct, 0),
      wallets: wallets.map((wallet) => wallet.wallet),
    }))
    .filter((cluster) => cluster.walletCount >= 2)
    .sort((a, b) => b.holderPct - a.holderPct);

  const funded = walletEvidence.filter((item) => item.fundedAt > 0).sort((a, b) => a.fundedAt - b.fundedAt);
  let synchronizedFunding = { walletCount: 0, holderPct: 0, spanMinutes: 0, wallets: [] };
  for (let i = 0; i < funded.length; i += 1) {
    const cluster = [funded[i]];
    for (let j = i + 1; j < funded.length; j += 1) {
      if (funded[j].fundedAt - funded[i].fundedAt <= 8 * 60 * 1000) cluster.push(funded[j]);
      else break;
    }
    const holderPct = cluster.reduce((sumPct, wallet) => sumPct + wallet.holderPct, 0);
    if (cluster.length > synchronizedFunding.walletCount || holderPct > synchronizedFunding.holderPct) {
      synchronizedFunding = {
        walletCount: cluster.length,
        holderPct,
        spanMinutes: cluster.length > 1 ? (cluster[cluster.length - 1].fundedAt - cluster[0].fundedAt) / 60_000 : 0,
        wallets: cluster.map((wallet) => wallet.wallet),
      };
    }
  }

  return {
    top1Pct: sum(1),
    top5Pct: sum(5),
    top10Pct: sum(10),
    rows,
    sampledWallets: walletEvidence,
    sampledFreshWalletPct: freshPct,
    sharedFunderClusters,
    synchronizedFunding,
    forensicsComplete: walletEvidence.length >= Math.min(uniqueOwners.length, 4),
  };
}

function classifyNarrative(market, pageContext = {}) {
  const text = `${market.name} ${market.symbol} ${pageContext.title || ''} ${pageContext.text || ''}`.toLowerCase();
  const buckets = [
    ['celebrity/news', ['elon', 'trump', 'president', 'celeb', 'celebrity', 'breaking', 'news', 'viral tweet']],
    ['tech/ai', ['ai', 'agent', 'robot', 'tech', 'protocol', 'app', 'software', 'gpu']],
    ['animal/meme', ['dog', 'cat', 'frog', 'pepe', 'doge', 'monkey', 'goat', 'meme']],
    ['culture/community', ['cult', 'community', 'culture', 'movement', 'army', 'club']],
    ['art/nft', ['art', 'nft', 'artist', 'pfp', 'collectible']],
  ];
  let best = { category: 'unknown', hits: [] };
  for (const [category, words] of buckets) {
    const hits = words.filter((word) => text.includes(word));
    if (hits.length > best.hits.length) best = { category, hits };
  }
  return {
    category: best.category,
    confidence: clamp(best.hits.length * 22, 15, 82),
    matched: best.hits.slice(0, 5),
  };
}

function buildSocialEvidence(market, pageContext = {}) {
  const raw = [
    ...(market.socialLinks || []),
    ...(market.websites || []),
    ...(Array.isArray(pageContext.socialLinks) ? pageContext.socialLinks : []),
  ];
  const deduped = [...new Set(raw.filter(Boolean).map((url) => String(url).trim()))];
  const classify = (url) => {
    const lower = url.toLowerCase();
    if (lower.includes('x.com/') || lower.includes('twitter.com/')) return 'X';
    if (lower.includes('t.me/') || lower.includes('telegram')) return 'Telegram';
    if (lower.includes('discord')) return 'Discord';
    if (lower.includes('youtube')) return 'YouTube';
    if (lower.includes('instagram')) return 'Instagram';
    if (lower.includes('tiktok')) return 'TikTok';
    return 'Website';
  };
  const links = deduped.slice(0, 12).map((url) => ({ url, type: classify(url) }));
  const types = [...new Set(links.map((item) => item.type))];
  const pageMentions = String(pageContext.text || '').slice(0, 20_000).toLowerCase();
  const socialKeywords = ['twitter', 'telegram', 'discord', 'community', 'holders', 'followers'];
  const visibleSocialMentions = socialKeywords.filter((word) => pageMentions.includes(word));
  return {
    links,
    types,
    count: links.length,
    hasX: types.includes('X'),
    hasTelegram: types.includes('Telegram'),
    hasWebsite: types.includes('Website'),
    visibleSocialMentions,
  };
}

function buildOutlook(market, risk, confidence, social) {
  const tx5 = market.buys5m + market.sells5m;
  const tx1h = market.buys1h + market.sells1h;
  const buy5 = tx5 > 0 ? market.buys5m / tx5 : 0.5;
  const buy1h = tx1h > 0 ? market.buys1h / tx1h : 0.5;
  const liqRatio = market.marketCapUsd > 0 ? (market.liquidityUsd / market.marketCapUsd) * 100 : 0;

  let score = 50;
  score += clamp(market.priceChange5m, -20, 20) * 0.8;
  score += clamp(market.priceChange1h, -35, 35) * 0.35;
  score += (buy5 - 0.5) * 42;
  score += (buy1h - 0.5) * 22;
  if (liqRatio >= 10) score += 5;
  else if (liqRatio < 3) score -= 8;
  if (market.volume5mUsd > 0 && market.liquidityUsd > 0 && market.volume5mUsd / market.liquidityUsd > 0.35) score += 5;
  if (social.count >= 2) score += 3;
  score -= Math.max(0, risk - 35) * 0.42;
  if (risk >= 65) score = Math.min(score, 42);
  score = clamp(Math.round(score));

  let label = 'MIXED';
  if (score >= 72) label = 'STRONG MOMENTUM';
  else if (score >= 60) label = 'POSITIVE MOMENTUM';
  else if (score < 30) label = 'DOWNSIDE RISK';
  else if (score < 43) label = 'WEAK MOMENTUM';

  const bull = [];
  const bear = [];
  if (buy5 >= 0.62) bull.push(`${Math.round(buy5 * 100)}% 5m buy share`);
  if (market.priceChange5m > 3) bull.push(`5m price +${market.priceChange5m.toFixed(1)}%`);
  if (liqRatio >= 10) bull.push(`${liqRatio.toFixed(1)}% liquidity/cap`);
  if (social.count >= 2) bull.push(`${social.count} visible social/project links`);
  if (buy5 <= 0.38) bear.push(`${Math.round((1 - buy5) * 100)}% 5m sell share`);
  if (market.priceChange5m < -3) bear.push(`5m price ${market.priceChange5m.toFixed(1)}%`);
  if (risk >= 50) bear.push(`risk score ${risk}/100`);
  if (liqRatio > 0 && liqRatio < 5) bear.push(`thin ${liqRatio.toFixed(1)}% liquidity/cap`);

  return {
    label,
    score,
    horizon: '15–60m evidence window',
    confidence: clamp(Math.round(confidence * 0.9), 20, 85),
    bull: bull.slice(0, 3),
    bear: bear.slice(0, 3),
    note: 'Momentum outlook from current market/on-chain evidence. It is not a guaranteed price prediction.',
  };
}

function scoreResult(market, holders, holderError, pageContext) {
  let risk = 8;
  let confidence = 34;
  const signals = [];
  const add = (label, detail, points, severity = 'info') => {
    risk += points;
    signals.push({ label, detail, points, severity });
  };

  const cap = market.marketCapUsd;
  const liquidityRatio = cap > 0 ? (market.liquidityUsd / cap) * 100 : 0;
  confidence += 15;

  if (market.liquidityUsd < 5_000) add('Very thin liquidity', `${money(market.liquidityUsd)} visible liquidity`, 24, 'critical');
  else if (market.liquidityUsd < 15_000) add('Low liquidity', `${money(market.liquidityUsd)} visible liquidity`, 13, 'warning');
  else if (market.liquidityUsd >= 50_000) add('Meaningful liquidity', `${money(market.liquidityUsd)} visible liquidity`, -4, 'positive');

  if (cap > 0) {
    if (liquidityRatio < 2) add('Liquidity tiny vs cap', `${pct(liquidityRatio)} liquidity / cap`, 24, 'critical');
    else if (liquidityRatio < 5) add('Weak liquidity ratio', `${pct(liquidityRatio)} liquidity / cap`, 14, 'warning');
    else if (liquidityRatio < 10) add('Liquidity ratio needs caution', `${pct(liquidityRatio)} liquidity / cap`, 7, 'warning');
    else add('Supportive liquidity ratio', `${pct(liquidityRatio)} liquidity / cap`, -5, 'positive');
  }

  const tx1h = market.buys1h + market.sells1h;
  if (tx1h > 0) {
    const buyShare = market.buys1h / tx1h;
    if (buyShare > 0.92 || buyShare < 0.08) add('Extreme 1h transaction imbalance', `${Math.round(buyShare * 100)}% buys`, 8, 'warning');
  }

  if (market.liquidityUsd > 0 && market.volume1hUsd / market.liquidityUsd > 15) {
    add('Extreme volume / liquidity', `${(market.volume1hUsd / market.liquidityUsd).toFixed(1)}x turnover in 1h`, 10, 'warning');
  }

  if (market.pairCreatedAt && Date.now() - market.pairCreatedAt < 5 * 60_000) {
    add('Extremely new pair', 'Less than 5 minutes of market history.', 8, 'warning');
  }

  if (holders) {
    confidence += 26;
    if (holders.top1Pct >= 20) add('Single-account concentration', `Top raw token account ${pct(holders.top1Pct)}`, 22, 'critical');
    else if (holders.top1Pct >= 10) add('Large top holder', `Top raw token account ${pct(holders.top1Pct)}`, 12, 'warning');

    if (holders.top5Pct >= 55) add('Top 5 control most supply', `${pct(holders.top5Pct)} combined`, 20, 'critical');
    else if (holders.top5Pct >= 35) add('Concentrated top 5', `${pct(holders.top5Pct)} combined`, 10, 'warning');
    else add('Top accounts relatively distributed', `${pct(holders.top5Pct)} combined`, -4, 'positive');

    if (holders.sampledFreshWalletPct !== null) {
      confidence += 5;
      if (holders.sampledFreshWalletPct >= 60) add('Many sampled holders look fresh', `${pct(holders.sampledFreshWalletPct)} of sampled owners`, 18, 'critical');
      else if (holders.sampledFreshWalletPct >= 35) add('Fresh-wallet concentration', `${pct(holders.sampledFreshWalletPct)} of sampled owners`, 9, 'warning');
    }

    const strongestFunder = holders.sharedFunderClusters?.[0];
    if (strongestFunder) {
      confidence += holders.forensicsComplete ? 9 : 4;
      const critical = strongestFunder.walletCount >= 3 && strongestFunder.holderPct >= 10;
      add(
        critical ? 'Strong shared-funder cluster' : 'Shared funding source detected',
        `${strongestFunder.walletCount} sampled holder wallets share one SOL funder and represent ~${pct(strongestFunder.holderPct)} of supply. Shared exchanges/bridges can create false positives.`,
        critical ? 24 : 12,
        critical ? 'critical' : 'warning',
      );
    }

    const sync = holders.synchronizedFunding;
    if (sync?.walletCount >= 3) {
      const critical = sync.holderPct >= 10 && sync.spanMinutes <= 5;
      add(
        'Synchronized wallet funding',
        `${sync.walletCount} sampled holders were first-funded within ~${sync.spanMinutes.toFixed(1)} minutes and represent ~${pct(sync.holderPct)} of supply.`,
        critical ? 20 : 11,
        critical ? 'critical' : 'warning',
      );
    }
  } else {
    confidence = Math.min(confidence, 55);
    add('Holder evidence unavailable', holderError || 'RPC holder scan failed.', 0, 'warning');
  }

  const social = buildSocialEvidence(market, pageContext);
  if (!social.hasX && !social.hasTelegram && !social.hasWebsite) {
    add('No visible project/social links', 'No X, Telegram or website was found in DEX metadata or the current terminal page.', 5, 'warning');
  } else {
    confidence += Math.min(8, social.count * 2);
    add('Social/project footprint found', `${social.types.join(', ') || 'links'} · ${social.count} visible link(s). Presence alone does not prove quality.`, 0, 'info');
  }

  const narrative = classifyNarrative(market, pageContext);
  add('Narrative classification', `${narrative.category}${narrative.matched.length ? ` · matched: ${narrative.matched.join(', ')}` : ''}`, 0, 'info');

  risk = clamp(Math.round(risk));
  confidence = clamp(Math.round(confidence));

  let posture;
  if (risk >= 65) posture = 'SKIP';
  else if (risk >= 45) posture = 'WAIT';
  else if (risk >= 25 || confidence < 60) posture = 'WATCH';
  else posture = 'SETUP';

  const outlook = buildOutlook(market, risk, confidence, social);
  return {
    risk,
    posture,
    confidence,
    liquidityRatio,
    signals: signals.sort((a, b) => b.points - a.points),
    social,
    narrative,
    outlook,
  };
}

export async function analyze(input, pageContext = {}) {
  const stored = await chrome.storage.local.get(['rpcUrl']);
  const rpcUrl = stored.rpcUrl || DEFAULT_RPC;
  const tokenAddress = await resolveTokenAddress(input);
  const market = await fetchMarket(tokenAddress);

  let holders = null;
  let holderError = '';
  try {
    holders = await fetchHoldersAndForensics(tokenAddress, rpcUrl);
  } catch (error) {
    holderError = error instanceof Error ? error.message : String(error);
  }

  const scored = scoreResult(market, holders, holderError, pageContext || {});
  return {
    tokenAddress,
    market,
    holders,
    holderError,
    generatedAt: Date.now(),
    ...scored,
  };
}
