import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import App from '../src/app.tsx';
import { AUTHORED_BASELINE, runModel, type ModelAssumptions } from '../../compute/src/models/model.ts';
import { RESEARCH_BASELINE } from '../../compute/src/models/baseline.ts';
import { SCENARIO_VERSION, decodeScenario, encodeScenario, exportScenario, parseAssumptionCommand, scenarioCsv } from '../../compute/src/models/scenario.ts';

const root = new URL('../../', import.meta.url);
const read = (path: string) => readFileSync(fileURLToPath(new URL(path, root)), 'utf8');
const json = <T>(path: string): T => JSON.parse(read(path)) as T;

interface DailyObservation {
  date: string;
  volumeUsd: number | null;
  grossFeesUsd: number | null;
  collectionsUsd: number | null;
  nativeReceiptsNear: number | null;
  priceUsd: number | null;
}
interface Period {
  start: string;
  endExclusive: string;
  days: number;
  volumeUsd: number;
  grossFeesUsd: number;
  collectionsUsd: number;
  nativeReceiptsNear: number;
  wallets: { account: string; amountNear: number; receiptCount: number }[];
}
interface Evidence {
  version: string;
  cleanCutoff: string;
  daily: DailyObservation[];
  periods: Period[];
  nativeCoverage: { start: string; end: string; internalTransfersExcludedNear: number; externalReceiptCount: number };
  seed: { dailyVolumeUsd: number; openingSupplyNear: number; initialPriceUsd: number };
}

const evidence = json<Evidence>('data/evidence/2026-11-02/baselines/observations.json');
const wire = (payload: unknown) => btoa(JSON.stringify(payload)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const near = (actual: number, expected: number, absoluteTolerance = 1e-7) => assert.ok(Math.abs(actual - expected) <= absoluteTolerance, `${actual} differs from ${expected}`);

function csv(text: string): Record<string, string>[] {
  const lines = text.trimEnd().split(/\r?\n/);
  const split = (line: string) => {
    const cells: string[] = [];
    let value = '', quoted = false;
    for (let i = 0; i < line.length; i += 1) {
      if (line[i] === '"') {
        if (quoted && line[i + 1] === '"') { value += '"'; i += 1; }
        else quoted = !quoted;
      } else if (line[i] === ',' && !quoted) { cells.push(value); value = ''; }
      else value += line[i];
    }
    cells.push(value);
    return cells;
  };
  const keys = split(lines[0]);
  return lines.slice(1).filter(Boolean).map((line) => Object.fromEntries(split(line).map((value, i) => [keys[i], value])));
}

/** Preserve the CSV's decimal strings, including scientific notation, before summing. */
function nativeUnits(text: string): bigint {
  const match = text.trim().match(/^([+-]?)(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);
  assert.ok(match, `Not a decimal native quantity: ${text}`);
  const fraction = match[3] ?? '';
  const exponent = Number(match[4] ?? 0);
  const shift = 24 + exponent - fraction.length;
  const magnitude = BigInt(match[2] + fraction);
  assert.ok(shift >= 0, 'Published CSV amount must be representable in yoctoNEAR.');
  return (match[1] === '-' ? -1n : 1n) * magnitude * 10n ** BigInt(shift);
}

test('a shared scenario round-trips every supported assumption and no evidence', () => {
  const scenario: ModelAssumptions = { ...RESEARCH_BASELINE, volumeGrowth: 0.2, retainedFeeBps: 3.1, annualCashCostsUsd: 4.5e6, priceMode: 'multiple', revenueMultiple: 160, burnShare: 0.3, stakeShare: 0.2, issuanceRate: 0.016 };
  const encoded = encodeScenario(scenario);
  assert.ok(!/[+/=]/.test(encoded), 'Share payload is URL-safe.');
  const restored = decodeScenario(encoded, { ...RESEARCH_BASELINE });
  assert.deepEqual(restored, scenario);
  assert.equal(Object.hasOwn(restored!, 'daily'), false);
  assert.equal(Object.hasOwn(restored!, 'periods'), false);
});

test('malformed, oversized, incompatible and non-finite scenarios are rejected', () => {
  assert.equal(decodeScenario('!not base64!', { ...RESEARCH_BASELINE }), null);
  assert.equal(decodeScenario(btoa('{'), { ...RESEARCH_BASELINE }), null);
  assert.equal(decodeScenario('a'.repeat(12001), { ...RESEARCH_BASELINE }), null);
  assert.equal(decodeScenario(wire({ version: 'old-model', assumptions: { ...RESEARCH_BASELINE } }), { ...RESEARCH_BASELINE }), null);
  assert.equal(decodeScenario(wire({ version: SCENARIO_VERSION, assumptions: { ...RESEARCH_BASELINE, initialPriceUsd: Number.NaN } }), { ...RESEARCH_BASELINE }), null);
  const overflow = JSON.stringify({ version: SCENARIO_VERSION, assumptions: { ...RESEARCH_BASELINE } }).replace(/"dailyVolumeUsd":[^,}]+/, '"dailyVolumeUsd":1e9999');
  assert.equal(decodeScenario(btoa(overflow), { ...RESEARCH_BASELINE }), null);
  assert.equal(decodeScenario(wire({ version: SCENARIO_VERSION, assumptions: { volumeGrowth: 0.2 } }), { ...RESEARCH_BASELINE }), null);
});

test('unrecognized share-payload keys cannot replace the article, evidence or prototype', () => {
  const payload = { version: SCENARIO_VERSION, assumptions: { ...RESEARCH_BASELINE, fakeRevenue: 99e9, thesis: 'replacement', ...JSON.parse('{"__proto__":{"polluted":true}}') }, daily: [], article: 'replacement' };
  const restored = decodeScenario(wire(payload), { ...RESEARCH_BASELINE });
  assert.deepEqual(restored, { ...RESEARCH_BASELINE });
  assert.deepEqual(Object.keys(restored!).sort(), Object.keys(RESEARCH_BASELINE).sort());
  assert.equal(Object.getPrototypeOf(restored), Object.prototype);
  assert.equal(Object.hasOwn(Object.prototype, 'polluted'), false);
});

test('supported text commands resolve one named assumption with explicit units', () => {
  assert.deepEqual(parseAssumptionCommand('Intents growth 20%')?.patch, { volumeGrowth: 0.2 });
  assert.deepEqual(parseAssumptionCommand('Assume Intents grows by 20% annually instead of 50%.')?.patch, { volumeGrowth: 0.2 });
  assert.deepEqual(parseAssumptionCommand('price $10')?.patch, { initialPriceUsd: 10, priceMode: 'fixed' });
  assert.deepEqual(parseAssumptionCommand('retained fees 3 bps')?.patch, { retainedFeeBps: 3 });
  assert.deepEqual(parseAssumptionCommand('retained 3 bps')?.patch, { retainedFeeBps: 3 });
  assert.deepEqual(parseAssumptionCommand('issuance 1.6%')?.patch, { issuanceRate: 0.016 });
  assert.deepEqual(parseAssumptionCommand('burn 30%')?.patch, { burnShare: 0.3, stakeShare: 0 });
});

test('research questions and multi-command text never silently mutate one parameter', () => {
  const unsupported = [
    'Which competitors could reduce NEAR margins?',
    'Why is price $10 fair?',
    'Research whether Intents growth 20% is plausible',
    'Is issuance 1.6% safe for validators?',
    'Can Intents growth 20% support a higher valuation?',
    'Intents growth 20% and price $10',
    'Intents growth 20% and Intents growth 50%',
    'Intents growth 20% and retained fees 3 bps',
    'revenue growth 20%',
    'growth 20',
  ];
  unsupported.forEach((text) => assert.equal(parseAssumptionCommand(text), null, `Must reject: ${text}`));
});

test('the historical chart bundle preserves its cutoff and explicit observations', () => {
  assert.equal(evidence.cleanCutoff, '2026-10-21');
  assert.equal(evidence.daily.length, 294);
  assert.equal(new Set(evidence.daily.map((day) => day.date)).size, evidence.daily.length);
  assert.deepEqual(evidence.daily.map((day) => day.date), evidence.daily.map((day) => day.date).sort());
  assert.ok(evidence.daily.every((day) => day.date <= evidence.cleanCutoff));
});

test('the two 30-day claims preserve independently stated totals and UTC boundaries', () => {
  const expected = [
    { start: '2026-08-23', endExclusive: '2026-09-22', volume: 4_260_147_656, fees: 6_193_942, collections: 1_373_136, native: 549_851.5511058077 },
    { start: '2026-09-22', endExclusive: '2026-10-22', volume: 4_350_995_030, fees: 7_180_292, collections: 2_282_005, native: 462_176.19972287747 },
  ];
  assert.equal(evidence.periods.length, expected.length);
  expected.forEach((reference, index) => {
    const period = evidence.periods[index];
    assert.equal(period.start, reference.start);
    assert.equal(period.endExclusive, reference.endExclusive);
    assert.equal(period.days, 30);
    const rows = evidence.daily.filter((day) => reference.start <= day.date && day.date < reference.endExclusive);
    assert.equal(rows.length, 30);
    assert.equal(period.volumeUsd, reference.volume);
    assert.equal(period.grossFeesUsd, reference.fees);
    assert.equal(period.collectionsUsd, reference.collections);
    near(period.nativeReceiptsNear, reference.native);
    assert.equal(rows.reduce((sum, day) => sum + (day.volumeUsd ?? 0), 0), reference.volume);
    assert.equal(rows.reduce((sum, day) => sum + (day.grossFeesUsd ?? 0), 0), reference.fees);
    assert.equal(rows.reduce((sum, day) => sum + (day.collectionsUsd ?? 0), 0), reference.collections);
    near(rows.reduce((sum, day) => sum + (day.nativeReceiptsNear ?? 0), 0), reference.native);
  });
  assert.ok(evidence.periods[1].collectionsUsd > evidence.periods[0].collectionsUsd);
  assert.ok(evidence.periods[1].nativeReceiptsNear < evidence.periods[0].nativeReceiptsNear);
});

test('actual daily native observations independently reconcile to deduplicated receipt decimals', () => {
  const receipts = csv(read('web/public/data/history/2026-10-28/native-receipts.csv'));
  const external = receipts.filter((receipt) => receipt.classification === 'external_native_inflow');
  assert.equal(external.length, 2375);
  assert.equal(new Set(external.map((receipt) => receipt.receipt_id)).size, external.length);
  assert.equal(evidence.nativeCoverage.externalReceiptCount, external.length);
  const byDay = new Map<string, bigint>();
  for (const receipt of external) {
    const date = receipt.utc.slice(0, 10);
    byDay.set(date, (byDay.get(date) ?? 0n) + nativeUnits(receipt.amount_near));
  }
  for (const day of evidence.daily.filter((item) => item.date >= evidence.nativeCoverage.start)) {
    assert.notEqual(day.nativeReceiptsNear, null);
    near(day.nativeReceiptsNear!, Number(byDay.get(day.date) ?? 0n) / 1e24);
  }
  const internal = receipts.filter((receipt) => receipt.classification === 'internal_native_transfer');
  assert.equal(internal.length, 2);
  assert.equal(internal.reduce((sum, receipt) => sum + nativeUnits(receipt.amount_near), 0n), 502_794n * 10n ** 24n);
  assert.equal(evidence.nativeCoverage.internalTransfersExcludedNear, 502_794);
});

test('dates before reconstructed native coverage remain null, not synthetic zero observations', () => {
  assert.equal(evidence.nativeCoverage.start, '2026-08-23');
  assert.equal(evidence.nativeCoverage.end, '2026-10-21');
  const unmeasured = evidence.daily.filter((day) => day.date < '2026-08-23');
  assert.ok(unmeasured.length > 200);
  assert.ok(unmeasured.every((day) => day.nativeReceiptsNear === null));
  assert.ok(evidence.daily.filter((day) => day.date >= '2026-08-23').every((day) => day.nativeReceiptsNear !== null));
});

test('downloaded daily CSV contains exactly the plotted dates, quantities and missingness', () => {
  const rows = csv(read('web/public/data/history/2026-10-28/daily-observations.csv'));
  const names: Record<keyof DailyObservation, string[]> = {
    date: ['date', 'date_utc'],
    volumeUsd: ['volumeUsd', 'volume_usd'],
    grossFeesUsd: ['grossFeesUsd', 'gross_fees_usd'],
    collectionsUsd: ['collectionsUsd', 'captured_receipts_usd'],
    nativeReceiptsNear: ['nativeReceiptsNear', 'native_receipts_near'],
    priceUsd: ['priceUsd', 'near_price_start_utc'],
  };
  assert.equal(rows.length, evidence.daily.length, 'CSV must not append the excluded partial October 22 observation.');
  const headers = Object.keys(rows[0]);
  const column = (field: keyof DailyObservation) => {
    const header = names[field].find((name) => headers.includes(name));
    assert.ok(header, `Missing plotted field ${field}; price-normalized estimates are not a substitute for actual receipts.`);
    return header;
  };
  const fields = Object.keys(names) as (keyof DailyObservation)[];
  rows.forEach((row, index) => {
    const plotted = evidence.daily[index];
    fields.forEach((field) => {
      const text = row[column(field)];
      if (field === 'date') assert.equal(text, plotted.date);
      else if (plotted[field] === null) assert.equal(text, '', `Missing ${field} on ${plotted.date} must stay blank.`);
      else assert.equal(Number(text), plotted[field], `${field} on ${plotted.date} differs from the chart.`);
    });
  });
});

interface SupplyWindow {
  startInclusiveUTC: string;
  endExclusiveUTC: string;
  openingBlock: number;
  closingBlock: number;
  openingSupplyYocto: string;
  closingSupplyYocto: string;
  payoutCount: number;
  grossMintedYocto: string;
  netSupplyChangeYocto: string;
  nativeBurnDerivedYocto: string;
  externalNativeWalletReceiptsNEAR: string;
  receiptQuantityToGrossMintingPct: string;
  supplyIdentityResidualYocto: string;
}

test('both matched supply windows use complete payout coverage and exact native accounting', () => {
  const source = json<{ windows: SupplyWindow[] }>('data/evidence/2026-11-02/inputs/supply-windows.json');
  const supply = json<{ windows: SupplyWindow[]; audit: { payoutRowsReplayed: number } }>('data/evidence/2026-11-02/baselines/supply.json');
  assert.deepEqual(supply.windows, source.windows, 'The inline table must use the preserved matched-window results.');
  assert.equal(supply.windows.length, 2);
  assert.equal(supply.audit.payoutRowsReplayed, 220);
  assert.equal(supply.windows.reduce((sum, row) => sum + row.payoutCount, 0), 197);
  const boundaries = [
    ['2026-08-23T00:00:00Z', '2026-09-22T00:00:00Z', 97],
    ['2026-09-22T00:00:00Z', '2026-10-22T00:00:00Z', 100],
  ] as const;
  supply.windows.forEach((window, index) => {
    assert.equal(window.startInclusiveUTC, boundaries[index][0]);
    assert.equal(window.endExclusiveUTC, boundaries[index][1]);
    assert.equal(window.payoutCount, boundaries[index][2]);
    const gross = BigInt(window.grossMintedYocto);
    const net = BigInt(window.closingSupplyYocto) - BigInt(window.openingSupplyYocto);
    const burn = BigInt(window.nativeBurnDerivedYocto);
    assert.equal(net, BigInt(window.netSupplyChangeYocto));
    assert.equal(gross - burn, net);
    assert.equal(window.supplyIdentityResidualYocto, '0');
    assert.ok(gross > net && burn > 0n);
    near(Number(window.externalNativeWalletReceiptsNEAR), evidence.periods[index].nativeReceiptsNear);
    near(Number(nativeUnits(window.externalNativeWalletReceiptsNEAR)) / Number(gross) * 100, Number(window.receiptQuantityToGrossMintingPct), 1e-12);
  });
  assert.equal(supply.windows[0].closingBlock, supply.windows[1].openingBlock);
  assert.equal(supply.windows[0].closingSupplyYocto, supply.windows[1].openingSupplyYocto);
  assert.ok(Number(supply.windows[1].receiptQuantityToGrossMintingPct) < Number(supply.windows[0].receiptQuantityToGrossMintingPct));
});

test('expanded withdrawal evidence preserves its sample and provenance limits', () => {
  const review = json<{
    collectorDiscovery: { originalMatchedWithdrawalCount: number; expandedMatchedWithdrawalCount: number; newVerifiedConfiguredCollectorCount: number; sampleSize: number };
    signatureVerification: { officialTestVectorPassed: boolean; indexerSignatureResults: { transactionHash: string; nep413SignatureVerified: boolean; implicitAccountMatchesPublicKey: boolean }[]; results: { transactionHash: string; nep413SignatureVerified: boolean; tamperedMessageRejected: boolean }[] };
  }>('data/evidence/2026-11-02/inputs/conversion-review.json');
  assert.equal(review.collectorDiscovery.originalMatchedWithdrawalCount, 702);
  assert.equal(review.collectorDiscovery.expandedMatchedWithdrawalCount, 703);
  assert.equal(review.collectorDiscovery.newVerifiedConfiguredCollectorCount, 1);
  assert.equal(review.collectorDiscovery.sampleSize, 17);
  assert.equal(review.signatureVerification.officialTestVectorPassed, true);
  const signed = review.signatureVerification.indexerSignatureResults;
  assert.equal(signed.length, 17);
  assert.equal(new Set(signed.map(row => row.transactionHash)).size, signed.length);
  signed.forEach(row => {
    assert.equal(row.nep413SignatureVerified, true);
    assert.equal(row.implicitAccountMatchesPublicKey, true);
  });
  assert.equal(review.signatureVerification.results.length, 2, 'Only two samples have the additional source-native block checks.');
  review.signatureVerification.results.forEach(row => {
    assert.equal(row.nep413SignatureVerified, true);
    assert.equal(row.tamperedMessageRejected, true);
    assert.ok(signed.some(sample => sample.transactionHash === row.transactionHash));
  });
});

test('scenario exports preserve assumptions, data cutoff, model version and undefined coverage', () => {
  const result = runModel({ ...RESEARCH_BASELINE, issuanceRate: 0, burnShare: 0 });
  const exported = JSON.parse(exportScenario(result, evidence.version, evidence.cleanCutoff));
  assert.equal(exported.modelVersion, SCENARIO_VERSION);
  assert.equal(exported.evidenceVersion, evidence.version);
  assert.equal(exported.historicalCutoff, '2026-10-21');
  assert.deepEqual(exported.assumptions, result.assumptions);
  assert.equal(exported.rows[0].purchaseCoverage, null);
  assert.equal(exported.rows[0].burnCoverage, null);
  assert.ok(!JSON.stringify(exported).includes('Infinity'));
  const rows = csv(scenarioCsv(result));
  assert.equal(rows.length, result.rows.length);
  assert.equal(rows[0].purchaseCoverage, '');
  assert.equal(rows[0].burnCoverage, '');
});

test('reader models and restored scenarios cannot mutate the authored baseline or frozen evidence', () => {
  const baselineBefore = JSON.stringify(RESEARCH_BASELINE);
  const teachingBefore = JSON.stringify(AUTHORED_BASELINE);
  const evidenceBefore = read('data/evidence/2026-11-02/baselines/observations.json');
  assert.ok(Object.isFrozen(RESEARCH_BASELINE));
  assert.ok(Object.isFrozen(AUTHORED_BASELINE));
  const restored = decodeScenario(encodeScenario({ ...RESEARCH_BASELINE, volumeGrowth: 0.2, burnShare: 0.3, issuanceRate: 0.016 }), { ...RESEARCH_BASELINE });
  assert.ok(restored);
  runModel(restored);
  assert.equal(JSON.stringify(RESEARCH_BASELINE), baselineBefore);
  assert.equal(JSON.stringify(AUTHORED_BASELINE), teachingBefore);
  assert.equal(read('data/evidence/2026-11-02/baselines/observations.json'), evidenceBefore);
});

test('legacy saved scenarios cannot change the page after the calculator is removed', () => {
  const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'window');
  const storageDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  let storageReads = 0;
  const altered: ModelAssumptions = { ...RESEARCH_BASELINE, volumeGrowth: 0.2, priceMode: 'multiple', retainedFeeBps: 3, burnShare: 0.3 };
  const render = (query: ModelAssumptions | null, saved: ModelAssumptions | null) => {
    Object.defineProperty(globalThis, 'window', { configurable: true, value: { location: { search: query ? `?scenario=${encodeScenario(query)}` : '' } } });
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
      getItem: () => { storageReads += 1; return saved ? encodeScenario(saved) : null; },
    } });
    return renderToStaticMarkup(createElement(App));
  };
  try {
    const original = render(null, null);
    assert.ok(original.includes('class="authored-thesis"'), 'The authored investment statement must render.');
    assert.equal(render(altered, null), original, 'An old shared URL must not silently change fixed article figures.');
    assert.equal(render(null, altered), original, 'An old locally saved scenario must not silently change fixed article figures.');
    assert.equal(render(altered, altered), original);
    assert.equal(storageReads, 0, 'The article must not consult saved calculator state.');
    assert.doesNotMatch(original, /<input\b|aria-label="(?:Reader scenario controls|Scenario interpretation|Copy scenario link|Download scenario JSON)"/i);
    assert.doesNotMatch(original, /Test the assumptions|Reset baseline|Edit assumptions|Change one assumption in words|Scenario JSON|Annual results CSV|Your purchases|Your native burns/);
  } finally {
    if (windowDescriptor) Object.defineProperty(globalThis, 'window', windowDescriptor);
    else Reflect.deleteProperty(globalThis, 'window');
    if (storageDescriptor) Object.defineProperty(globalThis, 'localStorage', storageDescriptor);
    else Reflect.deleteProperty(globalThis, 'localStorage');
  }
});
