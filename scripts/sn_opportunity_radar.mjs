// Batch 2.1 (#3) — Stacker News opportunity radar
// 跨 sub × 排序 抓「OP 主動掛賞 ≥ 100 sats 且 child comments 少」的低競爭機會
// 同時抓近期 jobs / freebies。輸出 logs/opportunities/sn_<UTC_ts>.tsv
//
// 用法:
//   node scripts/sn_opportunity_radar.mjs            # 全部 sub
//   node scripts/sn_opportunity_radar.mjs --json     # 印 JSON
//
// 不需要登入 — 純讀 public GraphQL；不消耗對話 token。
// 設計給 GH Actions cron 每 10-15 分鐘跑（Batch 2.2 整合）。

import fs from 'node:fs';
import path from 'node:path';

const BASE = 'https://stacker.news';
const SUBS = ['bitcoin', 'jobs', 'meta', 'tech', 'AGITHON'];
const SORTS = ['recent'];
const MIN_BOUNTY_SATS = 100;
const MAX_COMMENTS_FOR_LOW_COMP = 5;
const LOOKBACK_HOURS = 24;

// SN GraphQL items query — 只取需要欄位降低 payload
// 2026-05-03 schema 升級：加 user.since/nitems 供 parent 篩選硬規則 #3 使用
// 注意：SN User schema **沒有** lastSeenAt 欄位（已驗證 introspection）；
// `since` 是 Int (語意未文件化，疑似 user 最近 item id 或 user row id)；
// OP 真實 last_active 需另查 user 最近 item，radar 階段先輸出 since 供下游決策。
const QUERY = `
query items($sub: String, $sort: String, $when: String, $limit: Limit) {
  items(sub: $sub, sort: $sort, when: $when, limit: $limit) {
    items {
      id
      title
      url
      createdAt
      sats
      bounty
      bountyPaidTo
      ncomments
      user { name since nitems }
      sub { name }
    }
  }
}`;

async function fetchSub(sub, sort = 'recent') {
  const r = await fetch(`${BASE}/api/graphql`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'apollographql-client-name': 'web',
    },
    body: JSON.stringify({
      query: QUERY,
      variables: { sub, sort, when: 'day', limit: 30 },
      operationName: 'items',
    }),
  });
  if (!r.ok) {
    return { sub, error: `HTTP ${r.status}`, items: [] };
  }
  const j = await r.json();
  if (j.errors) return { sub, error: JSON.stringify(j.errors), items: [] };
  return { sub, items: j.data?.items?.items || [] };
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
  // 2026-05-03 新標籤 — 對應 CLAUDE.md 硬規則 #3「高訊噪比熱貼」
  // score >= 100 AND ncom <= 0.3 * score AND age <= 12h
  // (OP last_active 因 schema 限制無法在 radar 階段驗證；下游 reply 流程自行查 OP 最近 item)
  if (score >= 100 && ncom <= 0.3 * score && ageHours <= 12) tags.push('SIGNAL');
  return { tags, ageHours };
}

(async () => {
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const out = path.resolve(process.env.OUT_DIR || 'logs/opportunities');
  fs.mkdirSync(out, { recursive: true });

  const all = [];
  for (const sub of SUBS) {
    for (const sort of SORTS) {
      const r = await fetchSub(sub, sort);
      if (r.error) {
        console.error(`[radar] ${sub}/${sort} error: ${r.error}`);
        continue;
      }
      for (const it of r.items) {
        const c = classify(it);
        if (c.ageHours > LOOKBACK_HOURS) continue;
        all.push({ ...it, _tags: c.tags, _ageH: c.ageHours.toFixed(1) });
      }
    }
  }

  // 排序：先按 tag 數，再按 bounty
  all.sort((a, b) => {
    const ta = a._tags.length;
    const tb = b._tags.length;
    if (tb !== ta) return tb - ta;
    return Number(b.bounty || 0) - Number(a.bounty || 0);
  });

  // 取前 50
  const top = all.slice(0, 50);

  const isJson = process.argv.includes('--json');
  if (isJson) {
    console.log(JSON.stringify(top, null, 2));
  } else {
    const tsvPath = path.join(out, `sn_${ts}.tsv`);
    const lines = [
      '# id\tsub\tscore\tbounty\tncom\tageH\top_since\top_nitems\ttags\ttitle',
      ...top.map(it => [
        it.id,
        it.sub?.name || '-',
        it.sats || 0,
        it.bounty || 0,
        it.ncomments || 0,
        it._ageH,
        it.user?.since ?? '-',
        it.user?.nitems ?? '-',
        it._tags.join(','),
        (it.title || '').replace(/[\t\n]/g, ' ').slice(0, 100),
      ].join('\t')),
    ];
    fs.writeFileSync(tsvPath, lines.join('\n') + '\n');
    console.log(`[radar] wrote ${tsvPath} (${top.length} items)`);
    // 同時更新 latest symlink
    const latest = path.join(out, 'sn_latest.tsv');
    try { fs.unlinkSync(latest); } catch {}
    try { fs.symlinkSync(path.basename(tsvPath), latest); } catch {}
    // 印高優先項
    const hot = top.filter(it => it._tags.includes('OPEN_BOUNTY'));
    if (hot.length) {
      console.log(`\n[radar] ${hot.length} OPEN_BOUNTY:`);
      for (const it of hot.slice(0, 5)) {
        console.log(`  #${it.id} [${it.bounty} sats, ${it.ncomments} com, ${it._ageH}h] ${it.title}`);
      }
    }
  }
})();
