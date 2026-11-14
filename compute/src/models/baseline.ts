import evidence from '../../../data/evidence/2026-11-02/baselines/observations.json';
import { AUTHORED_BASELINE, type ModelAssumptions } from './model';

/** Starting scale is dated evidence. Future economics remain explicit assumptions. */
export const RESEARCH_BASELINE: Readonly<ModelAssumptions> = Object.freeze({
  ...AUTHORED_BASELINE,
  dailyVolumeUsd: evidence.seed.dailyVolumeUsd,
  openingSupplyNear: evidence.seed.openingSupplyNear,
  initialPriceUsd: evidence.seed.initialPriceUsd,
});

export const BASELINE_NOTES = [
  'Starting volume: the mean of the clean 30-day window ending 21 October 2026.',
  'Opening supply and initial price: separately dated 25 October snapshots. This is a mixed-date scenario seed, not a synchronized yield observation.',
  'Retained external fees: assumed 5 bps. The observed collection ratio is context, not an independently measured cash margin.',
  'Annual volume growth 50%, full purchase allocation, zero cash costs and zero gas burn are assumptions, not estimates or promises.',
  'Acquired tokens are held by default. A positive burn allocation is hypothetical.',
];
