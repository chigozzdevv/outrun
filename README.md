# Outrun

Outrun is a live investment thesis for NEAR. It asks one question: can NEAR Intents outrun dilution?

NEAR creates new tokens every year to pay validators. Part of the fees from NEAR Intents swaps is used to buy NEAR back. Outrun follows that money on-chain and measures whether the buybacks can keep up with the new tokens.

Built for NEAR HACKATHON 2026, Investment Thesis track. Everything in this repository was built during the hackathon.

## The answer

**Not yet.** As of 14 November 2026:

- From 23 August to 28 October 2026, buybacks equaled about **5%** of the new NEAR the network created.
- Even in the best case, where Intents keeps 0.05% of every swap and spends all of it on NEAR, today's fees would buy only about **21%** of the new NEAR each year.
- Under the same assumptions, matching the new NEAR would take about **4.8 times** today's volume, roughly $600m of swaps a day.
- To keep NEAR's price flat for five years at 100 times Intents' yearly fees, volume would need to grow about **17%** a year.
- Volume over the last 90 days was **223%** higher than a year earlier, but the last 30 days fell **21.7%** from the 30 days before.

The page updates these figures from live data, dates every number and lists what the data cannot show. The full argument, with the evidence for and against it, is in [THESIS.md](THESIS.md).

## What's inside

- **The thesis page.** A single article. Its numbers, tables and charts come from live chain and market data, and a line under the title shows when they were last updated.
- **The money trail.** A study of 23 August–28 October 2026 that follows fee payments into NEAR purchases and on to the treasury. It covers 6,125 receipts into the three fee-collection accounts (the collectors), 2,578 receipts into the three treasury wallets, the treasury's payments and who controls its wallets.
- **The model.** How much NEAR the fees can buy, the volume needed to match the new NEAR, the growth today's price assumes, and dilution for holders who stake and holders who don't.
- **The evidence.** 49 published datasets with SHA-256 hashes, and 38 studies that rebuild the results offline.

## Run it

Requires Node.js 22.12 or later and Python 3.10 or later. Python uses only the standard library.

```sh
npm ci
npm run evidence:download
npm run dev
```

Then open http://127.0.0.1:5174/.

`npm run evidence:download` fetches the saved source data (`sources.zip`, 114 MiB) from this repository's `evidence-2026-11-02` release and checks its SHA-256 against `data/evidence/2026-11-02/manifest.json`. The tests and the verify commands need it. The page does not.

## Check it

| Do | See |
| --- | --- |
| `npm test` | 72 JavaScript tests pass, then `Ran 5 tests` and `OK` |
| `npm run verify:data` | `"status": "passed"`, then `49 datasets match their evidence, schemas and recorded relationships.` |
| `npm run verify:research` | 38 lines ending in `passed`, then the same 49-dataset line. Takes about a minute. |
| Open the page | "Not yet." under the title, and a line saying which figures are live |

- The tests and verify commands need no network access.
- On a fresh copy, the page starts with archived figures. Price and supply go live within minutes. Volume and treasury figures can take 10 minutes or more, because the first refresh rebuilds 60 days of treasury receipts from a rate-limited indexer.
- `npm run live:refresh` refreshes every live source once. Exit code 1 means a source failed; the last verified values are kept.
- `npm run build` then `npm start` runs the production server at the same address. On macOS, double-clicking `start.command` installs and starts the page.

### Optional settings

| Variable | Purpose |
| --- | --- |
| `NEAR_RPC_URL` | NEAR RPC endpoint. Default: `https://rpc.mainnet.near.org` |
| `NEAR_ARCHIVE_URL` | Archival RPC endpoint. Default: `https://archival-rpc.mainnet.fastnear.com` |
| `NEARBLOCKS_API_KEY` | Faster NearBlocks access for the treasury receipts |
| `PORT`, `HOST` | Address for `npm start`. Default: `127.0.0.1:5174` |
| `THESIS_EVIDENCE_URL` | Download `sources.zip` from another location |

## How NEAR fits in

Every measurement comes from NEAR mainnet:

- **Supply.** Total supply, new tokens and burns come from block headers, reconciled exactly across 220 epoch boundaries.
- **Buybacks.** Collector balances and exchanges come from the NEAR Intents verifier contract (`intents.near`). Sampled withdrawals are checked against their signed NEP-413 intents.
- **Treasury.** Receipts and payments are traced for the three treasury wallets, and control comes from their Sputnik DAO and multisig policies.
- **Fees.** Fee rules come from the NEAR Intents and 1Click documentation.

Outrun only reads public data. It deploys no contract and signs no transactions.

## Data sources

| Source | Used for | Refresh |
| --- | --- | --- |
| NEAR RPC | Latest block, total supply, issuance settings, treasury balances, validator stake | Every minute |
| neardata.xyz, FastNEAR archival RPC | Block headers for supply accounting | When needed |
| NearBlocks API | NEAR received by the three treasury wallets | Every 5 minutes |
| DefiLlama | Intents volume, fees and collections; NEAR price | Every 5 minutes; price every minute |
| NEAR Intents docs, NEAR AI terms, DefiLlama adapter | Fee rules and collection method | Hourly |

The study's source responses, including NearStats routing data (CC BY 4.0), are saved in `sources.zip` with a hash for each file.

## Repository

| Path | Contents |
| --- | --- |
| `web/` | The thesis page. Article text is in `web/src/thesis/article.json`; sentences built from data are in `web/src/thesis/facts.ts`. |
| `server/` | Live data service: collection, validation, caching and the HTTP server |
| `compute/src/models/` | Buyback, valuation and holder models, shared by the page and the tests |
| `compute/src/live/` | The live snapshot format and its validation |
| `compute/src/` (other folders) | The money-trail study and its checks, in Python |
| `data/evidence/2026-11-02/` | The evidence edition: dataset catalogue, hash manifest, baselines and summaries |

Instructions for coding agents are in [AGENTS.md](AGENTS.md). [CLAUDE.md](CLAUDE.md) loads the same file for Claude Code.

## Built with

- React 19, ECharts 6, Vite 8, TypeScript 7 and tsx. Python uses only the standard library.
- Codex and Kiro were used as AI coding assistants.

## License

MIT. See [LICENSE](LICENSE).
