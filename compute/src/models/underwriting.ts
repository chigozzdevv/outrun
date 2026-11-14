/** Conditional economics, with total supply (not circulating supply) in capitalization. */
export interface UnderwritingInputs {
  dailyVolumeUsd: number;
  retainedFeeBps: number;
  priceUsd: number;
  supplyNear: number;
  issuanceRate: number;
  cashAvailability: number;
  purchaseAllocation: number;
}

function validate(a: UnderwritingInputs) {
  if (Object.values(a).some(value => !Number.isFinite(value)) || a.dailyVolumeUsd < 0 || a.retainedFeeBps < 0 || a.retainedFeeBps > 10_000 || a.priceUsd <= 0 || a.supplyNear <= 0 || [a.issuanceRate, a.cashAvailability, a.purchaseAllocation].some(value => value < 0 || value > 1)) throw Error('Invalid underwriting inputs');
}

export function currentEconomics(a: UnderwritingInputs) {
  validate(a);
  const revenueUsd = 365 * a.dailyVolumeUsd * a.retainedFeeBps / 10_000;
  const capitalizationUsd = a.priceUsd * a.supplyNear;
  const availableCashUsd = revenueUsd * a.cashAvailability;
  const acquisitionBudgetUsd = availableCashUsd * a.purchaseAllocation;
  const acquiredNear = acquisitionBudgetUsd / a.priceUsd;
  const issuedNear = a.supplyNear * a.issuanceRate;
  const effectiveRetainedRate = a.retainedFeeBps / 10_000 * a.cashAvailability * a.purchaseAllocation;
  const requiredDailyVolumeUsd = issuedNear === 0 ? 0 : effectiveRetainedRate > 0 ? issuedNear * a.priceUsd / (365 * effectiveRetainedRate) : null;
  const result = {
    revenueUsd, capitalizationUsd, availableCashUsd, acquisitionBudgetUsd, acquiredNear, issuedNear,
    revenueMultiple: revenueUsd > 0 ? capitalizationUsd / revenueUsd : null,
    purchaseCoverage: issuedNear > 0 ? acquiredNear / issuedNear : null,
    requiredDailyVolumeUsd,
    volumeHurdle: requiredDailyVolumeUsd !== null && a.dailyVolumeUsd > 0 ? requiredDailyVolumeUsd / a.dailyVolumeUsd : null,
  };
  if (Object.values(result).some(value => value !== null && !Number.isFinite(value))) throw Error('Current economics exceed numerical limits');
  return result;
}

export interface TerminalInputs extends UnderwritingInputs {
  years: number;
  volumeGrowth: number;
  /** Compound annual change in the retained rate; route mix and sharing can change it. */
  retainedRateGrowth: number;
  terminalMultiple: number;
}

/** Endpoint valuation: T full years, T growth steps, and T supply expansions. */
export function terminalEconomics(a: TerminalInputs) {
  validate(a);
  if (!Number.isInteger(a.years) || a.years < 1 || a.years > 30 || !Number.isFinite(a.volumeGrowth) || a.volumeGrowth <= -1 || !Number.isFinite(a.retainedRateGrowth) || a.retainedRateGrowth <= -1 || !Number.isFinite(a.terminalMultiple) || a.terminalMultiple <= 0) throw Error('Invalid terminal assumptions');
  const dailyVolumeUsd = a.dailyVolumeUsd * (1 + a.volumeGrowth) ** a.years;
  const retainedFeeBps = a.retainedFeeBps * (1 + a.retainedRateGrowth) ** a.years;
  if (retainedFeeBps > 10_000) throw Error('Terminal retained fee rate exceeds volume');
  const revenueUsd = 365 * dailyVolumeUsd * retainedFeeBps / 10_000;
  // No burns are assumed in the return scenarios. Held or staked purchases do not reduce S.
  const supplyNear = a.supplyNear * (1 + a.issuanceRate) ** a.years;
  const capitalizationUsd = a.terminalMultiple * revenueUsd;
  const priceUsd = capitalizationUsd / supplyNear;
  const result = { dailyVolumeUsd, retainedFeeBps, revenueUsd, supplyNear, capitalizationUsd, priceUsd, priceRatio: priceUsd / a.priceUsd, priceReturn: priceUsd / a.priceUsd - 1 };
  if (Object.values(result).some(value => !Number.isFinite(value))) throw Error('Terminal scenario exceeds numerical limits');
  return result;
}

/** Invert the endpoint identity to solve the constant-rate volume CAGR for a target price ratio. */
export function requiredGrowth(a: UnderwritingInputs, years: number, terminalMultiple: number, targetPriceRatio: number) {
  validate(a);
  if (!Number.isInteger(years) || years < 1 || years > 30 || !Number.isFinite(terminalMultiple) || terminalMultiple <= 0 || !Number.isFinite(targetPriceRatio) || targetPriceRatio <= 0) throw Error('Invalid reverse-underwriting assumptions');
  if (a.dailyVolumeUsd === 0 || a.retainedFeeBps === 0) return null;
  const terminalSupplyNear = a.supplyNear * (1 + a.issuanceRate) ** years;
  const requiredRevenueUsd = targetPriceRatio * a.priceUsd * terminalSupplyNear / terminalMultiple;
  const requiredDailyVolumeUsd = requiredRevenueUsd / (365 * a.retainedFeeBps / 10_000);
  const growth = (requiredDailyVolumeUsd / a.dailyVolumeUsd) ** (1 / years) - 1;
  if (![terminalSupplyNear, requiredRevenueUsd, requiredDailyVolumeUsd, growth].every(Number.isFinite)) throw Error('Reverse underwriting exceeds numerical limits');
  return { terminalSupplyNear, requiredRevenueUsd, requiredDailyVolumeUsd, growth };
}
