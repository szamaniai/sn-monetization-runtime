# sn-monetization-runtime

Cloud cron runtime for the SN Monetization sub-project (sister to `ClaudeEarnSelf-runtime`).

## Workflows
- `sn_radar.yml` — every 15 min, scrapes Stacker News GraphQL for opportunities
  (writes to `data/sn_opportunities/sn_latest.tsv`)

## Public repo = unlimited GitHub Actions minutes.

PAT: `ClaudeEarnSelf-gh-pat` (Keychain, `relayhop` user) — repo scope.
