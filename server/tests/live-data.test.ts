import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import App from '../../web/src/app.tsx';
import { LiveDataProvider } from '../../web/src/live/provider.tsx';
import { change, movement } from '../../web/src/live/metrics.ts';
import { fallbackSnapshot, validateSnapshot } from '../../compute/src/live/snapshot.ts';
import { scenarioAssumptions, scenarioExamples } from '../../compute/src/models/inputs.ts';
import { thesisFacts } from '../../web/src/thesis/facts.ts';
import { type LiveSnapshot } from '../../compute/src/live/schema.ts';
import { epochStakeObservation, LiveService, losslessReceipts, native, normalizeDocument, parseSeries, units, validateNativeLedger, type Receipt } from '../src/live/service.ts';
import { pct } from '../../compute/src/format.ts';

function render(s: LiveSnapshot) { return renderToStaticMarkup(createElement(LiveDataProvider, { snapshot: s, children: createElement(App) })); }
function currentFixture(): LiveSnapshot {
  const s = fallbackSnapshot();
  s.review.sources.forEach(source => { source.state = 'unchanged'; source.currentHash = source.reviewedHash = 'reviewed'; });
  return s;
}

test('validator stake counts each member of the finalized epoch exactly once and excludes the next set', () => {
  const epoch = '41eWWbirXU5oZjqYb3wc37FL5aCbE7fN17vT1AFHBwgi';
  const before = {height: 109, hash: 'before', prev_hash: 'older', epoch_id: 'previous', next_epoch_id: epoch, total_supply: units('100').toString()};
  const first = {...before, height: 110, hash: 'first', prev_hash: before.hash, epoch_id: epoch, next_epoch_id: 'next'};
  const final = {...first, height: 125, hash: 'final'};
  const validators = [{account_id: 'one.near', stake: units('30').toString()}, {account_id: 'two.near', stake: units('20').toString()}];
  const info = {epoch_start_height: 110, current_validators: validators, next_validators: [{account_id: 'other.near', stake: units('90').toString()}]};
  assert.deepEqual(epochStakeObservation(info, final, first, before), {activeValidatorStakeNear: 50, validatorEpochId: epoch, validatorEpochStartHeight: 110});
  assert.throws(() => epochStakeObservation({...info, current_validators: [...validators, validators[0]]}, final, first, before), /duplicate/);
  assert.throws(() => epochStakeObservation(info, {...final, total_supply: units('49').toString()}, first, before), /exceeds/);
  assert.throws(() => epochStakeObservation(info, {...final, epoch_id: 'different'}, first, before), /epoch boundary/);
});

test('holder model and prose share the observed active-stake denominator and the explicit fallback', () => {
  const s = currentFixture();
  assert.equal(scenarioAssumptions(s).stakingRatio, .45);
  assert.match(thesisFacts(s).holderExample, /assumes active validator stake equals 45%/);
  const illustrative = scenarioExamples(s)[0].model!.summary.final;
  s.chain.activeValidatorStakeNear = s.chain.supplyNear * .6;
  s.chain.validatorEpochId = '41eWWbirXU5oZjqYb3wc37FL5aCbE7fN17vT1AFHBwgi';
  s.chain.validatorEpochStartHeight = s.chain.blockHeight - 1;
  validateSnapshot(s);
  assert.equal(scenarioAssumptions(s).stakingRatio, .6);
  assert.match(thesisFacts(s).holderExample, /60\.00% of supply/);
  const observed = scenarioExamples(s)[0].model!.summary.final;
  assert.ok(observed.stakerSupplyShareIndex < illustrative.stakerSupplyShareIndex);
  assert.equal(observed.nonStakerSupplyShareIndex, illustrative.nonStakerSupplyShareIndex);
  delete s.chain.validatorEpochId;
  assert.throws(() => validateSnapshot(s), /validator stake/);
});
function alteredFixture() {
  const s = currentFixture(), later = s.operating.periods[1];
  for (const row of s.operating.daily) if (row.date >= later.start && row.date < later.endExclusive) { row.collectionsUsd! *= .5; row.volumeUsd! *= 1.5; row.nativeReceiptsNear! *= 2; }
  later.collectionsUsd *= .5; later.volumeUsd *= 1.5; later.nativeReceiptsNear *= 2;
  const w = s.operating.supply.windows[1];
  w.externalNativeWalletReceiptsNEAR = String(later.nativeReceiptsNear);
  w.receiptQuantityToGrossMintingPct = String(later.nativeReceiptsNear / Number(w.grossMintedNEAR) * 100);
  s.market.priceUsd *= 2; s.chain.supplyNear *= 1.1; s.chain.issuanceRate = .016;
  s.id = 'changed-live-evidence';
  validateSnapshot(s);
  return s;
}

test('live evidence drives matching chart values, prose, protocol claims and model results', () => {
  const before = render(currentFixture());
  const s = alteredFixture(), after = render(s), facts = thesisFacts(s);
  assert.notEqual(before, after);
  assert.match(facts.collectionComparison, /decreased/);
  assert.match(facts.nativeComparison, /increased/);
  assert.ok(after.replace(/<[^>]*>/g, '').includes(facts.nativeComparison.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')));
  assert.ok(after.includes(`/api/thesis/native.csv?snapshot=${s.id}`));
  assert.equal(after.includes('collection ratios improved'), false);
  assert.equal(after.includes('collection ratio improved'), false);
  assert.ok(after.includes('Collections per dollar settled fell from'));
  assert.ok(after.replace(/<[^>]*>/g, '').includes(facts.protocolPolicy));
  assert.ok(after.includes('1.60%'));
  const final = scenarioExamples(s)[0].model!.summary.final;
  assert.ok(after.includes(pct(final.purchaseCoverage, 2)));
  assert.ok(after.includes(s.market.priceUsd.toFixed(3)));
  assert.ok(after.includes(`/api/thesis/daily.csv?snapshot=${s.id}`));
  assert.equal(/\{\{\w+\}\}|NaN|Infinity/.test(after), false);
  assert.equal(after.includes('Test the assumptions'), false);
  assert.ok(after.includes('28.29'), 'The explicitly dated publisher example is preserved.');
});

test('snapshot validation rejects missing days, conflicting exhibit totals, partial days and fake displayed amounts', () => {
  validateSnapshot(currentFixture());
  const missing = currentFixture(); missing.operating.daily.splice(-1, 1); assert.throws(() => validateSnapshot(missing));
  const total = currentFixture(); total.operating.periods[1].nativeReceiptsNear += 1; assert.throws(() => validateSnapshot(total));
  const decimal = currentFixture(); decimal.operating.supply.windows[1].grossMintedNEAR = '1'; assert.throws(() => validateSnapshot(decimal));
  const ratio = currentFixture(); ratio.operating.supply.windows[1].receiptQuantityToGrossMintingPct = '99'; assert.throws(() => validateSnapshot(ratio));
  const partial = currentFixture(); partial.operating.endExclusive = '2026-10-29'; assert.throws(() => validateSnapshot(partial));
  const future = currentFixture(); future.market.asOf = '2030-01-01'; assert.throws(() => validateSnapshot(future));
});

test('complete-day parsing excludes the current UTC day and never invents missing or malformed values', () => {
  const clock = Date.parse('2026-10-28T14:00:00Z');
  const rows = Array.from({ length: 65 }, (_, i) => [(Date.parse('2026-08-25') + i * 86400000) / 1000, i]);
  const parsed = parseSeries({ totalDataChart: rows }, clock);
  assert.equal(parsed.has('2026-10-28'), false);
  assert.equal(parsed.get('2026-10-27'), 63);
  assert.throws(() => parseSeries({ totalDataChart: [...rows, rows[0]] }, clock));
  assert.throws(() => parseSeries({ totalDataChart: rows.map((row, i) => i === 1 ? [row[0], null] : row) }, clock));
  assert.throws(() => parseSeries({ totalDataChart: rows.map((row, i) => i === 1 ? [row[0] + 1, row[1]] : row) }, clock));
});

test('native source parsing preserves amounts beyond JavaScript integer precision', () => {
  assert.equal(native(units('248972632854913769973175269', 0)), '248.972632854913769973175269');
  const row = losslessReceipts('{"deposit":8.737979851818108e+26,"block_timestamp":1793170818386534400}') as { deposit: string; block_timestamp: string };
  assert.equal(row.deposit, '8.737979851818108e+26');
  assert.equal(native(units(row.deposit, 0)), '873.7979851818108');
  assert.equal(row.block_timestamp, '1793170818386534400');
  assert.throws(() => units('-1'));
  assert.throws(() => units('1e999999'));
});

test('exact published receipt totals cannot be replaced by rounded floating-point quantities', async () => {
  const text = await readFile(new URL('../../data/evidence/2026-11-02/seeds/native-receipts.csv', import.meta.url), 'utf8');
  const receipts: Receipt[] = text.trim().split(/\r?\n/).slice(1).map(line => {
    const [, , utc, block, id, transactionHash, predecessor, receiver, amount] = line.split(',');
    return { id, timestampNs: (BigInt(Date.parse(utc)) * 1000000n).toString(), block: Number(block), transactionHash, predecessor, receiver, amountYocto: units(amount).toString() };
  });
  const s = currentFixture();
  validateNativeLedger(s, receipts);
  s.operating.supply.windows[0].externalNativeWalletReceiptsNEAR = String(Number(s.operating.supply.windows[0].externalNativeWalletReceiptsNEAR));
  assert.throws(() => validateNativeLedger(s, receipts), /exact published quantity/);
});

test('zero issuance and a zero starting denominator never create fabricated percentage claims', () => {
  const s = currentFixture(); s.chain.issuanceRate = 0;
  const html = render(s);
  assert.match(thesisFacts(s).multipleExample, /no nonzero denominator/);
  assert.equal(/NaN|Infinity/.test(html), false);
  assert.equal(change(5, 0), null);
  assert.equal(movement(change(5, 0)), 'cannot be compared with a zero starting value');
});

test('a changed or unavailable primary document suppresses the affected current-policy claim', () => {
  const s = currentFixture();
  const before = render(s);
  assert.ok(before.replace(/<[^>]*>/g, '').includes('The 1Click fee schedule sets'));
  s.review.sources.find(source => source.id === 'fees')!.state = 'changed';
  const after = render(s);
  assert.equal(after.replace(/<[^>]*>/g, '').includes('The 1Click fee schedule sets'), false);
  assert.doesNotMatch(after, /changed after the .* review|has not been reverified|last substantive review/i);
  s.review.sources.find(source => source.id === 'terms')!.state = 'unavailable';
  const unavailable = render(s);
  assert.doesNotMatch(unavailable, /has not been reverified|last substantive review/i);
  assert.ok(unavailable.replace(/<[^>]*>/g, '').includes('The 1Click terms dated 7 October 2026 named'));
  assert.equal(normalizeDocument('<main>A  B</main><script>timestamp=123</script>'), 'A B');
  assert.equal(normalizeDocument('if (fee < 5 && other > 3) charge();'), 'if (fee < 5 && other > 3) charge();');
});

test('cold starts and every source-review state keep maintenance and revision notes out of the thesis', () => {
  const internalCopy = /has not been reverified|last substantive review|economic implications require review|in this snapshot|captured source responses|latest refresh|overlapping published daily observations|current wallet inventory has not been verified/i;
  const revisionCopy = /\b(?:expanded|broader|extended)\s+(?:acquisition|collector|research|ledger|study|evidence|analysis|records|findings|dataset)|\bpreviously identified\b|\bthe chain now\b|custody reconstruction is complete|fixed receipt study|complete first UTC day|this extends acquisition evidence|early-year criticism/i;
  const plumbingCopy = /\brpc\b|\barchival\b|\bpips?\b|pinned block|receipt trees?|raw indexed|Ed25519|independently queried|block hashes?|decimal scales/i;
  const cold = render(fallbackSnapshot());
  assert.doesNotMatch(cold.replace(/<[^>]*>/g, ' '), internalCopy);
  assert.doesNotMatch(cold.replace(/<[^>]*>/g, ' '), revisionCopy);
  assert.doesNotMatch(cold.replace(/<[^>]*>/g, ' '), plumbingCopy);
  assert.doesNotMatch(cold, /<p><\/p>|<h3>[^<]+<\/h3><\/div>/);
  for (const state of ['unchanged', 'changed', 'unavailable', 'unreviewed'] as const) {
    const s = currentFixture();
    s.review.sources.forEach(source => { source.state = state; });
    const html = render(s), visible = html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
    assert.doesNotMatch(visible, internalCopy, state);
    assert.doesNotMatch(visible, revisionCopy, state);
    assert.doesNotMatch(visible, plumbingCopy, state);
    assert.doesNotMatch(html, /<p><\/p>|<h3>[^<]+<\/h3><\/div>/);
    assert.equal(visible.includes('The 1Click fee schedule sets'), state === 'unchanged');
    assert.ok(visible.includes('The 1Click terms dated 7 October 2026 named'));
    assert.equal(visible.includes('On 2 November 2026 the 1Click fee schedule listed'), state !== 'unchanged');
    assert.equal(visible.includes('The fee aggregation service allows'), state === 'unchanged');
  }
});

test('failed feeds preserve the last verified values on disk and snapshot-specific CSV downloads', async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), 'intent-live-test-'));
  const service = new LiveService({ cacheDir, fetcher: (async () => { throw Error('test source unavailable'); }) as typeof fetch });
  try {
    await service.start(false);
    const before = structuredClone(service.snapshot);
    await service.refresh(true);
    assert.deepEqual(service.snapshot.operating, before.operating);
    assert.deepEqual(service.snapshot.market, before.market);
    assert.deepEqual(service.snapshot.chain, before.chain);
    assert.ok(service.snapshot.health.market.error);
    assert.ok(service.snapshot.health.operating.error);
    const persisted = JSON.parse(await readFile(join(cacheDir, 'snapshot.json'), 'utf8'));
    validateSnapshot(persisted);
    assert.deepEqual(persisted.operating.periods, before.operating.periods);
    let body = '', status = 0;
    const response = { writeHead: (value: number) => { status = value; }, end: (value: string) => { body = value; } };
    await service.handle({ url: `/api/thesis/daily.csv?snapshot=${service.snapshot.id}`, method: 'GET' } as never, response as never);
    assert.equal(status, 200);
    assert.ok(body.includes('2026-10-21,'));
    assert.equal(body.trim().split('\n').length, persisted.operating.daily.length + 1);
    await service.handle({ url: '/api/thesis/daily.csv?snapshot=unknown', method: 'GET' } as never, response as never);
    assert.equal(status, 410, 'An expired download must not silently return a different snapshot.');
  } finally { service.stop(); await rm(cacheDir, { recursive: true, force: true }); }
});
