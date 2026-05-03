// Batch 4 — Stacker News opportunity radar v2 (2026-05-03)
// 改進 vs v1:
//   - 涵蓋 58 個 active subs（vs v1 的 5 個 = 覆蓋率 8.6% → 100%）
//   - 三層 tier (T1=15min / T2=hourly / T3=daily) 控制成本
//   - 多排序軸（recent + top/day）捕捉非 recent 高 EV 機會
//   - 累積式 target log（永不覆寫，全歷史）→ 未來 self_post 主題挖掘
//   - SIGNAL tag 計算（沿用 v1）
// 用法:
//   node runtime/scripts/sn_radar_v2.mjs --tier 1            # 只 T1，給 15min cron
//   node runtime/scripts/sn_radar_v2.mjs --tier 1,2          # T1+T2，給 hourly cron
//   node runtime/scripts/sn_radar_v2.mjs --tier 1,2,3        # 全掃，給 daily cron
//   node runtime/scripts/sn_radar_v2.mjs --tier 1 --json     # 印 JSON

import fs from 'node:fs';
import path from 'node:path';
import { TIER_1, TIER_2, TIER_3, TIER_OF, SUB_ANGLE } from './sn_subs_config.mjs';

const BASE = 'https://stacker.news';
const SORTS = ['recent', 'top'];   // recent = 新鮮機會; top = 累積熱度
const WHEN_OF_SORT = { recent: 'day', top: 'day' };
const LIMIT = 30;
const MIN_BOUNTY_SATS = 100;
const MAX_COMMENTS_FOR_LOW_COMP = 5;
const LOOKBACK_HOURS = 36;          // T1 用 36h 而非 24h，看 SIGNAL 邊界

const ARGV = process.argv.slice(2);
const TIER_FLAG = ARGV.find(a => a.startsWith('--tier='))?.slice(7)
                 || ARGV[ARGV.indexOf('--tier') + 1]
                 || '1';
const TIERS = TIER_FLAG.split(',').map(t => t.trim());
const isJson = ARGV.includes('--json');

const SUBS = [];
if (TIERS.includes('1')) SUBS.push(...TIER_1);
if (TIERS.includes('2')) SUBS.push(...TIER_2);
if (TIERS.includes('3')) SUBS.push(...TIER_3);

const QUERY = `
query items($sub: String, $sort: String, $when: String, $limit: Limit) {
  items(sub: $sub, sort: $sort, when: $when, limit: $limit) {
    items {
      id title url createdAt sats bounty bountyPaidTo ncomments
      user { name since nitems }
      sub { name }
    }
  }
}`;

async function fetchSub(sub, sort) {
  try {
    const r = await fetch(`${BASE}/api/graphql`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'apollographql-client-name': 'web' },
      body: JSON.stringify({ query: QUERY, variables: { sub, sort, when: WHEN_OF_SORT[sort], limit: LIMIT }, operationName: 'items' }),
    });
    if (!r.ok) return { sub, sort, error: `HTTP ${r.status}`, items: [] };
    const j = await r.json();
    if (j.errors) return { sub, sort, error: JSON.stringify(j.errors).slice(0, 200), items: [] };
    return { sub, sort, items: j.data?.items?.items || [] };
  } catch (e) {
    return { sub, sort, error: e.message, items: [] };
  }
}

function classify(item) {
  const ageHours = (Date.now() - new Date(item.createdAt).getTime()) / 3600000;
  const tags = [];
  const bounty = Number(item.bounty || 0);
  const ncom = Number(item.ncomments || 0);
  const score = Number(item.sats || 0);
  if (bounty >= MIN_BOUNTY_SATS && !item.bountyPaidTo) tags.push('OPEN_BOUNTY');
  if (bounty >= MIN_BOUNTY_SATS && !item.bountyPaidTo && ncom <= MAX_COMMENTS_FOR_LOW_COMP) tags.push('LOW_COMP');
  if (item.sub?.name === 'jobs') tags.push('JOB');
  if (ageHours <= 2) tags.push('FRESH');
  if (score >= 1000) tags.push('HOT');
  // SIGNAL: score>=100 AND ncom<=0.3*score AND age<=12h (CLAUDE.md hard rule #3)
  if (score >= 100 && ncom <= 0.3 * score && ageHours <= 12) tags.push('SIGNAL');
  // SELF_POST_OPPORTUNITY: high score parents in low-saturation subs
  // Heuristic: T2/T3 sub with score >= 200 AND ncom >= 5 → topic has audience but few writers
  const t = TIER_OF[item.sub?.name] || 3;
  if (t >= 2 && score >= 200 && ncom >= 5 && ncom <= 20) tags.push('SELF_POST_OPP');
  return { tags, ageHours, score, ncom, tier: t };
}

(async () => {
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const outDir = path.resolve(process.env.OUT_DIR || 'data/sn_opportunities');
  fs.mkdirSync(outDir, { recursive: true });
  const accumDir = path.resolve(process.env.ACCUM_DIR || 'data/sn_targets_accumulated');
  fs.mkdirSync(accumDir, { recursive: true });

  // Parallel fetch (subs × sorts)
  const tasks = [];
  for (const sub of SUBS) for (const sort of SORTS) tasks.push(fetchSub(sub, sort));
  const results = await Promise.all(tasks);

  // Aggregate; dedup by item.id but accumulate sort hits
  const byId = new Map();
  for (const r of results) {
    if (r.error) { console.error(`[radar] ${r.sub}/${r.sort}: ${r.error}`); continue; }
    for (const it of r.items) {
      const c = classify(it);
      if (c.ageHours > LOOKBACK_HOURS) continue;
      const existing = byId.get(it.id);
      if (existing) {
        existing._hits.push(`${r.sort}@${r.sub}`);
        existing._tags = [...new Set([...existing._tags, ...c.tags])];
      } else {
        byId.set(it.id, { ...it, _tags: c.tags, _ageH: c.ageHours.toFixed(1), _tier: c.tier, _hits: [`${r.sort}@${r.sub}`] });
      }
    }
  }
  const all = [...byId.values()];

  // Sort: SIGNAL first, then HOT, then by score
  all.sort((a, b) => {
    const sA = a._tags.includes('SIGNAL') ? 1 : 0;
    const sB = b._tags.includes('SIGNAL') ? 1 : 0;
    if (sA !== sB) return sB - sA;
    const hA = a._tags.includes('HOT') ? 1 : 0;
    const hB = b._tags.includes('HOT') ? 1 : 0;
    if (hA !== hB) return hB - hA;
    return Number(b.sats || 0) - Number(a.sats || 0);
  });

  const top = all.slice(0, 100);

  if (isJson) {
    console.log(JSON.stringify(top, null, 2));
    return;
  }

  // 1. 主 TSV (覆寫 latest)
  const headers = '# id\tsub\ttier\tscore\tbounty\tncom\tageH\top_since\top_nitems\thits\ttags\ttitle';
  const row = it => [
    it.id, it.sub?.name || '-', it._tier, it.sats || 0, it.bounty || 0,
    it.ncomments || 0, it._ageH, it.user?.since ?? '-', it.user?.nitems ?? '-',
    (it._hits || []).slice(0, 3).join('|'),
    it._tags.join(','),
    (it.title || '').replace(/[\t\n]/g, ' ').slice(0, 100),
  ].join('\t');
  const tsvPath = path.join(outDir, `sn_${ts}.tsv`);
  fs.writeFileSync(tsvPath, [headers, ...top.map(row)].join('\n') + '\n');
  const latest = path.join(outDir, 'sn_latest.tsv');
  try { fs.unlinkSync(latest); } catch {}
  try { fs.symlinkSync(path.basename(tsvPath), latest); } catch {}

  // 2. 累積 SIGNAL targets (append-only)
  const accumFile = path.join(accumDir, 'all_signals.tsv');
  if (!fs.existsSync(accumFile)) fs.writeFileSync(accumFile, '# discovered_at\t' + headers.slice(2) + '\n');
  const sigItems = top.filter(it => it._tags.includes('SIGNAL'));
  for (const it of sigItems) {
    fs.appendFileSync(accumFile, new Date().toISOString() + '\t' + row(it) + '\n');
  }

  // 3. 累積 SELF_POST_OPP (主題挖掘)
  const oppFile = path.join(accumDir, 'self_post_opportunities.tsv');
  if (!fs.existsSync(oppFile)) fs.writeFileSync(oppFile, '# discovered_at\tsub\tscore\tncom\ttitle\tangle\n');
  const oppItems = top.filter(it => it._tags.includes('SELF_POST_OPP'));
  for (const it of oppItems) {
    const angle = SUB_ANGLE[it.sub?.name] || '-';
    fs.appendFileSync(oppFile, [
      new Date().toISOString(),
      it.sub?.name || '-',
      it.sats || 0,
      it.ncomments || 0,
      (it.title || '').replace(/[\t\n]/g, ' ').slice(0, 100),
      angle,
    ].join('\t') + '\n');
  }

  // 4. summary
  const summary = {
    tiers_scanned: TIERS,
    subs_count: SUBS.length,
    api_calls: tasks.length,
    items_fetched: results.reduce((a, r) => a + r.items.length, 0),
    items_unique: all.length,
    items_in_top100: top.length,
    signals: sigItems.length,
    self_post_opps: oppItems.length,
    open_bounty: top.filter(it => it._tags.includes('OPEN_BOUNTY')).length,
    hot: top.filter(it => it._tags.includes('HOT')).length,
  };
  console.log(`[radar v2] ${tsvPath}`);
  console.log(JSON.stringify(summary));

  // 5. quick top-5 SIGNAL preview
  if (sigItems.length) {
    console.log('\n[top SIGNAL]');
    for (const it of sigItems.slice(0, 5)) {
      console.log(`  #${it.id} ~${it.sub?.name} [score=${it.sats}, ncom=${it.ncomments}, ${it._ageH}h] ${it.title}`);
    }
  }
})();
