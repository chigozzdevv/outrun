import assert from 'node:assert/strict';
import test from 'node:test';
import {
  AUTHORED_BASELINE,
  ModelValidationError,
  calculatePriceFrontier,
  explainScenario,
  runModel,
  runSensitivity,
  validateAssumptions,
} from '../src/models/model.ts';

const close = (actual: number, expected: number, tolerance = 1e-9) => {
  assert.ok(Math.abs(actual - expected) <= tolerance * Math.max(1, Math.abs(expected)), `${actual} differs from ${expected}`);
};

test('USD, basis points and native-token quantities have coherent units', () => {
  const result = runModel({ ...AUTHORED_BASELINE });
  const first = result.rows[0];
  close(first.volumeUsd, 50e9);
  close(first.retainedRevenueUsd, 25e6);
  close(first.purchasedNear, 5e6);
  close(first.issuedNear, 25e6);
  close(first.purchaseCoverage!, 0.2);
  close(first.closingSupplyNear, 1.025e9);
  close(first.burnCoverage!, 0);
});

test('changing purchases from held to staked never destroys native supply', () => {
  const held = runModel({ ...AUTHORED_BASELINE, stakeShare: 0 });
  const staked = runModel({ ...AUTHORED_BASELINE, stakeShare: 1 });
  held.rows.forEach((row, i) => {
    close(row.closingSupplyNear, staked.rows[i].closingSupplyNear);
    close(row.totalBurnNear, staked.rows[i].totalBurnNear);
    close(row.purchasedNear, staked.rows[i].purchasedNear);
  });
  close(held.summary.final.cumulativeStakedPurchasesNear, 0);
  close(staked.summary.final.cumulativeHeldPurchasesNear, 0);
  close(staked.summary.final.cumulativeStakedPurchasesNear, staked.summary.totalPurchasedNear);
});

test('a purchased-and-burned token passes through acquisition and burn once', () => {
  const result = runModel({ ...AUTHORED_BASELINE, years: 1, burnShare: 1 });
  const row = result.rows[0];
  close(row.purchasedNear, 5e6);
  close(row.purchaseBurnNear, 5e6);
  close(row.totalBurnNear, 5e6);
  close(row.closingSupplyNear, 1.02e9);
  close(row.cumulativeHeldPurchasesNear + row.cumulativeStakedPurchasesNear, 0);
});

test('every annual supply path and the full horizon reconcile', () => {
  const result = runModel({ ...AUTHORED_BASELINE, burnShare: 0.3, stakeShare: 0.2, annualGasBurnNear: 1e6, gasBurnGrowth: 0.1 });
  result.rows.forEach((row, index) => {
    close(row.closingSupplyNear, row.openingSupplyNear + row.issuedNear - row.gasBurnNear - row.purchaseBurnNear);
    close(row.purchasedNear, row.heldPurchasesNear + row.stakedPurchasesNear + row.purchaseBurnNear);
    if (index > 0) close(row.openingSupplyNear, result.rows[index - 1].closingSupplyNear);
  });
  close(result.summary.final.closingSupplyNear, AUTHORED_BASELINE.openingSupplyNear + result.summary.totalIssuedNear - result.summary.totalBurnNear);
});

test('20% versus 50% growth changes future years, not the first annual observation', () => {
  const low = runModel({ ...AUTHORED_BASELINE, volumeGrowth: 0.2 });
  const high = runModel({ ...AUTHORED_BASELINE, volumeGrowth: 0.5 });
  close(low.rows[0].retainedRevenueUsd, high.rows[0].retainedRevenueUsd);
  close(low.summary.final.retainedRevenueUsd, 51.84e6);
  close(high.summary.final.retainedRevenueUsd, 126.5625e6);
  assert.ok(high.summary.final.purchaseCoverage! > low.summary.final.purchaseCoverage!);
});

test('constant-multiple purchase coverage obeys the identity across growth paths', () => {
  for (const growth of [-0.5, 0, 0.2, 0.5]) {
    const result = runModel({ ...AUTHORED_BASELINE, priceMode: 'multiple', volumeGrowth: growth, burnShare: 0.3 });
    result.rows.forEach((row) => close(row.purchaseCoverage!, AUTHORED_BASELINE.purchaseAllocation / (AUTHORED_BASELINE.revenueMultiple * AUTHORED_BASELINE.issuanceRate)));
  }
  const low = runModel({ ...AUTHORED_BASELINE, priceMode: 'multiple', volumeGrowth: 0.2 });
  const high = runModel({ ...AUTHORED_BASELINE, priceMode: 'multiple', volumeGrowth: 0.5 });
  assert.ok(high.summary.final.executionPriceUsd > low.summary.final.executionPriceUsd);
  close(low.summary.final.purchaseCoverage!, high.summary.final.purchaseCoverage!);
});

test('cash costs modify the constant-multiple identity by the cash-availability factor', () => {
  const result = runModel({ ...AUTHORED_BASELINE, years: 1, priceMode: 'multiple', annualCashCostsUsd: 10e6, purchaseAllocation: 0.8 });
  const row = result.rows[0];
  close(row.availableCashUsd, 15e6);
  close(row.purchaseBudgetUsd, 12e6);
  close(row.reserveCashUsd, 3e6);
  close(row.purchaseCoverage!, 0.8 * (15 / 25) / (200 * 0.025));
});

test('an unfunded cash deficit cannot create a negative budget or buy tokens', () => {
  const result = runModel({ ...AUTHORED_BASELINE, years: 1, annualCashCostsUsd: 40e6, burnShare: 1 });
  const row = result.rows[0];
  close(row.cashDeficitUsd, 15e6);
  close(row.purchaseBudgetUsd, 0);
  close(row.purchasedNear, 0);
  close(row.purchaseBurnNear, 0);
  close(row.closingSupplyNear, 1.025e9);
});

test('zero issuance produces null coverage, not infinity or a fabricated percentage', () => {
  const result = runModel({ ...AUTHORED_BASELINE, issuanceRate: 0 });
  result.rows.forEach((row) => {
    assert.equal(row.purchaseCoverage, null);
    assert.equal(row.burnCoverage, null);
    close(row.closingSupplyNear, 1e9);
    close(row.stakerRewardNear, 0);
    assert.ok(Object.values(row).every((value) => value === null || Number.isFinite(value)));
  });
  assert.equal(calculatePriceFrontier(result.rows[0], 0).supplyBreakEvenStatus, 'no-issuance');
});

test('contracting volume and independent price paths remain finite', () => {
  const result = runModel({ ...AUTHORED_BASELINE, volumeGrowth: -0.5, priceMode: 'growth', priceGrowth: -0.5 });
  close(result.summary.final.retainedRevenueUsd, 1.5625e6);
  close(result.summary.final.executionPriceUsd, 0.3125);
  result.rows.forEach((row) => assert.ok(Object.values(row).every((value) => value === null || Number.isFinite(value))));
});

test('staker rewards are a distribution of already-counted issuance', () => {
  const result = runModel({ ...AUTHORED_BASELINE, years: 1 });
  const row = result.rows[0];
  close(row.stakerRewardNear, 47.5);
  close(row.stakerBalanceNear, 1047.5);
  close(row.closingSupplyNear, 1.025e9);
  close(row.nonStakerMarkedValueIndex, 100);
  assert.ok(row.nonStakerSupplyShareIndex < 100);
  assert.ok(row.stakerSupplyShareIndex > row.nonStakerSupplyShareIndex);
});

test('staking commission affects the holder, not the total minted quantity', () => {
  const low = runModel({ ...AUTHORED_BASELINE, years: 1, validatorCommission: 0 });
  const high = runModel({ ...AUTHORED_BASELINE, years: 1, validatorCommission: 1 });
  close(low.rows[0].issuedNear, high.rows[0].issuedNear);
  close(low.rows[0].closingSupplyNear, high.rows[0].closingSupplyNear);
  close(high.rows[0].stakerRewardNear, 0);
  close(high.rows[0].stakerSupplyShareIndex, high.rows[0].nonStakerSupplyShareIndex);
});

test('purchase-equivalence and supply-break-even prices are distinct', () => {
  const row = runModel({ ...AUTHORED_BASELINE, years: 1, annualGasBurnNear: 1e6 }).rows[0];
  const held = calculatePriceFrontier(row, 0);
  close(held.purchaseEquivalencePriceUsd!, 1);
  assert.equal(held.supplyBreakEvenPriceUsd, null);
  assert.equal(held.supplyBreakEvenStatus, 'unreachable-with-this-policy');
  const burned = calculatePriceFrontier(row, 0.5);
  close(burned.supplyBreakEvenPriceUsd!, 0.5 * 25e6 / 24e6);
  const atThreshold = runModel({ ...AUTHORED_BASELINE, years: 1, annualGasBurnNear: 1e6, burnShare: 0.5, initialPriceUsd: burned.supplyBreakEvenPriceUsd! });
  close(atThreshold.rows[0].netSupplyChangeNear, 0);
});

test('unrelated native burns can cover issuance without a purchase-dependent boundary', () => {
  const row = runModel({ ...AUTHORED_BASELINE, years: 1, annualGasBurnNear: 25e6 }).rows[0];
  const frontier = calculatePriceFrontier(row, 0);
  assert.equal(frontier.supplyBreakEvenStatus, 'already-covered-by-other-burns');
  assert.equal(frontier.supplyBreakEvenPriceUsd, null);
});

test('invalid shares, non-finite inputs and impossible supply paths are rejected', () => {
  assert.ok(validateAssumptions({ ...AUTHORED_BASELINE, burnShare: 0.8, stakeShare: 0.3 }).length > 0);
  assert.throws(() => runModel({ ...AUTHORED_BASELINE, initialPriceUsd: Number.NaN }), ModelValidationError);
  assert.throws(() => runModel({ ...AUTHORED_BASELINE, annualGasBurnNear: -1 }), ModelValidationError);
  assert.throws(() => runModel({ ...AUTHORED_BASELINE, annualGasBurnNear: 2e9 }), /entire token supply/);
  assert.throws(() => runModel({ ...AUTHORED_BASELINE, dailyVolumeUsd: 1e16 }), /more acquired tokens than the total supply/);
  assert.throws(() => runModel({ ...AUTHORED_BASELINE, priceMode: 'multiple', retainedFeeBps: 0 }), /requires positive retained revenue/);
});

test('a zero external-revenue scenario remains usable under an independent price rule', () => {
  const row = runModel({ ...AUTHORED_BASELINE, years: 1, retainedFeeBps: 0 }).rows[0];
  close(row.retainedRevenueUsd, 0);
  close(row.purchasedNear, 0);
  close(row.purchaseCoverage!, 0);
});

test('raising an independent execution price halves purchases without changing dollar revenue', () => {
  const base = runModel({ ...AUTHORED_BASELINE, years: 1 });
  const high = runModel({ ...AUTHORED_BASELINE, years: 1, initialPriceUsd: 10 });
  close(high.rows[0].retainedRevenueUsd, base.rows[0].retainedRevenueUsd);
  close(high.rows[0].purchaseBudgetUsd, base.rows[0].purchaseBudgetUsd);
  close(high.rows[0].purchasedNear, base.rows[0].purchasedNear / 2);
  close(high.rows[0].issuedNear, base.rows[0].issuedNear);
});

test('one-variable sensitivity reports invalid cases instead of clipping them', () => {
  const cases = runSensitivity({ ...AUTHORED_BASELINE }, [
    { label: 'Lower growth', parameter: 'volumeGrowth', value: 0.2 },
    { label: 'Invalid burn', parameter: 'burnShare', value: 1.1 },
  ]);
  assert.equal(cases[0].error, null);
  assert.ok(cases[0].purchaseCoverage! < runModel({ ...AUTHORED_BASELINE }).summary.final.purchaseCoverage!);
  assert.equal(cases[1].purchaseCoverage, null);
  assert.ok(cases[1].error?.includes('between 0% and 100%'));
});

test('running and explaining reader scenarios never mutates the authored baseline', () => {
  const before = JSON.stringify(AUTHORED_BASELINE);
  const baseline = runModel({ ...AUTHORED_BASELINE });
  const scenario = runModel({ ...AUTHORED_BASELINE, volumeGrowth: 0.2, priceMode: 'multiple' });
  const explanation = explainScenario(baseline, scenario);
  assert.ok(explanation.changes.some((change) => change.startsWith('Annual volume growth:')));
  assert.ok(explanation.findings.some((finding) => finding.includes('constant total-valuation/revenue multiple')));
  assert.equal(JSON.stringify(AUTHORED_BASELINE), before);
  close(baseline.rows[4].retainedRevenueUsd, 126.5625e6);
});
