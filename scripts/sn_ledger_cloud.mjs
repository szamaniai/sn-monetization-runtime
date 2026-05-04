#!/usr/bin/env node
// Cloud-safe SN ledger — runs in GH Actions, no auth required (item.cost/sats are public)
// Outputs runtime/data/ledger/YYYY-MM-DD.tsv (daily snapshot) + ledger_latest.tsv
//
// Local complement: scripts/sn_ledger.mjs (private) adds wallet balance reconciliation
// via authenticated `me { privates { sats } }` — keep that local, runtime only does public side.

import fs from 'node:fs';
import path from 'node:path';

const SN_USER = process.env.SN_USER || '366aad5d38';
const REWARD_POOL_RATE = 0.30;  // assumed; reconciliation log will help calibrate

async function gqlAnon(query, variables = {}) {
  const r = await fetch('https://stacker.news/api/graphql', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'apollographql-client-name': 'web',
      'user-agent': 'Mozilla/5.0 (sn-ledger-cloud)',
    },
    body: JSON.stringify({ query, variables }),
  });
  const text = await r.text();
  try { return JSON.parse(text); } catch { return { raw: text.slice(0, 200), status: r.status }; }
}

async function fetchAllUserItems(userName) {
  const items = new Map();
  for (const type of ['posts', 'comments']) {
    let cursor = null;
    let pages = 0;
    while (pages < 60) {
      const q = `query g($name:String!,$type:String!,$cursor:String){
        items(sort:"user", type:$type, name:$name, when:"forever", cursor:$cursor, limit:50){
          cursor
          items { id sats cost commentSats commentCost createdAt parentId title user{name} sub{name} }
        }
      }`;
      const r = await gqlAnon(q, { name: userName, type, cursor });
      const data = r.data?.items;
      if (!data) {
        console.error(`[ledger] error type=${type} page=${pages}: ${JSON.stringify(r.errors || r.raw || 'empty').slice(0,200)}`);
        break;
      }
      const list = data.items || [];
      for (const it of list) {
        if (it.user?.name !== userName) continue;
        items.set(it.id, { ...it, _kind: it.parentId ? 'comment' : 'self_post' });
      }
      if (!data.cursor || list.length === 0) break;
      cursor = data.cursor;
      pages++;
      // Be polite: small delay to avoid rate-limit
      await new Promise(r => setTimeout(r, 250));
    }
  }
  return [...items.values()];
}

function summarize(items) {
  const stats = {
    total_items: items.length,
    total_self_posts: 0,
    total_comments: 0,
    total_cost: 0,
    total_gross_earned: 0,
    total_comment_sats_under_my_posts: 0,
    free_items: 0,
    paid_items: 0,
    earning_items: 0,
    by_sub: {},
    earners: [],
  };
  for (const it of items) {
    const cost = +it.cost || 0;
    const sats = +it.sats || 0;
    stats.total_cost += cost;
    stats.total_gross_earned += sats;
    if (it._kind === 'self_post') {
      stats.total_self_posts++;
      stats.total_comment_sats_under_my_posts += +(it.commentSats || 0);
    } else stats.total_comments++;
    if (cost === 0) stats.free_items++; else stats.paid_items++;
    if (sats > 0) stats.earning_items++;
    const sub = it.sub?.name || '-';
    if (!stats.by_sub[sub]) stats.by_sub[sub] = { count: 0, cost: 0, gross: 0 };
    stats.by_sub[sub].count++;
    stats.by_sub[sub].cost += cost;
    stats.by_sub[sub].gross += sats;
    if (sats > 0) {
      stats.earners.push({ id: it.id, kind: it._kind, sub, cost, sats, parentId: it.parentId, title: (it.title || '').slice(0, 60) });
    }
  }
  stats.earners.sort((a, b) => b.sats - a.sats);
  stats.estimated_net_received = Math.round(stats.total_gross_earned * (1 - REWARD_POOL_RATE));
  stats.estimated_net_pl = stats.estimated_net_received - stats.total_cost;
  stats.gross_pl = stats.total_gross_earned - stats.total_cost;
  return stats;
}

(async () => {
  const items = await fetchAllUserItems(SN_USER);
  const stats = summarize(items);

  const ts = new Date();
  const today = ts.toISOString().slice(0, 10);
  const outDir = path.resolve(process.env.OUT_DIR || 'data/ledger');
  fs.mkdirSync(outDir, { recursive: true });

  // 1. Detailed TSV snapshot
  const tsvPath = path.join(outDir, `${today}.tsv`);
  const headers = '# id\tkind\tsub\tcost\tsats\tcommentSats\tparentId\tcreatedAt\ttitle';
  const rows = items
    .sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt))
    .map(it => [
      it.id, it._kind, it.sub?.name || '-', it.cost || 0, it.sats || 0,
      it.commentSats || 0, it.parentId || '-', it.createdAt,
      (it.title || '').replace(/[\t\n]/g, ' ').slice(0, 60),
    ].join('\t'));
  const banner = [
    `# SN Ledger snapshot ${ts.toISOString()}`,
    `# user: ${SN_USER}`,
    `# total_items: ${stats.total_items} (self_post=${stats.total_self_posts}, comment=${stats.total_comments})`,
    `# total_cost: ${stats.total_cost} sats`,
    `# total_gross_earned: ${stats.total_gross_earned} sats`,
    `# estimated_net_received (×${(1-REWARD_POOL_RATE).toFixed(2)}): ${stats.estimated_net_received} sats`,
    `# gross_pl: ${stats.gross_pl} | est_net_pl: ${stats.estimated_net_pl}`,
    `# earners: ${stats.earning_items}`,
    `# (NOTE: real wallet balance + reconciliation gap done by local sn_ledger.mjs only)`,
  ];
  fs.writeFileSync(tsvPath, [...banner, headers, ...rows].join('\n') + '\n');

  // 2. Latest symlink
  const latest = path.join(outDir, 'ledger_latest.tsv');
  try { fs.unlinkSync(latest); } catch {}
  try { fs.symlinkSync(path.basename(tsvPath), latest); } catch {}

  // 3. Summary JSON for downstream consumers
  const sumPath = path.join(outDir, `${today}_summary.json`);
  fs.writeFileSync(sumPath, JSON.stringify({
    snapshot_at: ts.toISOString(),
    user: SN_USER,
    stats: {
      ...stats,
      earners: stats.earners.slice(0, 10),  // top 10 only in summary
    },
  }, null, 2));

  // 4. Append-only earnings log (only items with sats>0; for trend analysis)
  const earnLog = path.join(outDir, 'earnings_log.tsv');
  if (!fs.existsSync(earnLog)) {
    fs.writeFileSync(earnLog, '# snapshot_at\tid\tkind\tsub\tcost\tsats\tparentId\ttitle\n');
  }
  for (const e of stats.earners) {
    fs.appendFileSync(earnLog, [
      ts.toISOString(), e.id, e.kind, e.sub, e.cost, e.sats, e.parentId || '-',
      e.title.replace(/\t/g, ' '),
    ].join('\t') + '\n');
  }

  console.log(`[ledger] wrote ${tsvPath} (${items.length} items)`);
  console.log(JSON.stringify({
    items: stats.total_items,
    cost: stats.total_cost,
    gross: stats.total_gross_earned,
    est_net_pl: stats.estimated_net_pl,
    earners: stats.earning_items,
  }));
})().catch(e => {
  console.error('[ledger] FATAL:', e.message);
  process.exit(1);
});
