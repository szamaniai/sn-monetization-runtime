#!/usr/bin/env node
// Trending-topic predictor (cloud version).
// Pulls top stories from SN /top + HN /front, extracts noun-phrase keywords,
// flags "RISING" topics (in trending sources but not yet covered in SN sub-territories).
//
// Cron: 4x/day at Taipei 00/06/12/18 = UTC 16/22/04/10
// Output: data/trending/topics.tsv (committed by workflow)
// Schema: topic<TAB>sources<TAB>score<TAB>seen_in_sn<TAB>rising_flag

import fs from 'node:fs';
import path from 'node:path';

const OUT_DIR = process.env.OUT_DIR || 'data/trending';
const OUT_FILE = path.join(OUT_DIR, 'topics.tsv');
fs.mkdirSync(OUT_DIR, { recursive: true });

const STOPWORDS = new Set('a an the and or but of in on for with to from by at as is are was were be been being have has had do does did this that these those it its'.split(' '));

function extractKeywords(text) {
  if (!text) return [];
  const candidates = (text.match(/\b([A-Z][a-z]+(?:[-\s][A-Z][a-z]+)*|[A-Z]{2,}|[a-z]+-[a-z]+)\b/g) || [])
    .map(s => s.toLowerCase())
    .filter(s => s.length >= 4 && !STOPWORDS.has(s));
  return [...new Set(candidates)];
}

async function fetchHN() {
  const ids = await fetch('https://hacker-news.firebaseio.com/v0/topstories.json').then(r => r.json());
  const top = ids.slice(0, 30);
  const stories = await Promise.all(top.map(id =>
    fetch(`https://hacker-news.firebaseio.com/v0/item/${id}.json`).then(r => r.json()).catch(() => null)
  ));
  return stories.filter(Boolean).map(s => ({ source: 'hn', text: s.title || '', score: s.score || 0 }));
}

async function fetchSNTop() {
  const q = `query top { items(sort:"top", when:"day", limit:30){ items{ id title sub{ name } sats ncomments } } }`;
  try {
    const r = await fetch('https://stacker.news/api/graphql', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: q }),
    }).then(r => r.json());
    return (r.data?.items?.items || []).map(i => ({
      source: `sn:${i.sub?.name || 'main'}`,
      text: i.title || '',
      score: i.sats || 0,
    }));
  } catch (e) { console.error(`[trending] sn top fail: ${e.message}`); return []; }
}

async function fetchSNRecent() {
  const subs = ['bitcoin', 'tech', 'meta', 'nostr', 'AI'];
  const out = [];
  for (const sub of subs) {
    const q = `query r($s:String!){ items(sort:"recent", sub:$s, limit:20){ items{ id title sats } } }`;
    try {
      const r = await fetch('https://stacker.news/api/graphql', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ query: q, variables: { s: sub } }),
      }).then(r => r.json());
      for (const it of r.data?.items?.items || []) out.push({ sub, title: it.title || '' });
    } catch {}
  }
  return out;
}

const [hn, snTop, snRecent] = await Promise.all([fetchHN(), fetchSNTop(), fetchSNRecent()]);
console.error(`[trending] hn=${hn.length} sn_top=${snTop.length} sn_recent=${snRecent.length}`);

const seenInSN = new Set();
for (const r of snRecent) for (const k of extractKeywords(r.title)) seenInSN.add(k);

const topicScore = new Map();
function bump(topic, source, weight) {
  const cur = topicScore.get(topic) || { score: 0, sources: new Set(), in_sn: seenInSN.has(topic) };
  cur.score += weight; cur.sources.add(source);
  topicScore.set(topic, cur);
}
for (const s of [...hn, ...snTop]) {
  for (const kw of extractKeywords(s.text)) bump(kw, s.source, Math.log10(Math.max(s.score, 1) + 1));
}

const ranked = [...topicScore.entries()]
  .filter(([t, s]) => s.score >= 0.3)
  .map(([topic, s]) => ({
    topic,
    score: +s.score.toFixed(2),
    sources: [...s.sources].join(','),
    in_sn: s.in_sn ? 'yes' : 'no',
    rising: !s.in_sn && s.sources.size >= 1 ? 'RISING' : '',
  }))
  .sort((a, b) => b.score - a.score)
  .slice(0, 40);

const out = ['# topic\tsources\tscore\tseen_in_sn\trising_flag',
  `# generated ${new Date().toISOString()}; n=${ranked.length}`];
for (const r of ranked) {
  out.push(`${r.topic}\t${r.sources}\t${r.score}\t${r.in_sn}\t${r.rising}`);
}
fs.writeFileSync(OUT_FILE, out.join('\n') + '\n');

const rising = ranked.filter(r => r.rising === 'RISING').slice(0, 5);
console.log(`[trending] wrote ${ranked.length} topics; ${rising.length} marked RISING`);
for (const r of rising) console.log(`  ⤴ ${r.topic} score=${r.score} (${r.sources})`);
