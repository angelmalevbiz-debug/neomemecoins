const DEFAULT_RPC = 'https://api.mainnet.solana.com';
let rpcId = 1;

const n = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
const clamp = (value, min = 0, max = 100) => Math.min(max, Math.max(min, value));
const pct = (value, digits = n(value) >= 10 ? 1 : 2) => `${n(value).toFixed(digits)}%`;
const money = (value) => {
  const v = n(value);
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(v < 1 ? 4 : 0)}`;
};

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
      try { results[index] = { status: 'fulfilled', value: await worker(items[index], index) }; }
      catch (error) { results[index] = { status: 'rejected', reason: error }; }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length || 1) }, run));
  return results;
}

export async function resolveTokenAddress(input) {
  const clean = String(input || '').trim();
  if (!clean) throw new Error('Missing token address.');
  try {
    const url = new URL(clean);
    if (url.hostname.includes('dexscreener.com')) {
      const parts = url.pathname.split('/').filter(Boolean);
      const i = parts.indexOf('solana');
      const pairId = i >= 0 ? parts[i + 1] : null;
      if (pairId) {
        const response = await fetch(`https://api.dexscreener.com/latest/dex/pairs/solana/${encodeURIComponent(pairId)}`);
        if (response.ok) {
          const data = await response.json();
          const pair = Array.isArray(data?.pairs) ? data.pairs[0] : null;
          if (pair?.baseToken?.address) return String(pair.baseToken.address);
        }
      }
    }
  } catch { /* token address */ }
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
  return '';
}

async function fetchMarket(tokenAddress) {
  const response = await fetch(`https://api.dexscreener.com/token-pairs/v1/solana/${encodeURIComponent(tokenAddress)}`);
  if (!response.ok) throw new Error(`DEX Screener HTTP ${response.status}`);
  const payload = await response.json();
  const pairs = Array.isArray(payload) ? payload : Array.isArray(payload?.pairs) ? payload.pairs : [];
  const matching = pairs.filter((pair) => pair?.chainId === 'solana' && pair?.baseToken?.address === tokenAddress)
    .sort((a, b) => n(b?.liquidity?.usd) - n(a?.liquidity?.usd));
  if (!matching.length) throw new Error('No active Solana pair found for this mint.');
  const pair = matching[0];
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
    socialLinks: (pair.info?.socials || []).map((x) => normalizeSocialLink(x?.url || x?.handle, x?.platform)).filter(Boolean),
    websites: (pair.info?.websites || []).map((x) => String(x?.url || '')).filter(Boolean),
  };
}

async function fetchMintControls(tokenAddress, rpcUrl) {
  const result = await rpcCall(rpcUrl, 'getAccountInfo', [tokenAddress, { encoding: 'jsonParsed', commitment: 'confirmed' }]);
  const value = result?.value;
  const info = value?.data?.parsed?.info || {};
  const extensions = Array.isArray(info.extensions) ? info.extensions : [];
  const extensionText = JSON.stringify(extensions).toLowerCase();
  return {
    mintAuthority: String(info.mintAuthority || ''),
    freezeAuthority: String(info.freezeAuthority || ''),
    decimals: n(info.decimals),
    programOwner: String(value?.owner || ''),
    token2022: String(value?.owner || '').includes('TokenzQd'),
    extensions,
    hasTransferFee: extensionText.includes('transferfee'),
    hasPermanentDelegate: extensionText.includes('permanentdelegate'),
    hasTransferHook: extensionText.includes('transferhook'),
    hasDefaultFrozenState: extensionText.includes('defaultaccountstate') && extensionText.includes('frozen'),
  };
}

function extractIncomingSolTransfers(transaction, wallet) {
  const hits = [];
  const inspect = (instruction) => {
    const parsed = instruction?.parsed;
    const info = parsed?.info;
    if (!parsed || !info || String(instruction?.program || '').toLowerCase() !== 'system') return;
    if (!String(parsed.type || '').toLowerCase().includes('transfer')) return;
    const destination = String(info.destination || info.to || '');
    const source = String(info.source || info.from || '');
    const lamports = n(info.lamports);
    if (destination === wallet && source && source !== wallet && lamports > 0) hits.push({ source, lamports });
  };
  const outer = transaction?.transaction?.message?.instructions;
  if (Array.isArray(outer)) outer.forEach(inspect);
  const inner = transaction?.meta?.innerInstructions;
  if (Array.isArray(inner)) for (const group of inner) if (Array.isArray(group?.instructions)) group.instructions.forEach(inspect);
  return hits;
}

async function inspectWallet(wallet, holderPct, rpcUrl) {
  const LIMIT = 60;
  const signatures = await rpcCall(rpcUrl, 'getSignaturesForAddress', [wallet, { limit: LIMIT, commitment: 'confirmed' }]);
  const valid = Array.isArray(signatures) ? signatures.filter((x) => x?.signature && !x.err) : [];
  const historyTruncated = valid.length >= LIMIT;
  const times = valid.map((x) => n(x.blockTime)).filter((x) => x > 0);
  const oldestVisibleAt = times.length ? Math.min(...times) * 1000 : 0;
  const likelyFresh = !historyTruncated && Boolean(oldestVisibleAt && oldestVisibleAt >= Date.now() - 24 * 60 * 60 * 1000);
  const activityClass = likelyFresh ? 'fresh' : historyTruncated ? 'active/established' : 'established';

  let funder = '';
  let fundedAt = 0;
  let fundingSol = 0;
  let fundingReliable = false;

  // Only treat funding as reliable when the visible history appears complete.
  if (!historyTruncated) {
    const oldest = [...valid].sort((a, b) => n(a.blockTime) - n(b.blockTime)).slice(0, 5);
    for (const sig of oldest) {
      const tx = await rpcCall(rpcUrl, 'getTransaction', [sig.signature, { encoding: 'jsonParsed', maxSupportedTransactionVersion: 0, commitment: 'confirmed' }]);
      if (!tx) continue;
      const transfers = extractIncomingSolTransfers(tx, wallet).sort((a, b) => b.lamports - a.lamports);
      if (transfers.length) {
        funder = transfers[0].source;
        fundingSol = transfers[0].lamports / 1e9;
        fundedAt = n(tx.blockTime) * 1000;
        fundingReliable = true;
        break;
      }
    }
  }

  return { wallet, holderPct, signatureCount: valid.length, historyTruncated, oldestVisibleAt, likelyFresh, activityClass, funder, fundedAt, fundingSol, fundingReliable };
}

async function fetchHoldersAndForensics(tokenAddress, rpcUrl) {
  const [supplyResult, largestResult] = await Promise.all([
    rpcCall(rpcUrl, 'getTokenSupply', [tokenAddress, { commitment: 'confirmed' }]),
    rpcCall(rpcUrl, 'getTokenLargestAccounts', [tokenAddress, { commitment: 'confirmed' }]),
  ]);
  const supply = n(supplyResult?.value?.uiAmountString ?? supplyResult?.value?.uiAmount);
  const largest = Array.isArray(largestResult?.value) ? largestResult.value.slice(0, 20) : [];
  if (!supply || !largest.length) throw new Error('Holder distribution unavailable.');

  const tokenAccounts = largest.map((x) => String(x.address));
  let parsedAccounts = [];
  try {
    const parsed = await rpcCall(rpcUrl, 'getMultipleAccounts', [tokenAccounts, { encoding: 'jsonParsed', commitment: 'confirmed' }]);
    parsedAccounts = Array.isArray(parsed?.value) ? parsed.value : [];
  } catch { parsedAccounts = []; }

  const rows = largest.map((entry, index) => {
    const amount = n(entry.uiAmountString ?? entry.uiAmount);
    return {
      tokenAccount: String(entry.address),
      owner: String(parsedAccounts[index]?.data?.parsed?.info?.owner || ''),
      percentage: supply > 0 ? amount / supply * 100 : 0,
    };
  });
  const sum = (count) => rows.slice(0, count).reduce((a, b) => a + b.percentage, 0);

  const uniqueOwners = [];
  const seen = new Set();
  for (const row of rows) {
    if (row.owner && !seen.has(row.owner)) {
      seen.add(row.owner);
      uniqueOwners.push({ wallet: row.owner, holderPct: row.percentage });
    }
    if (uniqueOwners.length >= 8) break;
  }

  const inspected = await mapLimit(uniqueOwners, 2, (item) => inspectWallet(item.wallet, item.holderPct, rpcUrl));
  const walletEvidence = inspected.filter((x) => x.status === 'fulfilled').map((x) => x.value);
  const freshKnown = walletEvidence.filter((x) => !x.historyTruncated);
  const sampledFreshWalletPct = freshKnown.length ? freshKnown.filter((x) => x.likelyFresh).length / freshKnown.length * 100 : null;

  const reliableFunding = walletEvidence.filter((x) => x.fundingReliable && x.funder);
  const funderMap = new Map();
  for (const item of reliableFunding) {
    const list = funderMap.get(item.funder) || [];
    list.push(item); funderMap.set(item.funder, list);
  }
  const sharedFunderClusters = [...funderMap.entries()].map(([funder, wallets]) => ({
    funder,
    walletCount: wallets.length,
    holderPct: wallets.reduce((s, w) => s + w.holderPct, 0),
    wallets: wallets.map((w) => w.wallet),
  })).filter((x) => x.walletCount >= 2).sort((a, b) => b.holderPct - a.holderPct);

  const funded = reliableFunding.filter((x) => x.fundedAt > 0).sort((a, b) => a.fundedAt - b.fundedAt);
  let synchronizedFunding = { walletCount: 0, holderPct: 0, spanMinutes: 0, wallets: [] };
  for (let i = 0; i < funded.length; i += 1) {
    const cluster = [funded[i]];
    for (let j = i + 1; j < funded.length; j += 1) {
      if (funded[j].fundedAt - funded[i].fundedAt <= 8 * 60_000) cluster.push(funded[j]); else break;
    }
    const holderPct = cluster.reduce((s, w) => s + w.holderPct, 0);
    if (cluster.length > synchronizedFunding.walletCount || holderPct > synchronizedFunding.holderPct) {
      synchronizedFunding = { walletCount: cluster.length, holderPct, spanMinutes: cluster.length > 1 ? (cluster.at(-1).fundedAt - cluster[0].fundedAt) / 60_000 : 0, wallets: cluster.map((w) => w.wallet) };
    }
  }

  return {
    top1Pct: sum(1), top5Pct: sum(5), top10Pct: sum(10), rows,
    sampledWallets: walletEvidence, sampledFreshWalletPct, sharedFunderClusters, synchronizedFunding,
    forensicsComplete: walletEvidence.length >= Math.min(uniqueOwners.length, 5),
    reliableFundingSamples: reliableFunding.length,
  };
}

function classifyNarrative(market, pageContext = {}) {
  const text = ` ${market.name} ${market.symbol} ${pageContext.title || ''} ${pageContext.text || ''} `.toLowerCase();
  const buckets = [
    ['culture/pure-meme', ['meme','pepe','doge','wojak','troll','bonk','frog','cat','dog','monkey','goat']],
    ['culture/community', ['cult','community','movement','army','club','gang']],
    ['culture/art', ['art','nft','artist','pfp','collectible']],
    ['culture/social-viral', ['viral','tiktok','instagram','youtube','tweet','reel','video']],
    ['celebrity/news', ['elon','trump','president','celeb','celebrity','breaking','news','sam altman']],
    ['tech/ai', [' ai ','agent','robot','tech','protocol','software','app','gpu','openai','llm']],
  ];
  let best = { category: 'unknown', hits: [] };
  for (const [category, words] of buckets) {
    const hits = words.filter((word) => text.includes(word));
    if (hits.length > best.hits.length) best = { category, hits };
  }
  return { category: best.category, confidence: clamp(best.hits.length * 22, best.hits.length ? 30 : 10, 88), matched: best.hits.slice(0, 5) };
}

function buildSocialEvidence(market, pageContext = {}) {
  const raw = [...(market.socialLinks || []), ...(market.websites || []), ...(Array.isArray(pageContext.socialLinks) ? pageContext.socialLinks : [])];
  const deduped = [...new Set(raw.filter(Boolean).map((x) => String(x).trim()))];
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
  const links = deduped.slice(0, 12).map((url) => ({ url, platform: classify(url) }));
  const types = [...new Set(links.map((x) => x.platform))];
  return { links, types, count: links.length, hasX: types.includes('X'), hasTelegram: types.includes('Telegram'), hasWebsite: types.includes('Website') };
}

function marketFlowRisk(market) {
  let score = 0;
  const tx5 = market.buys5m + market.sells5m;
  const tx1 = market.buys1h + market.sells1h;
  const buy5 = tx5 ? market.buys5m / tx5 : 0.5;
  const buy1 = tx1 ? market.buys1h / tx1 : 0.5;
  const volLiq1 = market.liquidityUsd > 0 ? market.volume1hUsd / market.liquidityUsd : 0;
  if (tx5 > 8 && (buy5 > 0.95 || buy5 < 0.05)) score += 20;
  if (tx1 > 15 && (buy1 > 0.93 || buy1 < 0.07)) score += 16;
  if (volLiq1 > 15) score += 25;
  if (Math.abs(market.priceChange5m) > 80) score += 18;
  if (Math.abs(market.priceChange1h) > 250) score += 15;
  return clamp(score);
}

function buildOutlook(market, risk, confidence, social) {
  const tx5 = market.buys5m + market.sells5m;
  const tx1 = market.buys1h + market.sells1h;
  const buy5 = tx5 ? market.buys5m / tx5 : 0.5;
  const buy1 = tx1 ? market.buys1h / tx1 : 0.5;
  const liqRatio = market.marketCapUsd > 0 ? market.liquidityUsd / market.marketCapUsd * 100 : 0;
  let score = 50;
  score += clamp(market.priceChange5m, -20, 20) * 0.75;
  score += clamp(market.priceChange1h, -35, 35) * 0.32;
  score += (buy5 - 0.5) * 42;
  score += (buy1 - 0.5) * 20;
  if (liqRatio >= 10 && liqRatio <= 45) score += 5; else if (liqRatio < 3) score -= 8;
  if (social.count >= 2) score += 3;
  score -= Math.max(0, risk - 35) * 0.42;
  if (risk >= 65) score = Math.min(score, 42);
  if (confidence < 55) score = 50 + (score - 50) * 0.45;
  score = clamp(Math.round(score));

  let label = 'MIXED / UNCERTAIN';
  if (confidence >= 55) {
    if (score >= 72) label = 'STRONG MOMENTUM';
    else if (score >= 60) label = 'POSITIVE MOMENTUM';
    else if (score < 30) label = 'DOWNSIDE RISK';
    else if (score < 43) label = 'WEAK MOMENTUM';
    else label = 'MIXED';
  }

  const bull = [], bear = [];
  if (buy5 >= 0.62) bull.push(`${Math.round(buy5 * 100)}% 5m buy share`);
  if (market.priceChange5m > 3) bull.push(`5m price +${market.priceChange5m.toFixed(1)}%`);
  if (liqRatio >= 10 && liqRatio <= 45) bull.push(`${liqRatio.toFixed(1)}% liquidity/cap`);
  if (social.count >= 2) bull.push(`${social.count} public project/social links`);
  if (buy5 <= 0.38) bear.push(`${Math.round((1 - buy5) * 100)}% 5m sell share`);
  if (market.priceChange5m < -3) bear.push(`5m price ${market.priceChange5m.toFixed(1)}%`);
  if (risk >= 50) bear.push(`risk score ${risk}/100`);
  if (liqRatio > 0 && liqRatio < 5) bear.push(`thin ${liqRatio.toFixed(1)}% liquidity/cap`);
  return { label, score, horizon: '15–60m evidence window', confidence: clamp(Math.round(confidence * 0.9), 20, 88), bull: bull.slice(0, 3), bear: bear.slice(0, 3), note: 'Current-evidence momentum estimate; locally validated after 15m/60m when the token remains observable.' };
}

function scoreResult(market, holders, holderError, mintControls, mintError, pageContext) {
  let risk = 10;
  let confidence = 34;
  const signals = [];
  const add = (label, detail, points, severity = 'info') => { risk += points; signals.push({ label, detail, points, severity }); };
  const liqRatio = market.marketCapUsd > 0 ? market.liquidityUsd / market.marketCapUsd * 100 : 0;
  confidence += 15;

  if (market.liquidityUsd < 5_000) add('Very thin liquidity', `${money(market.liquidityUsd)} visible liquidity`, 24, 'critical');
  else if (market.liquidityUsd < 15_000) add('Low liquidity', `${money(market.liquidityUsd)} visible liquidity`, 13, 'warning');
  else if (market.liquidityUsd >= 50_000) add('Meaningful liquidity', `${money(market.liquidityUsd)} visible liquidity`, -3, 'positive');

  if (market.marketCapUsd > 0) {
    if (liqRatio < 2) add('Liquidity tiny vs cap', `${pct(liqRatio)} liquidity / cap`, 24, 'critical');
    else if (liqRatio < 5) add('Weak liquidity ratio', `${pct(liqRatio)} liquidity / cap`, 14, 'warning');
    else if (liqRatio < 10) add('Liquidity ratio needs caution', `${pct(liqRatio)} liquidity / cap`, 7, 'warning');
    else if (liqRatio <= 45) add('Supportive liquidity ratio', `${pct(liqRatio)} liquidity / cap`, -4, 'positive');
  }

  const flowRisk = marketFlowRisk(market);
  if (flowRisk >= 45) add('Suspicious market-flow profile', `Flow-risk proxy ${flowRisk}/100. This is not direct staircase-chart detection.`, 12, 'warning');
  else if (flowRisk >= 25) add('Market-flow caution', `Flow-risk proxy ${flowRisk}/100.`, 6, 'warning');

  if (mintControls) {
    confidence += 12;
    if (mintControls.freezeAuthority) add('Freeze authority still active', 'Token accounts may still be freezeable by an authority.', 18, 'critical');
    else add('No freeze authority', 'Mint reports no active freeze authority.', -2, 'positive');
    if (mintControls.mintAuthority) add('Mint authority still active', 'Additional supply may still be mintable by an authority.', 12, 'warning');
    else add('Mint authority revoked', 'Mint reports no active mint authority.', -3, 'positive');
    if (mintControls.hasPermanentDelegate) add('Token-2022 permanent delegate', 'Permanent-delegate extension detected.', 22, 'critical');
    if (mintControls.hasTransferHook) add('Token-2022 transfer hook', 'Transfer-hook extension detected; transfers may have custom behavior.', 12, 'warning');
    if (mintControls.hasTransferFee) add('Token-2022 transfer fee', 'Transfer-fee extension detected. Verify current fee parameters before trading.', 8, 'warning');
    if (mintControls.hasDefaultFrozenState) add('Default frozen account state', 'Token-2022 default account state appears frozen.', 24, 'critical');
  } else {
    add('Mint-control evidence unavailable', mintError || 'RPC mint inspection failed.', 0, 'warning');
  }

  let bundleRisk = 0;
  if (holders) {
    confidence += 24;
    // Raw largest accounts can include protocol/vault accounts, so concentration alone is not treated as proof of insider control.
    if (holders.top1Pct >= 25) { add('Large raw top account', `Top raw token account ${pct(holders.top1Pct)}. May include protocol/vault accounts.`, 12, 'warning'); bundleRisk += 10; }
    else if (holders.top1Pct >= 12) { add('Notable raw top account', `Top raw token account ${pct(holders.top1Pct)}.`, 6, 'warning'); bundleRisk += 5; }
    if (holders.top5Pct >= 65) { add('High raw top-5 concentration', `${pct(holders.top5Pct)} combined; inspect wallet links before interpreting.`, 12, 'warning'); bundleRisk += 12; }
    else if (holders.top5Pct >= 40) { add('Concentrated raw top 5', `${pct(holders.top5Pct)} combined.`, 7, 'warning'); bundleRisk += 7; }
    else add('Top accounts relatively distributed', `${pct(holders.top5Pct)} combined`, -3, 'positive');

    if (holders.sampledFreshWalletPct !== null) {
      confidence += 5;
      if (holders.sampledFreshWalletPct >= 60) { add('Many sampled holders look fresh', `${pct(holders.sampledFreshWalletPct)} of reliable wallet-history samples`, 18, 'critical'); bundleRisk += 28; }
      else if (holders.sampledFreshWalletPct >= 35) { add('Fresh-wallet concentration', `${pct(holders.sampledFreshWalletPct)} of reliable samples`, 9, 'warning'); bundleRisk += 14; }
    }

    const strongest = holders.sharedFunderClusters?.[0];
    if (strongest) {
      const critical = strongest.walletCount >= 3 && strongest.holderPct >= 10;
      add(critical ? 'Strong shared-funder cluster' : 'Shared reliable funding source', `${strongest.walletCount} sampled holder wallets share one early SOL funder and represent ~${pct(strongest.holderPct)}. Shared services can still create false positives.`, critical ? 24 : 12, critical ? 'critical' : 'warning');
      bundleRisk += critical ? 35 : 20;
    }

    const sync = holders.synchronizedFunding;
    if (sync?.walletCount >= 3) {
      const critical = sync.holderPct >= 10 && sync.spanMinutes <= 5;
      add('Synchronized reliable wallet funding', `${sync.walletCount} sampled holders were funded within ~${sync.spanMinutes.toFixed(1)} minutes and represent ~${pct(sync.holderPct)}.`, critical ? 20 : 11, critical ? 'critical' : 'warning');
      bundleRisk += critical ? 30 : 18;
    }
    bundleRisk = clamp(bundleRisk);
  } else {
    confidence = Math.min(confidence, 58);
    add('Holder evidence unavailable', holderError || 'RPC holder scan failed.', 0, 'warning');
  }

  const social = buildSocialEvidence(market, pageContext);
  if (!social.hasX && !social.hasTelegram && !social.hasWebsite) add('No visible project/social links', 'No X, Telegram or website found in DEX metadata/current page.', 5, 'warning');
  else { confidence += Math.min(8, social.count * 2); add('Social/project footprint found', `${social.types.join(', ')} · ${social.count} link(s). Presence is not quality verification.`, 0, 'info'); }

  const narrative = classifyNarrative(market, pageContext);
  add('Narrative classification', `${narrative.category}${narrative.matched.length ? ` · ${narrative.matched.join(', ')}` : ''}`, 0, 'info');

  risk = clamp(Math.round(risk));
  confidence = clamp(Math.round(confidence));
  let posture = 'WATCH';
  if (risk >= 65 || bundleRisk >= 70) posture = 'SKIP';
  else if (risk >= 45 || bundleRisk >= 50) posture = 'WAIT';
  else if (risk < 25 && confidence >= 70 && bundleRisk < 35 && mintControls && !mintControls.freezeAuthority) posture = 'SETUP';

  const outlook = buildOutlook(market, risk, confidence, social);
  return { risk, bundleRisk, flowRisk, posture, confidence, liquidityRatio: liqRatio, signals: signals.sort((a,b) => b.points - a.points), social, narrative, outlook };
}

export async function analyze(input, pageContext = {}) {
  const stored = await chrome.storage.local.get(['rpcUrl']);
  const rpcUrl = stored.rpcUrl || DEFAULT_RPC;
  const tokenAddress = await resolveTokenAddress(input);
  const market = await fetchMarket(tokenAddress);

  let holders = null, holderError = '';
  let mintControls = null, mintError = '';
  const [holderRes, mintRes] = await Promise.allSettled([
    fetchHoldersAndForensics(tokenAddress, rpcUrl),
    fetchMintControls(tokenAddress, rpcUrl),
  ]);
  if (holderRes.status === 'fulfilled') holders = holderRes.value;
  else holderError = holderRes.reason instanceof Error ? holderRes.reason.message : String(holderRes.reason);
  if (mintRes.status === 'fulfilled') mintControls = mintRes.value;
  else mintError = mintRes.reason instanceof Error ? mintRes.reason.message : String(mintRes.reason);

  const scored = scoreResult(market, holders, holderError, mintControls, mintError, pageContext || {});
  return { tokenAddress, market, holders, holderError, mintControls, mintError, generatedAt: Date.now(), ...scored };
}
