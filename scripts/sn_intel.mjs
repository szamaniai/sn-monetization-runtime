#!/usr/bin/env node
// SN intelligence collector — anonymous GraphQL only (no auth needed)
// Outputs:
//   data/intel/super_zappers.tsv   (top earners site-wide, weekly refresh)
//   data/intel/sub_velocity.tsv    (per-sub stacking velocity, daily refresh)
//   data/intel/zapper_subs.tsv     (which subs each super-zapper posts in)
// Usage:
//   OUT_DIR=data/intel node scripts/sn_intel.mjs --topusers
//   OUT_DIR=data/intel node scripts/sn_intel.mjs --velocity
//   OUT_DIR=data/intel node scripts/sn_intel.mjs --all

import fs from 'node:fs';
import path from 'node:path';

const OUT = path.resolve(process.env.OUT_DIR || 'data/intel');
fs.mkdirSync(OUT, { recursive: true });

async function gql(query, variables = {}) {
  const r = await fetch('https://stacker.news/api/graphql', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'apollographql-client-name': 'web', 'user-agent': 'Mozilla/5.0' },
    body: JSON.stringify({ query, variables }),
  });
  const text = await r.text();
  try { return JSON.parse(text); } catch { return { raw: text.slice(0,150) }; }
}

async function topUsers() {
  const r = await gql('query g{ topUsers(when:"week",limit:50){ users{ name nitems optional{stacked spent streak} } } }');
  const users = (r.data?.topUsers?.users || []).filter(u => u && u.optional?.stacked > 0);
  return users.sort((a,b) => (b.optional?.stacked||0) - (a.optional?.stacked||0));
}

async function userSubs(name) {
  const r = await gql('query g($n:String!){ userSubs(name:$n,when:"week",limit:20){ subs{ name } } }', { n: name });
  return (r.data?.userSubs?.subs || []).map(s => s.name);
}

const TIER_1 = ['bitcoin','bitcoin_beginners','bitcoin_Mining','bitcoinplusplus','bitdevs','lightning','mempool','nostr','security','privacy','devs','tech','AI','openagents','meta'];
const TIER_2 = ['math','science','econ','Construction_and_Engineering','Education','Stacker_Stocks','charts_and_maps','AMA','AskSN','jobs','ideasfromtheedge','dotnet','Design','tutorials','mostly_harmless','news','history','BooksAndArticles','podcasts','lol','Memes'];
const ALL = [...TIER_1, ...TIER_2];

async function subVelocity(sub) {
  // stackingGrowth returns array of {time, data:[{name,value}]} per day
  // Aggregate week total
  try {
    const r = await gql('query g($s:String){ stackingGrowth(when:"week",sub:$s){ time data{name value} } }', { s: sub });
    const series = r.data?.stackingGrowth || [];
    let total = 0, days = series.length;
    for (const point of series) {
      for (const d of (point.data || [])) total += +d.value || 0;
    }
    return { sub, weekly_stacked: total, days, avg_daily: days ? Math.round(total/days) : 0 };
  } catch (e) { return { sub, error: e.message }; }
}

async function runTopUsers() {
  const ts = new Date().toISOString();
  const users = await topUsers();
  const lines = ['# refreshed_at\tname\tnitems\tstacked\tspent\tstreak'];
  for (const u of users) {
    lines.push([ts, u.name, u.nitems, u.optional?.stacked||0, u.optional?.spent||0, u.optional?.streak||0].join('\t'));
  }
  fs.writeFileSync(path.join(OUT, 'super_zappers.tsv'), lines.join('\n')+'\n');
  console.log(`[intel] super_zappers: ${users.length} users; top: ${users[0]?.name} (${users[0]?.optional?.stacked} stacked)`);

  // Append-only history
  const hist = path.join(OUT, 'super_zappers_history.tsv');
  if (!fs.existsSync(hist)) fs.writeFileSync(hist, lines[0]+'\n');
  for (let i = 1; i < lines.length; i++) fs.appendFileSync(hist, lines[i]+'\n');

  // For top 20: query their active subs
  const zSubs = ['# refreshed_at\tname\tstacked\tactive_subs'];
  for (const u of users.slice(0, 20)) {
    const subs = await userSubs(u.name);
    zSubs.push([ts, u.name, u.optional?.stacked||0, subs.join(',')].join('\t'));
    await new Promise(r => setTimeout(r, 200));
  }
  fs.writeFileSync(path.join(OUT, 'zapper_subs.tsv'), zSubs.join('\n')+'\n');
  console.log(`[intel] zapper_subs: queried top 20 zappers' active subs`);
}

async function runVelocity() {
  const ts = new Date().toISOString();
  const lines = ['# refreshed_at\tsub\ttier\tweekly_stacked\tdays_in_window\tavg_daily_stacked'];
  for (const sub of ALL) {
    const v = await subVelocity(sub);
    if (v.error) continue;
    const tier = TIER_1.includes(sub) ? 1 : 2;
    lines.push([ts, sub, tier, v.weekly_stacked, v.days, v.avg_daily].join('\t'));
    await new Promise(r => setTimeout(r, 200));
  }
  fs.writeFileSync(path.join(OUT, 'sub_velocity.tsv'), lines.join('\n')+'\n');
  console.log(`[intel] sub_velocity: ${lines.length-1} subs surveyed`);
}

(async () => {
  const args = process.argv.slice(2);
  if (args.includes('--all') || args.includes('--topusers')) await runTopUsers();
  if (args.includes('--all') || args.includes('--velocity')) await runVelocity();
})().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
