// SN Subs tier classification — 2026-05-03
// Discovered 58 active subs; tiered by expected EV / posting cadence / topic alignment with Claude strengths.
// Tier 1 (T1): scan every 15 min — high-value technical/Bitcoin
// Tier 2 (T2): scan hourly — adjacent technical/professional
// Tier 3 (T3): scan daily — niche/lifestyle

export const TIER_1 = [
  'bitcoin',
  'bitcoin_beginners',
  'bitcoin_Mining',
  'bitcoinplusplus',
  'bitdevs',
  'lightning',
  'mempool',
  'nostr',
  'security',
  'privacy',
  'devs',
  'tech',
  'AI',
  'openagents',
  'meta',
];

export const TIER_2 = [
  'math',
  'science',
  'econ',
  'Construction_and_Engineering',
  'Education',
  'Stacker_Stocks',
  'charts_and_maps',
  'AMA',
  'AskSN',
  'jobs',
  'ideasfromtheedge',
  'dotnet',
  'Design',
  'tutorials',
  'mostly_harmless',
  'news',
  'history',
  'BooksAndArticles',
  'podcasts',
  'lol',
  'Memes',
];

export const TIER_3 = [
  'AGORA', 'aliens_and_UFOs', 'Animal_World', 'art', 'Christianity',
  'culture', 'DIY', 'events', 'food_and_drinks', 'gaming',
  'Geyser_community', 'HealthAndFitness', 'hyperlinks', 'movies',
  'Music', 'Photography', 'Politics_And_Law', 'relationships',
  'Stacker_Sports', 'the_stacker_muse', 'Travel', 'videos',
];

// Topic angle hints — which sub aligns with Claude's writing strengths
export const SUB_ANGLE = {
  bitcoin: 'protocol, mining economics, multisig, UX',
  bitcoin_beginners: 'pedagogy, tx mistakes, common-fallacy debunks',
  bitcoin_Mining: 'difficulty, ASIC economics, pool design',
  bitcoinplusplus: 'protocol research deep-dive',
  bitdevs: 'technical meeting agenda commentary',
  lightning: 'liquidity, channel management, LSP design',
  mempool: 'fee dynamics, RBF, block-template',
  nostr: 'relay design, NIP analysis, key delegation',
  security: 'threat models, SOP critiques',
  privacy: 'coinjoin, privacy techniques, deanon attacks',
  devs: 'tooling reviews, architecture patterns',
  tech: 'systems design, perf, security',
  AI: 'agent architecture, eval critique, alignment',
  openagents: 'agent infra, payment rails',
  meta: 'SN platform mechanics, curation, incentives',
  math: 'structural intuition, applied stats',
  science: 'methodology critique',
  econ: 'mechanism design, monetary policy',
  AskSN: 'substantive Q answer (high zap potential)',
  AMA: 'substantive Q to host (matches BM-SN-002 pattern)',
  jobs: 'role analysis, market commentary',
  ideasfromtheedge: 'speculative tech analysis',
  Design: 'UX critique',
  tutorials: 'completeness audit, common-pitfall',
  history: 'historical economic mechanism analysis',
  econ: 'monetary policy, mechanism design',
  Construction_and_Engineering: 'systems-engineering crossover',
  Education: 'pedagogy critique',
  charts_and_maps: 'data viz critique',
  BooksAndArticles: 'review with synthesis angle',
  news: 'context-add to news without rehashing',
  podcasts: 'episode summary with technical angle',
};

export const ALL_SUBS = [...TIER_1, ...TIER_2, ...TIER_3];

export const TIER_OF = (() => {
  const m = {};
  for (const x of TIER_1) m[x] = 1;
  for (const x of TIER_2) m[x] = 2;
  for (const x of TIER_3) m[x] = 3;
  return m;
})();
