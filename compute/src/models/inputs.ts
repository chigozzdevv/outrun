import { AUTHORED_BASELINE, runForwardModel, type ModelAssumptions } from './model.ts';
import { currentEconomics, type UnderwritingInputs } from './underwriting.ts';
import { DAY, type LiveSnapshot } from '../live/schema.ts';

export function scenarioAssumptions(s: LiveSnapshot, patch: Partial<ModelAssumptions> = {}): ModelAssumptions {
  const stakingRatio = s.chain.activeValidatorStakeNear === undefined ? AUTHORED_BASELINE.stakingRatio : s.chain.activeValidatorStakeNear / s.chain.supplyNear;
  return { ...AUTHORED_BASELINE, dailyVolumeUsd: s.operating.periods[1].volumeUsd / 30, openingSupplyNear: s.chain.supplyNear, initialPriceUsd: s.market.priceUsd, issuanceRate: s.chain.issuanceRate, stakerIssuanceShare: s.chain.stakerIssuanceShare, stakingRatio, ...patch };
}

export function underwritingInputs(s: LiveSnapshot): UnderwritingInputs {
  const a = scenarioAssumptions(s);
  return { dailyVolumeUsd: a.dailyVolumeUsd, retainedFeeBps: a.retainedFeeBps, priceUsd: a.initialPriceUsd, supplyNear: a.openingSupplyNear, issuanceRate: a.issuanceRate, cashAvailability: 1, purchaseAllocation: 1 };
}

export function trailingYearVolume(s: LiveSnapshot): number | null {
  const end = Date.parse(s.operating.endExclusive);
  const start = end - 365 * DAY;
  const startDate = new Date(start).toISOString().slice(0, 10);
  const endDate = new Date(end).toISOString().slice(0, 10);
  const days = s.operating.daily.filter(day => day.date >= startDate && day.date < endDate);
  if (days.length !== 365) return null;
  let volume = 0;
  for (const [i, day] of days.entries()) {
    if (day.date !== new Date(start + i * DAY).toISOString().slice(0, 10) || day.volumeUsd === null) return null;
    volume += day.volumeUsd;
  }
  return volume;
}

export function purchaseCapacityByWindow(s: LiveSnapshot) {
  const inputs = underwritingInputs(s);
  const actualVolume = trailingYearVolume(s);
  const atVolume = (annualVolumeUsd: number) => ({
    annualVolumeUsd,
    ...currentEconomics({ ...inputs, dailyVolumeUsd: annualVolumeUsd / 365 }),
  });
  return {
    recent: atVolume(inputs.dailyVolumeUsd * 365),
    trailing: actualVolume === null ? null : atVolume(actualVolume),
  };
}

export function scenarioExamples(s: LiveSnapshot) {
  return [
    { id: 'fixed50', label: '50% volume growth · fixed price', patch: {} },
    { id: 'fixed20', label: '20% volume growth · fixed price', patch: { volumeGrowth: .2 } },
    { id: 'both50', label: '50% volume growth · 50% price growth', patch: { priceMode: 'growth', priceGrowth: .5 } },
    { id: 'multiple200', label: '50% volume growth · 200× revenue', patch: { priceMode: 'multiple', revenueMultiple: 200 } },
  ].map(row => {
    const assumptions = scenarioAssumptions(s, row.patch as Partial<ModelAssumptions>);
    return { id: row.id, label: row.label, model: assumptions.priceMode === 'multiple' && assumptions.dailyVolumeUsd === 0 ? null : runForwardModel(assumptions) };
  });
}
