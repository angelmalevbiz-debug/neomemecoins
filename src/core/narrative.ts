import type { MarketSnapshot, NarrativeCategory, NarrativeSnapshot } from './types';

const KEYWORDS: Record<NarrativeCategory, string[]> = {
  'pure-meme': ['meme', 'dog', 'cat', 'frog', 'pepe', 'wojak', 'troll', 'goat', 'monkey', 'ape', 'ponke', 'bonk'],
  culture: ['culture', 'viral', 'internet', 'community meme', 'movement', 'trend'],
  community: ['community', 'cult', 'army', 'dao', 'club', 'tribe'],
  news: ['news', 'breaking', 'election', 'launch', 'event', 'world cup', 'headline'],
  celebrity: ['elon', 'trump', 'celebrity', 'artist', 'rapper', 'actor', 'influencer', 'famous'],
  tech: ['ai', 'agent', 'robot', 'protocol', 'app', 'tech', 'open source', 'developer', 'compute'],
  art: ['art', 'nft', 'artist', 'design', 'creative', 'collection'],
  unknown: [],
};

export function classifyNarrative(market: MarketSnapshot, description = ''): NarrativeSnapshot {
  const corpus = [
    market.name,
    market.symbol,
    description,
    ...market.socials,
  ]
    .join(' ')
    .toLowerCase();

  const ranked = (Object.keys(KEYWORDS) as NarrativeCategory[])
    .filter((category) => category !== 'unknown')
    .map((category) => {
      const matched = KEYWORDS[category].filter((keyword) => corpus.includes(keyword));
      return { category, matched, score: matched.length };
    })
    .sort((a, b) => b.score - a.score);

  const winner = ranked[0];
  if (!winner || winner.score === 0) {
    return {
      category: 'unknown',
      confidence: 20,
      matchedKeywords: [],
      explanation: 'No strong narrative could be classified from the token metadata alone.',
    };
  }

  const confidence = Math.min(90, 45 + winner.score * 15);
  return {
    category: winner.category,
    confidence,
    matchedKeywords: winner.matched,
    explanation: `Detected ${winner.category.replace('-', ' ')} narrative from: ${winner.matched.join(', ')}.`,
  };
}
