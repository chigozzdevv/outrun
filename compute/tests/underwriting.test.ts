import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import App from '../../web/src/app.tsx';
import { LiveDataProvider } from '../../web/src/live/provider.tsx';
import { currentEconomics, requiredGrowth, terminalEconomics, type UnderwritingInputs } from '../src/models/underwriting.ts';
import { AUTHORED_BASELINE, runForwardModel, runModel } from '../src/models/model.ts';
import { fallbackSnapshot, validateSnapshot } from '../src/live/snapshot.ts';
import { underwritingInputs } from '../src/models/inputs.ts';
import { type Daily } from '../src/live/schema.ts';
import { receiptReplayRanges, reportingWindow, revisedDailyDates } from '../../server/src/live/service.ts';
import { usd, pct } from '../src/format.ts';

const close = (actual: number, expected: number) => assert.ok(Math.abs(actual - expected) <= Math.max(1e-9, Math.abs(expected) * 1e-11), `${actual} != ${expected}`);
const a: UnderwritingInputs = { dailyVolumeUsd:132_200_000, retainedFeeBps:5, priceUsd:4.356, supplyNear:1_310_000_000, issuanceRate:.025, cashAvailability:1, purchaseAllocation:1 };

// Independently calculated dated inputs from the proposal, not fixed values in the live page.
test('the current capitalization and acquisition bridge reproduce independent dated arithmetic', () => {
  const e = currentEconomics(a);
  close(e.revenueUsd,24_126_500);
  close(e.capitalizationUsd,5_706_360_000);
  close(e.revenueMultiple!,236.51835119059955);
  close(e.acquiredNear,5_538_682.277318641);
  close(e.issuedNear,32_750_000);
  close(e.purchaseCoverage!, .16912006953644705);
  close(e.requiredDailyVolumeUsd!,781_693_150.6849315);
  close(currentEconomics({...a,cashAvailability:.7,purchaseAllocation:.5}).purchaseCoverage!, .05919202433775646);
});

test('reverse underwriting solves four five-year hurdles and forward pricing reproduces the target', () => {
  const expected = [
    [100,1,353_765_620.11558205,.21757847011301745],
    [60,1,589_609_366.8593035,.3485489691419492],
    [100,2,707_531_240.2311641,.3986290027349173],
    [60,2,1_179_218_733.718607,.5490748894903315],
  ];
  for (const [terminalMultiple,target,volume,growth] of expected) {
    const r = requiredGrowth(a,5,terminalMultiple,target)!;
    // Numerical constants rounded here only; full precision is used in calculations.
    assert.ok(Math.abs(r.requiredDailyVolumeUsd - volume) < 2);
    assert.ok(Math.abs(r.growth - growth) < 1e-6);
    const t = terminalEconomics({...a,years:5,volumeGrowth:r.growth,retainedRateGrowth:0,terminalMultiple});
    close(t.priceRatio,target);
    close(t.dailyVolumeUsd,r.requiredDailyVolumeUsd);
  }
});

test('endpoint returns use five growth steps, closing supply and the explicitly selected fee rate', () => {
  const t = terminalEconomics({...a,years:5,volumeGrowth:.2,retainedRateGrowth:0,terminalMultiple:100});
  close(t.revenueUsd,24_126_500 * 1.2**5);
  close(t.supplyNear,1_310_000_000 * 1.025**5);
  close(t.priceUsd,100 * t.revenueUsd / t.supplyNear);
  const compressed = terminalEconomics({...a,years:5,volumeGrowth:.2,retainedRateGrowth:-.1,terminalMultiple:100});
  close(compressed.priceUsd / t.priceUsd,.9**5);
  const fiveToThree = terminalEconomics({...a,years:5,volumeGrowth:.2,retainedRateGrowth:(3/5)**(1/5)-1,terminalMultiple:100});
  close(fiveToThree.retainedFeeBps,3);
  close(fiveToThree.priceUsd / t.priceUsd,.6);
  const m0 = a.priceUsd * a.supplyNear / 24_126_500;
  close(t.priceRatio,100/m0 * 1.2**5 / 1.025**5);
});

test('purchase identity survives costs and allocation, and a higher execution price buys fewer tokens', () => {
  const e = currentEconomics({...a,cashAvailability:.7,purchaseAllocation:.5});
  close(e.purchaseCoverage!, .7*.5 / (e.revenueMultiple! * a.issuanceRate));
  const high = currentEconomics({...a,priceUsd:a.priceUsd*2});
  close(high.acquiredNear,currentEconomics(a).acquiredNear/2);
  close(high.requiredDailyVolumeUsd!,currentEconomics(a).requiredDailyVolumeUsd!*2);
});

test('zero revenue, zero issuance and unavailable acquisition allocations retain their distinct meanings', () => {
  const noRevenue = currentEconomics({...a,retainedFeeBps:0});
  assert.equal(noRevenue.revenueMultiple,null);
  assert.equal(noRevenue.purchaseCoverage,0);
  assert.equal(noRevenue.requiredDailyVolumeUsd,null);
  assert.equal(requiredGrowth({...a,dailyVolumeUsd:0},5,100,1),null);
  assert.equal(currentEconomics({...a,issuanceRate:0}).purchaseCoverage,null);
  assert.equal(currentEconomics({...a,issuanceRate:0}).requiredDailyVolumeUsd,0);
  assert.equal(currentEconomics({...a,purchaseAllocation:0}).requiredDailyVolumeUsd,null);
  assert.throws(() => currentEconomics({...a,priceUsd:0}));
  assert.throws(() => terminalEconomics({...a,years:5,volumeGrowth:.2,retainedRateGrowth:1,terminalMultiple:100,retainedFeeBps:1000}));
});

test('forward acquisitions compound five times while the historical operating-period convention stays reproducible', () => {
  const historical = runModel({...AUTHORED_BASELINE});
  const forward = runForwardModel({...AUTHORED_BASELINE});
  close(historical.summary.final.retainedRevenueUsd,25e6*1.5**4);
  close(forward.rows[0].retainedRevenueUsd,25e6*1.5);
  close(forward.summary.final.retainedRevenueUsd,25e6*1.5**5);
  close(forward.summary.final.closingSupplyNear,1e9*1.025**5);
  const growingPrice = runForwardModel({...AUTHORED_BASELINE,priceMode:'growth',priceGrowth:.5});
  close(growingPrice.summary.final.nonStakerMarkedValueIndex,100*1.5**5);
  growingPrice.rows.forEach(row => close(row.purchasedNear,5e6));
});

test('new live valuation tables and required growth update together without rewriting the historical audit', () => {
  const s = fallbackSnapshot();
  const render = () => renderToStaticMarkup(createElement(LiveDataProvider,{snapshot:s,children:createElement(App)}));
  const before = render();
  s.market.priceUsd *= 2;
  s.chain.issuanceRate = .016;
  const after = render();
  const e = currentEconomics(underwritingInputs(s));
  const r = requiredGrowth(underwritingInputs(s),5,100,1)!;
  assert.notEqual(before,after);
  assert.ok(after.includes(e.revenueMultiple!.toFixed(1)+'×'));
  assert.ok(after.includes(usd(r.requiredDailyVolumeUsd,2)));
  assert.ok(after.includes(pct(r.growth,2)));
  assert.ok(after.includes('28.29'));
  assert.equal((after.match(/<math /g)??[]).length,8);
  assert.equal((after.match(/<math [^>]+aria-label=/g)??[]).length,8);
  assert.ok(after.indexOf('Required five-year volume growth') < after.indexOf('id="holders"'));
  assert.ok(after.indexOf('id="scenario"') < after.indexOf('id="holders"'));
  assert.equal(/NaN|Infinity|\{\{/.test(after),false);
});

test('a reporting buffer excludes recent dates by age while preserving low values on eligible dates', () => {
  const now = Date.parse('2026-10-29T11:00:00Z');
  const series = new Map(['2026-10-24','2026-10-25','2026-10-26','2026-10-27','2026-10-28'].map(date => [date,1]));
  const fees = new Map(series); fees.set('2026-10-26',0); fees.set('2026-10-28',7176);
  const r = reportingWindow([series,fees,series],now);
  assert.equal(r.last,'2026-10-26');
  assert.equal(r.latestAvailableDate,'2026-10-28');
  assert.equal(fees.get(r.last),0,'Publication must not select dates by whether fees look favorable.');
  assert.equal(fees.get('2026-10-28'),7176,'The source observation remains intact.');
  assert.throws(() => reportingWindow([series,fees],Date.parse('2026-11-06')));
});

test('source revisions are detected on overlapping operating values without inventing revisions for new dates or native backfills', () => {
  const day: Daily = {date:'2026-10-24',volumeUsd:100,grossFeesUsd:10,collectionsUsd:5,nativeReceiptsNear:null,priceUsd:null};
  assert.deepEqual(revisedDailyDates([day],[{...day,nativeReceiptsNear:5}]),[]);
  assert.deepEqual(revisedDailyDates([day],[{...day,grossFeesUsd:11},{...day,date:'2026-10-25'}]),['2026-10-24']);
});

test('snapshot publication cannot claim a reporting buffer with inconsistent cutoff metadata', () => {
  const s = fallbackSnapshot();
  s.operating.reporting = {bufferDays:2,latestAvailableDate:'2026-10-27',revisedDates:[]};
  validateSnapshot(s);
  s.operating.reporting.bufferDays = 1;
  assert.throws(() => validateSnapshot(s),/reporting buffer/);
});

test('moving publication backward restores the missing receipt prefix and replays the new endpoint overlap', () => {
  assert.deepEqual(receiptReplayRanges('2026-08-28','2026-10-27','2026-08-30','2026-10-29',3),[
    {start:'2026-08-28',end:'2026-08-30'},
    {start:'2026-10-25',end:'2026-10-27'},
  ]);
  assert.deepEqual(receiptReplayRanges('2026-08-31','2026-10-30','2026-08-30','2026-10-29',3),[
    {start:'2026-10-27',end:'2026-10-30'},
  ]);
});
