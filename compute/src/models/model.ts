/**
 * A transparent cash-allocation and token-supply model, not a market-price forecast.
 * Rates and allocations are decimal fractions; retainedFeeBps is in basis points.
 * Each year's execution price uses that year's revenue and OPENING token supply.
 */
export type PriceMode = 'fixed' | 'growth' | 'multiple';

export interface ModelAssumptions {
  years: number;
  dailyVolumeUsd: number;
  volumeGrowth: number;
  retainedFeeBps: number;
  annualCashCostsUsd: number;
  cashCostGrowth: number;
  purchaseAllocation: number;
  openingSupplyNear: number;
  initialPriceUsd: number;
  priceMode: PriceMode;
  priceGrowth: number;
  revenueMultiple: number;
  issuanceRate: number;
  annualGasBurnNear: number;
  gasBurnGrowth: number;
  burnShare: number;
  stakeShare: number;
  stakingRatio: number;
  stakerIssuanceShare: number;
  validatorCommission: number;
  illustrativeHoldingNear: number;
}

export interface ValidationIssue {
  field: keyof ModelAssumptions | 'projectedSupply' | 'projectedInventory' | 'projectedValues';
  message: string;
}

export class ModelValidationError extends Error {
  readonly issues: ValidationIssue[];
  constructor(issues: ValidationIssue[]) {
    super(issues.map((issue) => issue.message).join(' '));
    this.name = 'ModelValidationError';
    this.issues = issues;
  }
}

export interface ModelYear {
  year: number;
  volumeUsd: number;
  retainedRevenueUsd: number;
  cashCostsUsd: number;
  availableCashUsd: number;
  cashDeficitUsd: number;
  purchaseBudgetUsd: number;
  reserveCashUsd: number;
  openingSupplyNear: number;
  issuedNear: number;
  gasBurnNear: number;
  executionPriceUsd: number;
  purchasedNear: number;
  purchaseBurnNear: number;
  totalBurnNear: number;
  heldPurchasesNear: number;
  stakedPurchasesNear: number;
  cumulativeHeldPurchasesNear: number;
  cumulativeStakedPurchasesNear: number;
  cumulativePurchaseBurnNear: number;
  closingSupplyNear: number;
  netSupplyChangeNear: number;
  netSupplyGrowth: number;
  /** Ratios, e.g. 0.2 = 20%; null when gross issuance is zero. */
  purchaseCoverage: number | null;
  burnCoverage: number | null;
  nonStakerBalanceNear: number;
  stakerBalanceNear: number;
  stakerRewardNear: number;
  nonStakerSupplyShareIndex: number;
  stakerSupplyShareIndex: number;
  /** A valuation mark using the year's execution-price assumption, not an end-date quote. */
  nonStakerMarkedValueUsd: number;
  stakerMarkedValueUsd: number;
  nonStakerMarkedValueIndex: number;
  stakerMarkedValueIndex: number;
}

export interface ModelSummary {
  final: ModelYear;
  totalVolumeUsd: number;
  totalRetainedRevenueUsd: number;
  totalCashCostsUsd: number;
  totalCashDeficitUsd: number;
  totalPurchasedNear: number;
  totalIssuedNear: number;
  totalBurnNear: number;
  firstPurchaseCoverageYear: number | null;
  firstSupplyBreakEvenYear: number | null;
}

export interface ModelResult {
  timeBasis: 'operating-periods' | 'forward-years';
  assumptions: ModelAssumptions;
  rows: ModelYear[];
  summary: ModelSummary;
  warnings: string[];
}

/** Every number here is an illustrative assumption, not a NEAR observation. */
export const AUTHORED_BASELINE: Readonly<ModelAssumptions> = Object.freeze({
  years: 5,
  dailyVolumeUsd: 50_000_000_000 / 365,
  volumeGrowth: 0.5,
  retainedFeeBps: 5,
  annualCashCostsUsd: 0,
  cashCostGrowth: 0,
  purchaseAllocation: 1,
  openingSupplyNear: 1_000_000_000,
  initialPriceUsd: 5,
  priceMode: 'fixed',
  priceGrowth: 0,
  revenueMultiple: 200,
  issuanceRate: 0.025,
  annualGasBurnNear: 0,
  gasBurnGrowth: 0,
  burnShare: 0,
  stakeShare: 0,
  stakingRatio: 0.45,
  stakerIssuanceShare: 0.9,
  validatorCommission: 0.05,
  illustrativeHoldingNear: 1_000,
});

const numericFields: (keyof ModelAssumptions)[] = [
  'years', 'dailyVolumeUsd', 'volumeGrowth', 'retainedFeeBps',
  'annualCashCostsUsd', 'cashCostGrowth', 'purchaseAllocation',
  'openingSupplyNear', 'initialPriceUsd', 'priceGrowth', 'revenueMultiple',
  'issuanceRate', 'annualGasBurnNear', 'gasBurnGrowth', 'burnShare',
  'stakeShare', 'stakingRatio', 'stakerIssuanceShare',
  'validatorCommission', 'illustrativeHoldingNear',
];

export function validateAssumptions(a: ModelAssumptions): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const fail = (field: ValidationIssue['field'], message: string) => issues.push({ field, message });
  for (const field of numericFields) {
    if (!Number.isFinite(a[field] as number)) fail(field, `${field} must be a finite number.`);
  }
  if (issues.length) return issues;
  if (!Number.isInteger(a.years) || a.years < 1 || a.years > 30) fail('years', 'Choose a whole-number horizon from 1 to 30 years.');
  for (const field of ['dailyVolumeUsd', 'retainedFeeBps', 'annualCashCostsUsd', 'annualGasBurnNear'] as const) {
    if (a[field] < 0) fail(field, `${field} cannot be negative.`);
  }
  if (a.retainedFeeBps > 10_000) fail('retainedFeeBps', 'Retained fees cannot exceed 100% of volume.');
  for (const field of ['volumeGrowth', 'cashCostGrowth', 'priceGrowth', 'gasBurnGrowth'] as const) {
    if (a[field] <= -1) fail(field, `${field} must be greater than -100%.`);
  }
  for (const field of ['purchaseAllocation', 'issuanceRate', 'burnShare', 'stakeShare', 'stakerIssuanceShare', 'validatorCommission'] as const) {
    if (a[field] < 0 || a[field] > 1) fail(field, `${field} must be between 0% and 100%.`);
  }
  if (a.burnShare + a.stakeShare > 1 + 1e-12) fail('burnShare', 'Burned and staked purchase shares cannot sum to more than 100%; the remainder is held.');
  if (a.stakingRatio <= 0 || a.stakingRatio > 1) fail('stakingRatio', 'The assumed network staking ratio must be above 0% and at most 100%.');
  if (a.openingSupplyNear <= 0) fail('openingSupplyNear', 'Opening token supply must be positive.');
  if (a.initialPriceUsd <= 0) fail('initialPriceUsd', 'The initial token price must be positive.');
  if (a.revenueMultiple <= 0) fail('revenueMultiple', 'The total-valuation/revenue multiple must be positive.');
  if (a.illustrativeHoldingNear <= 0) fail('illustrativeHoldingNear', 'The illustrative holder balance must be positive.');
  if (a.illustrativeHoldingNear > a.openingSupplyNear * a.stakingRatio) fail('illustrativeHoldingNear', 'The illustrative stake cannot exceed the entire assumed network stake.');
  if (!['fixed', 'growth', 'multiple'].includes(a.priceMode)) fail('priceMode', 'Choose fixed, independent price growth, or constant revenue multiple.');
  if (a.priceMode === 'multiple' && (a.dailyVolumeUsd === 0 || a.retainedFeeBps === 0)) {
    fail('priceMode', 'A revenue multiple requires positive retained revenue; use an independent price rule for zero-revenue scenarios.');
  }
  return issues;
}

const sum = (rows: ModelYear[], field: keyof ModelYear): number =>
  rows.reduce((total, row) => total + (row[field] as number), 0);

/** Historical operating-period convention: Year 1 uses the starting run rate. */
export function runModel(input: ModelAssumptions): ModelResult {
  return calculateModel(input, 0);
}

/** Five forward years apply five growth steps to today's annualized run rate. */
export function runForwardModel(input: ModelAssumptions): ModelResult {
  return calculateModel(input, 1);
}

function calculateModel(input: ModelAssumptions, forward: 0 | 1): ModelResult {
  const issues = validateAssumptions(input);
  if (issues.length) throw new ModelValidationError(issues);
  const a = { ...input };
  const rows: ModelYear[] = [];
  let supply = a.openingSupplyNear;
  let cumulativeHeld = 0;
  let cumulativeStaked = 0;
  let cumulativeBurned = 0;
  let stakerBalance = a.illustrativeHoldingNear;
  const initialShare = a.illustrativeHoldingNear / a.openingSupplyNear;
  const yearOneRevenue = a.dailyVolumeUsd * 365 * a.retainedFeeBps / 10_000;
  const initialMarkedPrice = a.priceMode === 'multiple'
    ? a.revenueMultiple * yearOneRevenue / a.openingSupplyNear
    : a.initialPriceUsd;
  const initialMarkedValue = a.illustrativeHoldingNear * initialMarkedPrice;

  for (let index = 0; index < a.years; index += 1) {
    const year = index + 1;
    const growthStep = index + forward;
    const openingSupply = supply;
    const volume = a.dailyVolumeUsd * 365 * (1 + a.volumeGrowth) ** growthStep;
    const revenue = volume * a.retainedFeeBps / 10_000;
    const cashCosts = a.annualCashCostsUsd * (1 + a.cashCostGrowth) ** growthStep;
    const available = Math.max(0, revenue - cashCosts);
    const deficit = Math.max(0, cashCosts - revenue);
    const budget = available * a.purchaseAllocation;
    const issuance = openingSupply * a.issuanceRate;
    const gasBurn = a.annualGasBurnNear * (1 + a.gasBurnGrowth) ** growthStep;
    const price = a.priceMode === 'multiple'
      ? a.revenueMultiple * revenue / openingSupply
      : a.initialPriceUsd * (a.priceMode === 'growth' ? (1 + a.priceGrowth) ** growthStep : 1);
    if (!Number.isFinite(price) || price <= 0) {
      throw new ModelValidationError([{ field: 'projectedValues', message: `Year ${year} has no valid positive execution price under these assumptions.` }]);
    }
    const purchases = budget / price;
    const purchaseBurn = purchases * a.burnShare;
    const staked = purchases * a.stakeShare;
    const held = purchases * Math.max(0, 1 - a.burnShare - a.stakeShare);
    const totalBurn = gasBurn + purchaseBurn;
    const netNew = issuance - totalBurn;
    const closingSupply = openingSupply + netNew;
    if (!Number.isFinite(closingSupply) || closingSupply <= 0) {
      throw new ModelValidationError([{ field: 'projectedSupply', message: `Year ${year} would destroy the entire token supply. Reduce burns or change the price/revenue assumptions.` }]);
    }
    cumulativeHeld += held;
    cumulativeStaked += staked;
    cumulativeBurned += purchaseBurn;
    if (cumulativeHeld + cumulativeStaked > closingSupply * (1 + 1e-10)) {
      throw new ModelValidationError([{ field: 'projectedInventory', message: `Year ${year} would retain more acquired tokens than the total supply. This fixed-price/liquidity assumption is not feasible.` }]);
    }
    if (cumulativeStaked > closingSupply * a.stakingRatio * (1 + 1e-10)) {
      throw new ModelValidationError([{ field: 'stakingRatio', message: `Year ${year}'s acquired staking principal would exceed the entire assumed network stake. Increase the staking-ratio assumption or reduce that allocation.` }]);
    }
    const networkStake = openingSupply * a.stakingRatio;
    if (stakerBalance > networkStake * (1 + 1e-10)) {
      throw new ModelValidationError([{ field: 'stakingRatio', message: `Year ${year}'s illustrative holder would exceed the entire assumed network stake. Change the staking ratio or holder size.` }]);
    }
    const stakerReward = (stakerBalance / networkStake) * issuance * a.stakerIssuanceShare * (1 - a.validatorCommission);
    stakerBalance += stakerReward;
    const nonStakerBalance = a.illustrativeHoldingNear;
    if (stakerBalance > closingSupply * (1 + 1e-10) || nonStakerBalance > closingSupply * (1 + 1e-10)) {
      throw new ModelValidationError([{ field: 'projectedSupply', message: `Year ${year}'s burns leave less supply than the illustrative holder owns. These assumptions cannot describe a feasible holder outcome.` }]);
    }
    const row: ModelYear = {
      year,
      volumeUsd: volume,
      retainedRevenueUsd: revenue,
      cashCostsUsd: cashCosts,
      availableCashUsd: available,
      cashDeficitUsd: deficit,
      purchaseBudgetUsd: budget,
      reserveCashUsd: available - budget,
      openingSupplyNear: openingSupply,
      issuedNear: issuance,
      gasBurnNear: gasBurn,
      executionPriceUsd: price,
      purchasedNear: purchases,
      purchaseBurnNear: purchaseBurn,
      totalBurnNear: totalBurn,
      heldPurchasesNear: held,
      stakedPurchasesNear: staked,
      cumulativeHeldPurchasesNear: cumulativeHeld,
      cumulativeStakedPurchasesNear: cumulativeStaked,
      cumulativePurchaseBurnNear: cumulativeBurned,
      closingSupplyNear: closingSupply,
      netSupplyChangeNear: netNew,
      netSupplyGrowth: netNew / openingSupply,
      purchaseCoverage: issuance > 0 ? purchases / issuance : null,
      burnCoverage: issuance > 0 ? totalBurn / issuance : null,
      nonStakerBalanceNear: nonStakerBalance,
      stakerBalanceNear: stakerBalance,
      stakerRewardNear: stakerReward,
      nonStakerSupplyShareIndex: (nonStakerBalance / closingSupply) / initialShare * 100,
      stakerSupplyShareIndex: (stakerBalance / closingSupply) / initialShare * 100,
      nonStakerMarkedValueUsd: nonStakerBalance * price,
      stakerMarkedValueUsd: stakerBalance * price,
      nonStakerMarkedValueIndex: nonStakerBalance * price / initialMarkedValue * 100,
      stakerMarkedValueIndex: stakerBalance * price / initialMarkedValue * 100,
    };
    if (Object.values(row).some((value) => value !== null && !Number.isFinite(value))) {
      throw new ModelValidationError([{ field: 'projectedValues', message: `Year ${year} exceeds finite numerical limits. Choose a shorter horizon or smaller assumptions.` }]);
    }
    rows.push(row);
    supply = closingSupply;
  }

  const warnings = [
    'This model assumes execution at the selected price; liquidity, slippage and market-price impact are not estimated.',
    'Retained external revenue is an operating assumption. Dollar-marked native-token receipts, staking issuance and token revaluation are not independent external cash earnings.',
    'Purchase coverage compares quantities; it does not establish that every issued token is sold or that held purchases are permanently removed.',
    'Holder value uses each year’s modeled execution price, not an independently forecast closing-date quote.',
  ];
  if (a.annualCashCostsUsd === 0) warnings.push('Cash costs are assumed zero. This is a generous scenario, not measured operating profit.');
  if (a.annualGasBurnNear === 0) warnings.push('Gas burn is assumed zero in this scenario; real native burns must be sourced separately.');
  if (a.stakeShare > 0) warnings.push('Staked-purchase inventory tracks acquired principal only; treasury staking rewards are not separately consolidated into external revenue.');
  if (rows.some((row) => row.cashDeficitUsd > 0)) warnings.push('A cash deficit is shown separately. External financing, debt and carry-forward cash balances are not modeled.');

  return {
    timeBasis: forward ? 'forward-years' : 'operating-periods',
    assumptions: a,
    rows,
    summary: {
      final: rows[rows.length - 1],
      totalVolumeUsd: sum(rows, 'volumeUsd'),
      totalRetainedRevenueUsd: sum(rows, 'retainedRevenueUsd'),
      totalCashCostsUsd: sum(rows, 'cashCostsUsd'),
      totalCashDeficitUsd: sum(rows, 'cashDeficitUsd'),
      totalPurchasedNear: sum(rows, 'purchasedNear'),
      totalIssuedNear: sum(rows, 'issuedNear'),
      totalBurnNear: sum(rows, 'totalBurnNear'),
      firstPurchaseCoverageYear: rows.find((row) => row.purchaseCoverage !== null && row.purchaseCoverage >= 1)?.year ?? null,
      firstSupplyBreakEvenYear: rows.find((row) => row.totalBurnNear >= row.issuedNear)?.year ?? null,
    },
    warnings,
  };
}

export interface PriceFrontier {
  purchaseEquivalencePriceUsd: number | null;
  supplyBreakEvenPriceUsd: number | null;
  supplyBreakEvenStatus: 'price-dependent' | 'already-covered-by-other-burns' | 'unreachable-with-this-policy' | 'no-issuance';
}

/** Thresholds hold this year's budget, issuance and other burns fixed. They are not fair-value ceilings. */
export function calculatePriceFrontier(row: ModelYear, burnShare: number): PriceFrontier {
  if (!Number.isFinite(burnShare) || burnShare < 0 || burnShare > 1) {
    throw new ModelValidationError([{ field: 'burnShare', message: 'Burn share must be between 0% and 100%.' }]);
  }
  if (row.issuedNear === 0) {
    return { purchaseEquivalencePriceUsd: null, supplyBreakEvenPriceUsd: null, supplyBreakEvenStatus: 'no-issuance' };
  }
  const purchaseThreshold = row.purchaseBudgetUsd > 0 ? row.purchaseBudgetUsd / row.issuedNear : null;
  const burnsRequired = row.issuedNear - row.gasBurnNear;
  if (burnsRequired <= 0) {
    return { purchaseEquivalencePriceUsd: purchaseThreshold, supplyBreakEvenPriceUsd: null, supplyBreakEvenStatus: 'already-covered-by-other-burns' };
  }
  if (burnShare === 0 || row.purchaseBudgetUsd === 0) {
    return { purchaseEquivalencePriceUsd: purchaseThreshold, supplyBreakEvenPriceUsd: null, supplyBreakEvenStatus: 'unreachable-with-this-policy' };
  }
  return {
    purchaseEquivalencePriceUsd: purchaseThreshold,
    supplyBreakEvenPriceUsd: burnShare * row.purchaseBudgetUsd / burnsRequired,
    supplyBreakEvenStatus: 'price-dependent',
  };
}

export type SensitivityParameter = 'volumeGrowth' | 'retainedFeeBps' | 'annualCashCostsUsd' | 'purchaseAllocation' | 'initialPriceUsd' | 'priceGrowth' | 'revenueMultiple' | 'issuanceRate' | 'burnShare' | 'annualGasBurnNear';

export interface SensitivityCase {
  label: string;
  parameter: SensitivityParameter;
  value: number;
}

export interface SensitivityResult extends SensitivityCase {
  purchaseCoverage: number | null;
  burnCoverage: number | null;
  finalNetSupplyGrowth: number | null;
  error: string | null;
}

/** Each result changes exactly one named assumption; errors are data, not silently clipped outcomes. */
export function runSensitivity(base: ModelAssumptions, cases: SensitivityCase[]): SensitivityResult[] {
  return cases.map((item) => {
    try {
      const result = runModel({ ...base, [item.parameter]: item.value });
      const final = result.summary.final;
      return { ...item, purchaseCoverage: final.purchaseCoverage, burnCoverage: final.burnCoverage, finalNetSupplyGrowth: final.netSupplyGrowth, error: null };
    } catch (error) {
      return { ...item, purchaseCoverage: null, burnCoverage: null, finalNetSupplyGrowth: null, error: error instanceof Error ? error.message : 'Invalid scenario.' };
    }
  });
}

export interface ScenarioExplanation {
  changes: string[];
  findings: string[];
}

const assumptionLabels: Record<keyof ModelAssumptions, string> = {
  years: 'Projection horizon',
  dailyVolumeUsd: 'Starting daily volume',
  volumeGrowth: 'Annual volume growth',
  retainedFeeBps: 'Retained fees per dollar of volume',
  annualCashCostsUsd: 'Starting annual cash costs',
  cashCostGrowth: 'Annual cash-cost growth',
  purchaseAllocation: 'Cash allocated to purchases',
  openingSupplyNear: 'Opening token supply',
  initialPriceUsd: 'Initial execution price',
  priceMode: 'Price rule',
  priceGrowth: 'Independent annual price growth',
  revenueMultiple: 'Total-valuation/revenue multiple',
  issuanceRate: 'Annual gross issuance',
  annualGasBurnNear: 'Starting annual native gas burn',
  gasBurnGrowth: 'Annual gas-burn growth',
  burnShare: 'Acquired tokens burned',
  stakeShare: 'Acquired tokens staked',
  stakingRatio: 'Assumed network staking ratio',
  stakerIssuanceShare: 'Issuance distributed to staking',
  validatorCommission: 'Pool commission',
  illustrativeHoldingNear: 'Illustrative holder balance',
};

function describeAssumption(key: keyof ModelAssumptions, value: number | PriceMode): string {
  if (key === 'priceMode') return { fixed: 'fixed price', growth: 'independent price growth', multiple: 'constant revenue multiple' }[value as PriceMode];
  const n = value as number;
  const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(n);
  if (['volumeGrowth', 'cashCostGrowth', 'purchaseAllocation', 'priceGrowth', 'issuanceRate', 'gasBurnGrowth', 'burnShare', 'stakeShare', 'stakingRatio', 'stakerIssuanceShare', 'validatorCommission'].includes(key)) {
    return `${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(n * 100)}%`;
  }
  if (key === 'retainedFeeBps') return `${n} bps`;
  if (key === 'revenueMultiple') return `${n}×`;
  if (key === 'years') return `${n} years`;
  if (key === 'dailyVolumeUsd') return `$${compact}/day`;
  if (key === 'annualCashCostsUsd') return `$${compact}/year`;
  if (key === 'initialPriceUsd') return `$${new Intl.NumberFormat('en-US', { maximumFractionDigits: 6 }).format(n)}`;
  if (key === 'annualGasBurnNear') return `${compact} NEAR/year`;
  return `${compact} NEAR`;
}

/** A constrained, deterministic explanation; never rewrites the authored thesis. */
export function explainScenario(baseline: ModelResult, scenario: ModelResult): ScenarioExplanation {
  const changes = (Object.keys(baseline.assumptions) as (keyof ModelAssumptions)[])
    .filter((key) => baseline.assumptions[key] !== scenario.assumptions[key])
    .map((key) => `${assumptionLabels[key]}: ${describeAssumption(key, baseline.assumptions[key])} → ${describeAssumption(key, scenario.assumptions[key])}`);
  const final = scenario.summary.final;
  const findings: string[] = [];
  if (scenario.assumptions.priceMode === 'multiple' && scenario.assumptions.annualCashCostsUsd === 0) {
    findings.push('At a constant total-valuation/revenue multiple, revenue growth changes the modeled price as well as earnings. Purchase coverage remains allocation ÷ (multiple × issuance rate), when issuance is positive.');
  }
  if (scenario.assumptions.burnShare === 0) {
    findings.push('Acquired tokens are held or staked. These purchases do not reduce total supply; only separately modeled native burns do.');
  }
  if (final.netSupplyChangeNear > 0) findings.push('Modeled total supply is still growing in the final year. This does not by itself predict a falling token price.');
  else if (final.netSupplyChangeNear < 0) findings.push('Modeled native burns exceed issuance in the final year. This is a conditional supply result, not a guaranteed investment return.');
  else findings.push('Modeled native burns equal issuance in the final year.');
  if (scenario.summary.totalCashDeficitUsd > 0) findings.push('Cash costs exceed retained revenue in at least one year; purchase funding is zero for the unfunded portion.');
  if (final.stakerSupplyShareIndex > final.nonStakerSupplyShareIndex) findings.push('The illustrative staker receives a share of already-counted issuance, so its supply-share path differs from the unstaked holder’s path.');
  return { changes, findings };
}
