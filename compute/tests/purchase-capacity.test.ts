import assert from 'node:assert/strict';
import test from 'node:test';
import { DAY } from '../src/live/schema.ts';
import { fallbackSnapshot } from '../src/live/snapshot.ts';
import { purchaseCapacityByWindow } from '../src/models/inputs.ts';

function observedYear() {
  const s = fallbackSnapshot();
  const start = Date.parse(s.operating.endExclusive) - 365 * DAY;
  s.operating.daily = Array.from({length: 365}, (_, i) => ({
    date: new Date(start + i * DAY).toISOString().slice(0, 10),
    volumeUsd: i < 335 ? 1_000_000 : 2_000_000,
    grossFeesUsd: null, collectionsUsd: null, nativeReceiptsNear: null, priceUsd: null,
  }));
  s.operating.periods[1].volumeUsd = 60_000_000;
  s.market.priceUsd = 5;
  s.chain.supplyNear = 100_000_000;
  s.chain.issuanceRate = .02;
  return s;
}

test('purchase capacity changes only the volume basis, using a complete 365-day settlement record', () => {
  const {recent, trailing} = purchaseCapacityByWindow(observedYear());
  assert.ok(trailing);
  assert.equal(recent.annualVolumeUsd, 730_000_000);
  assert.equal(trailing.annualVolumeUsd, 395_000_000);
  assert.equal(trailing.issuedNear, recent.issuedNear);
  assert.equal(trailing.acquiredNear, 39_500);
  assert.equal(trailing.purchaseCoverage, 39_500 / 2_000_000);
  assert.equal(recent.purchaseCoverage, 73_000 / 2_000_000);
});

test('a missing day or value suppresses the trailing calculation while an observed zero counts', () => {
  const s = observedYear();
  s.operating.daily[0].volumeUsd = 0;
  assert.equal(purchaseCapacityByWindow(s).trailing?.annualVolumeUsd, 394_000_000);
  s.operating.daily[0].volumeUsd = null;
  assert.equal(purchaseCapacityByWindow(s).trailing, null);
  s.operating.daily.shift();
  const {recent, trailing} = purchaseCapacityByWindow(s);
  assert.equal(trailing, null);
  assert.equal(recent.acquiredNear, 73_000);
});
