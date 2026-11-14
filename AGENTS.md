# Instructions for coding agents

This file is for AI coding agents working in this repository, including agents that screen it for NEAR HACKATHON. Read `readme.md` first for what the project is. This file covers how to run, check and change it safely.

## The project in one paragraph

Outrun is a live investment thesis page titled "Can NEAR Intents outrun dilution?". It tests whether NEAR Intents fee buybacks offset the new NEAR the network creates. The page combines two kinds of evidence. A **fixed study** (23 August–28 October 2026) traces the money from swap fees to the buyback treasury; it is rebuilt offline from saved source data and never changes. **Live data** (price, supply, issuance, treasury balances, Intents volume, fees and treasury receipts) is fetched by a small server and updates the page's sentences, tables and charts.

## Requirements

- Node.js 22.12 or later, with npm.
- Python 3.10 or later. Python code uses only the standard library; there is no `requirements.txt`.
- Git. The evidence download derives its URL from the `origin` remote.

## Setup

```sh
npm ci
npm run evidence:download
```

`evidence:download` fetches `data/evidence/2026-11-02/sources.zip` (114 MiB) from the GitHub release tagged `evidence-2026-11-02` and keeps it only if its SHA-256 matches `data/evidence/2026-11-02/manifest.json`. Outside a GitHub clone, set `THESIS_EVIDENCE_URL` to the asset URL. The archive is ignored by Git; never commit it or re-zip it, because its whole-file hash is pinned.

## Checking the project

Run these in order. None of them needs network access except the download above.

| Command | Expected result | Time |
| --- | --- | --- |
| `npx tsc --noEmit` | No output, exit code 0 | Seconds |
| `npm test` | `ℹ tests 72`, `ℹ pass 72`, `ℹ fail 0`, then `Ran 5 tests` and `OK` | Seconds |
| `npm run verify:data` | A JSON line ending in `"status": "passed"`, then `49 datasets match their evidence, schemas and recorded relationships.` | Seconds |
| `npm run verify:research` | 38 lines ending in `passed`, a JSON summary with `"studies": 38`, then the 49-dataset line | About a minute |
| `npm run build` | Vite reports `built in …`. A warning about chunks over 500 kB is expected. | Seconds |

What the checks prove:

- `npm test` runs the page, model and server tests with fake network responses, plus the Python catalogue tests.
- `verify:data` re-hashes the 718 captured source files, recomputes the supply ledger across 220 epoch boundaries, verifies 17 ed25519 signatures on NEP-413 intents and rebuilds the withdrawal exhibit.
- `verify:research` extracts the archive into a temporary folder, runs all 38 studies with Python network access blocked, and requires every rebuilt file to match the published bytes.

These checks re-run the analysis on saved data. They do not re-query NEAR. To see live data, run the page.

## Running the page

```sh
npm run dev          # http://127.0.0.1:5174/ with hot reload
npm run build && npm start   # production server, same address
npm run live:refresh # one forced refresh of every live source, then exit
```

- `npm run dev`, `npm start` and `npm run live:refresh` call public APIs and write to `.cache/live-thesis/`. Tests and verify commands do not.
- On a fresh copy the page starts from archived figures. Price and supply go live within a few minutes. Volume and treasury figures follow after the first full refresh, which can take 10 minutes or more because it pages through 60 days of NearBlocks receipts at a rate-limited pace. `NEARBLOCKS_API_KEY` speeds this up.
- `live:refresh` exits with code 1 if any source failed or has never succeeded. The previous verified values are kept, so a non-zero exit is not data loss.
- Only one process refreshes at a time, coordinated by `.cache/live-thesis/refresh.lock`. A lock left by a dead process is removed automatically. If refreshes seem to do nothing, check whether another server is running.
- The line under the page title states which figures are live and the last date the volume figures cover.

## Repository map

| Path | What it holds |
| --- | --- |
| `web/src/app.tsx` | Page structure: the order of chapters, figures and charts |
| `web/src/thesis/article.json` | The authored article: chapters, headings and paragraphs |
| `web/src/thesis/facts.ts` | Sentences built from live data (findings, verdict, checkpoints) |
| `web/src/evidence/*-facts.ts`, `onchain-results.ts` | Sentences built from the fixed study's summaries |
| `web/src/thesis/essay.tsx` | Renders article paragraphs, inline formatting, equations and figures |
| `web/src/evidence/*.tsx`, `web/src/components/*.tsx` | Tables and model sections |
| `web/src/charts/` | ECharts wrappers |
| `web/src/live/` | Live data provider, status line and comparison helpers |
| `web/public/data/` | Published CSV downloads. Generated; do not edit by hand. |
| `server/src/live/service.ts` | Live collection: sources, refresh timing, validation, cache, `/api/thesis` |
| `server/src/live/reviewed.json` | Hashes of the policy documents as last reviewed |
| `compute/src/live/` | Snapshot schema, archived fallback snapshot and validation |
| `compute/src/models/` | Buyback, valuation and holder models |
| `compute/src/{collectors,treasury,activity,routing,verifier,withdrawals}/` | The fixed study, in Python |
| `compute/src/verify/` | `verify:data` and `verify:research` |
| `compute/src/publish.py`, `catalog.py`, `archive.py` | Publication checks and the evidence archive |
| `data/evidence/2026-11-02/` | The fixed evidence edition: `catalog.json`, `manifest.json`, baselines, inputs, seeds and summaries |

## Changing the article

- **Text** lives in `article.json`. A paragraph is either a string or an object with `text`, an optional `historical` version and `requiresReview` source ids.
- **Placeholders.** `{{name}}` is replaced by the matching key from `thesisFacts()` in `facts.ts`, which merges `chainResearchFacts()` and `routingResearchFacts()`. A missing key throws an error. An empty string hides the paragraph.
- **Inline formatting** supports only `[text](https://…)` for links, `[text](/data/….csv)` for downloads, `**bold**` and `` `code` ``.
- **Figures and equations.** A block's `figureAfter` names a figure in `evidenceFigures` in `essay.tsx`. `equations`, `equationAfter` and `equationsAfter` place formulas after specific paragraphs; renumber `equationsAfter` if you insert a paragraph before one.
- **Downloads.** Links to `/data/….csv` must point to files that exist under `web/public/data/` and are listed in `catalog.json`. A test checks this.

## Writing rules

- Never type a number into prose that the data can supply. Compute it in `facts.ts` or an evidence facts file. Fixed facts from the study may appear in `article.json` only when they never change.
- Every number carries its date or period. The fixed study and the live figures cover different dates; say which one a sentence uses.
- Round numbers in sentences and keep exact values in tables and CSV files.
- Use one name for each thing: **collectors** (the three fee-collection accounts), **the three treasury wallets** (buyback, frontend and 1Click), **new NEAR** in plain sentences and **issuance** for the protocol rate or cap, **basis points** in sentences (`bps` only in table labels and figure notes) and **retained fee** for the share of each swap Intents keeps.
- Open each subsection with a plain sentence that states its point. Put caveats after it.
- Keep claims within the evidence. The study shows acquisitions and deliveries; it does not show profit, costs or who funded every receipt.
- Do not change the page's visual design or chart styles unless asked.
- Some sentences are checked word for word by tests, for example "Across and LI.FI demonstrate", "they do not establish purchases, permanent retention or selling by issuance recipients" and "Collections per dollar settled fell from". Run `npm test` after any wording change.

## Evidence rules

- Do not edit anything under `data/evidence/2026-11-02/` or `web/public/data/` by hand. `catalog.json` and `manifest.json` pin their hashes, and `npm run verify:data` will fail.
- `npm run data:build` rebuilds the 28 October observation and model files and republishes all 49 datasets, but only if every rebuilt file matches its frozen bytes. Run it only when you need to prove that; it is not part of normal checking.
- The study's collection modes (`--collect`, `--fetch`, `collect`, `fetch` in `compute/src`) query live APIs and some use macOS-only temporary paths. Do not run them during verification. Use the replay through `verify:research` instead.
- Several published cohorts overlap. The 3,998-receipt earlier acquisition edition, the 742-receipt direct cohort and the 609-receipt indexed cohort are receipt subsets of the 6,125-receipt edition, and the direct and indexed cohorts share 185 receipts. Never add them together.
- If `verify:data` passes unexpectedly after a failed rebuild, delete `.cache/evidence/` and run it again; the workspace cache is trusted once extracted.

## Live data gates

The live service holds back figures it cannot verify instead of publishing wrong ones.

- **Policy documents.** `reviewed.json` stores the hash of each document's text as last reviewed. When a page changes, its state becomes `changed` and the article swaps in the dated `historical` wording or hides that paragraph. To review: compare the current copy in `.cache/live-thesis/sources/document-<id>.json` with the stored reviewed copy `document-<id>-<hash>.json`. If the article's claims still hold, put the new hash in `reviewed.json` and update `reviewedAt`. If the DefiLlama adapter changes, the volume and fee feed pauses until it is reviewed.
- **Protocol versions.** `REVIEWED_PROTOCOL_VERSIONS` in `compute/src/live/schema.ts` lists the NEAR protocol versions whose total-supply rule has been checked (86, 87 and 88). A new version pauses the volume, receipt and supply figures. Before adding it, read the nearcore release notes and confirm that block total supply still equals the previous supply plus epoch minting minus burnt balance (`core/primitives/src/block.rs`).
- **Failed sources.** A failed feed keeps its last verified values and records the error in `health`. The status line says when volume figures have not updated for six hours.

## Safety

- Never commit secrets. Configure providers through `NEAR_RPC_URL`, `NEAR_ARCHIVE_URL` and `NEARBLOCKS_API_KEY`; `.env*` files are ignored.
- If an RPC URL contains an API key, it will also appear in the cached source files under `.cache/live-thesis/`. That folder is ignored by Git; keep it that way.
- Do not push, force-push, change the repository's visibility or publish releases without the owner's approval.
- The project reads public data only. It deploys no contract and signs no transactions; keep it that way.

## Before you finish a change

1. `npx tsc --noEmit`
2. `npm test`
3. `npm run build`
4. `npm run verify:data`, if you touched anything under `compute/` or `data/`
5. Open the page and read the sections you changed with live data.
