import evidence from '../../../data/evidence/2026-11-02/baselines/observations.json' with { type: 'json' };
import historicalSupply from '../../../data/evidence/2026-11-02/baselines/supply.json' with { type: 'json' };
import { AUTHORED_BASELINE } from '../models/model.ts';
import { scenarioExamples } from '../models/inputs.ts';
import { DAY, WALLETS, POLICY_SOURCES, type FeedHealth, type LiveSnapshot } from './schema.ts';

export function fallbackSnapshot(): LiveSnapshot {
  const health = (): FeedHealth => ({ checkedAt: null, successAt: null, error: null });
  const snapshot: LiveSnapshot = {
    schemaVersion: 1, id: 'preserved-2026-10-28', generatedAt: '2026-10-28T07:26:47.281Z', refreshSeconds: 60,
    operating: { checkedAt: '2026-10-28T07:26:47.281Z', endExclusive: evidence.periods[1].endExclusive, daily: evidence.daily, periods: evidence.periods, longPeriods: null, nativeCoverage: evidence.nativeCoverage, supply: { windows: historicalSupply.windows, protocolVersions: [86] } },
    market: { priceUsd: evidence.seed.initialPriceUsd, asOf: evidence.seed.quoteTimestamp, source: 'https://coins.llama.fi/prices/current/coingecko:near' },
    chain: { asOf: evidence.seed.supplyDate, blockHeight: evidence.seed.supplyBlock, blockHash: '', supplyNear: evidence.seed.openingSupplyNear, issuanceRate: AUTHORED_BASELINE.issuanceRate, stakerIssuanceShare: AUTHORED_BASELINE.stakerIssuanceShare, protocolVersion: 86, wallets: [], frontendUsdc: null },
    review: { reviewedAt: '2026-10-28', sources: POLICY_SOURCES.map(source => ({ ...source, reviewedHash: null, currentHash: null, checkedAt: null, state: 'unreviewed' })) },
    health: { operating: health(), market: health(), chain: health(), documents: health() },
  };
  return structuredClone(snapshot);
}

export function validateSnapshot(value: unknown): asserts value is LiveSnapshot {
  if (!value || typeof value !== 'object') throw Error('Missing live snapshot');
  const s = value as LiveSnapshot;
  const finite = (n: unknown) => typeof n === 'number' && Number.isFinite(n) && n >= 0;
  const date = (d: unknown) => typeof d === 'string' && /^\d{4}-\d{2}-\d{2}(?:T.*)?$/.test(d) && Number.isFinite(Date.parse(d));
  if (s.schemaVersion !== 1 || typeof s.id !== 'string' || !date(s.generatedAt) || !finite(s.refreshSeconds) || s.refreshSeconds < 15 || s.refreshSeconds > 3600) throw Error('Incompatible live snapshot');
  if (!s.operating || !Array.isArray(s.operating.daily) || s.operating.periods?.length !== 2 || s.operating.supply?.windows?.length !== 2) throw Error('Incomplete evidence snapshot');
  if (!date(s.operating.endExclusive) || Date.parse(s.operating.endExclusive) > Math.floor(Date.parse(s.generatedAt) / DAY) * DAY || !date(s.operating.checkedAt)) throw Error('Daily data includes an unfinished UTC day');

  if (!finite(s.market?.priceUsd) || s.market.priceUsd <= 0 || !date(s.market.asOf) || !finite(s.chain?.supplyNear) || s.chain.supplyNear <= 0 || !finite(s.chain.issuanceRate) || s.chain.issuanceRate > 1 || !finite(s.chain.stakerIssuanceShare) || s.chain.stakerIssuanceShare > 1 || !date(s.chain.asOf)) throw Error('Invalid market or protocol observation');
  if (!Number.isSafeInteger(s.chain.blockHeight) || s.chain.blockHeight <= 0 || !Array.isArray(s.chain.wallets) || s.chain.wallets.some(wallet => !WALLETS.includes(wallet.account as typeof WALLETS[number]) || !finite(wallet.nativeNear) || typeof wallet.codeHash !== 'string') || new Set(s.chain.wallets.map(wallet => wallet.account)).size !== s.chain.wallets.length || (s.chain.frontendUsdc !== null && !finite(s.chain.frontendUsdc))) throw Error('Invalid wallet inventory');
  const { activeValidatorStakeNear, validatorEpochId, validatorEpochStartHeight } = s.chain;
  if (activeValidatorStakeNear !== undefined || validatorEpochId !== undefined || validatorEpochStartHeight !== undefined) {
    if (!finite(activeValidatorStakeNear) || activeValidatorStakeNear! <= 0 || activeValidatorStakeNear! > s.chain.supplyNear ||
      typeof validatorEpochId !== 'string' || !/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(validatorEpochId) ||
      !Number.isSafeInteger(validatorEpochStartHeight) || validatorEpochStartHeight! <= 0 || validatorEpochStartHeight! > s.chain.blockHeight) throw Error('Invalid active validator stake for block epoch');
  }
  if ([s.market.asOf, s.chain.asOf, s.operating.checkedAt].some(value => Date.parse(value) > Date.parse(s.generatedAt) + 5 * 60_000)) throw Error('Observation timestamp exceeds the snapshot time');
  let last = '';
  for (const day of s.operating.daily) {
    if (!date(day.date) || day.date <= last || day.date >= s.operating.endExclusive || !['volumeUsd','grossFeesUsd','collectionsUsd','nativeReceiptsNear'].every(key => (day as unknown as Record<string, unknown>)[key] === null || finite((day as unknown as Record<string, unknown>)[key]))) throw Error('Invalid daily observations');
    last = day.date;
  }
  for (const [index, period] of s.operating.periods.entries()) {
    if (!date(period.start) || !date(period.endExclusive) || Date.parse(period.endExclusive) - Date.parse(period.start) !== 30 * DAY || period.days !== 30) throw Error('Unmatched comparison period');
    const days = s.operating.daily.filter(day => day.date >= period.start && day.date < period.endExclusive);
    if (days.length !== 30) throw Error('Missing comparison days');
    for (const key of ['volumeUsd','grossFeesUsd','collectionsUsd','nativeReceiptsNear'] as const) {
      if (!finite(period[key]) || days.some(day => day[key] === null)) throw Error('Missing comparison values');
      const sum = days.reduce((total, day) => total + day[key]!, 0);
      if (Math.abs(sum - period[key]) > Math.max(1e-6, Math.abs(sum) * 1e-10)) throw Error('Comparison does not reconcile');
    }
    const w = s.operating.supply.windows[index];
    if (w.startInclusiveUTC.slice(0,10) !== period.start || w.endExclusiveUTC.slice(0,10) !== period.endExclusive || BigInt(w.closingSupplyYocto) - BigInt(w.openingSupplyYocto) !== BigInt(w.netSupplyChangeYocto) || BigInt(w.grossMintedYocto) - BigInt(w.nativeBurnDerivedYocto) !== BigInt(w.netSupplyChangeYocto) || BigInt(w.nativeBurnDerivedYocto) < 0n) throw Error('Supply evidence does not reconcile');
    if (Math.abs(Number(w.externalNativeWalletReceiptsNEAR) - period.nativeReceiptsNear) > 1e-6) throw Error('Native receipts differ between exhibits');
    for (const [decimal, integer] of [['grossMintedNEAR','grossMintedYocto'],['netSupplyChangeNEAR','netSupplyChangeYocto'],['nativeBurnDerivedNEAR','nativeBurnDerivedYocto']] as const) {
      if (!Number.isFinite(Number(w[decimal])) || Math.abs(Number(w[decimal]) - Number(w[integer]) / 1e24) > 1e-6) throw Error('Displayed native amount differs from the exact ledger');
    }
    const gross = Number(w.grossMintedYocto) / 1e24;
    if (gross === 0 ? w.receiptQuantityToGrossMintingPct !== null : w.receiptQuantityToGrossMintingPct === null || !Number.isFinite(Number(w.receiptQuantityToGrossMintingPct)) || Math.abs(Number(w.receiptQuantityToGrossMintingPct) - period.nativeReceiptsNear / gross * 100) > 1e-8) throw Error('Receipt coverage does not reconcile');
    if (!Number.isSafeInteger(w.payoutCount) || w.payoutCount < 0 || !Number.isSafeInteger(w.openingBlock) || !Number.isSafeInteger(w.closingBlock) || w.closingBlock <= w.openingBlock) throw Error('Invalid supply provenance');

  }
  for (const key of ['operating','market','chain','documents'] as const) {
    const health = s.health?.[key];
    if (!health || (health.checkedAt !== null && !date(health.checkedAt)) || (health.successAt !== null && !date(health.successAt)) || (health.error !== null && typeof health.error !== 'string')) throw Error('Missing source health');
  }
  if (!date(s.operating.nativeCoverage?.start) || !date(s.operating.nativeCoverage.end) || s.operating.nativeCoverage.start > s.operating.periods[0].start || Date.parse(s.operating.nativeCoverage.end) + DAY !== Date.parse(s.operating.endExclusive)) throw Error('Native coverage does not match the comparison');
  if (s.operating.reporting) {
    const r = s.operating.reporting;
    if (!Number.isInteger(r.bufferDays) || r.bufferDays !== 2 || !date(r.latestAvailableDate) || r.latestAvailableDate >= new Date(Math.floor(Date.parse(s.generatedAt) / DAY) * DAY).toISOString().slice(0,10) || !Array.isArray(r.revisedDates) || new Set(r.revisedDates).size !== r.revisedDates.length || r.revisedDates.some(d => !date(d) || d >= s.operating.endExclusive) || Date.parse(s.operating.endExclusive) > Math.floor(Date.parse(s.operating.checkedAt) / DAY) * DAY - r.bufferDays * DAY) throw Error('Invalid reporting buffer or revisions');
  }
  if (s.operating.longPeriods !== null) {
    if (s.operating.longPeriods.length !== 2) throw Error('Incomplete long comparison');
    s.operating.longPeriods.forEach((period, i) => {
      const expectedEnd = Date.parse(s.operating.endExclusive) - (1 - i) * 90 * DAY;
      if (period.days !== 90 || Date.parse(period.endExclusive) !== expectedEnd || Date.parse(period.start) !== expectedEnd - 90 * DAY) throw Error('Unmatched long comparison');
      const rows = s.operating.daily.filter(day => day.date >= period.start && day.date < period.endExclusive);
      if (rows.length !== 90) throw Error('Missing long comparison days');
      for (const key of ['volumeUsd','grossFeesUsd','collectionsUsd'] as const) if (!finite(period[key]) || rows.some(day => day[key] === null) || Math.abs(rows.reduce((sum,day) => sum + day[key]!,0) - period[key]) > Math.max(1e-6, period[key] * 1e-10)) throw Error('Long comparison does not reconcile');
    });
  }
  scenarioExamples(s);
  if (s.operating.periods[0].endExclusive !== s.operating.periods[1].start || s.operating.periods[1].endExclusive !== s.operating.endExclusive || !s.health || !s.review || !Array.isArray(s.review.sources)) throw Error('Snapshot dates or health are incomplete');
}

export function reviewCurrent(s: LiveSnapshot, ids: string[]): boolean { return ids.every(id => s.review.sources.some(source => source.id === id && source.state === 'unchanged')); }
