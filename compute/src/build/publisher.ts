/** Compile the page's fixed numerical comparisons; never export another essay. */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runModel } from '../models/model.ts';
import { RESEARCH_BASELINE } from '../models/baseline.ts';

const codeRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const root = process.env.THESIS_EVIDENCE_ROOT || path.join(codeRoot, '.cache/evidence');
const load = (relative: string) => JSON.parse(fs.readFileSync(path.join(root, relative), 'utf8'));
const edition = JSON.parse(fs.readFileSync(path.join(codeRoot, 'data/evidence/2026-11-02/catalog.json'), 'utf8')).editions.publisher as string;
const denominator = load('data/original/published-denominator.json');
const publishedScenarios = load('data/original/published-scenarios.json');
const cases = [
  { id: 'fixed50', label: '50% volume growth · fixed price', patch: {} },
  { id: 'fixed20', label: '20% volume growth · fixed price', patch: { volumeGrowth: .2 } },
  { id: 'both50', label: '50% volume growth · 50% price growth', patch: { priceMode: 'growth' as const, priceGrowth: .5 } },
  { id: 'multiple200', label: '50% volume growth · 200× revenue', patch: { priceMode: 'multiple' as const, revenueMultiple: 200 } },
];
const modelExamples = cases.map(item => {
  const result = runModel({ ...RESEARCH_BASELINE, ...item.patch });
  const row = result.summary.final;
  return {
    id: item.id, label: item.label, patch: item.patch,
    revenueUsd: row.retainedRevenueUsd, acquiredNear: row.purchasedNear,
    coverage: row.purchaseCoverage, netSupplyGrowth: row.netSupplyGrowth,
    holderSupplyShareIndex: row.nonStakerSupplyShareIndex,
    holderMarkedValueIndex: row.nonStakerMarkedValueIndex, assumptions: result.assumptions,
  };
});
const data = {
  version: edition,
  scope: 'Publisher-model reconstructions and preserved operating-period examples: five rows, four growth steps. Current live forward models are calculated separately.',
  denominator, publishedScenarios, modelExamples,
  reflexivityClaim: {
    url: 'https://svrn-research-nearprotocol.netlify.app/', heading: 'The Reflexive Dynamic',
    retrievedAt: '2026-10-26', sha256: '90b8c2bb374c75df62a98cb64d768ed15627ea27e33a6d902eaee9711762c1c9',
    quote: 'higher prices lower the effective barrier to deflation in token terms',
    conditions: 'Fixed positive native issuance and fixed USD purchase budget: acquired NEAR = budget / price. Higher price reduces the acquired quantity. Timing and changing budgets must be modeled separately.',
  },
};
fs.writeFileSync(path.join(root, 'data/baselines/publisher.json'), JSON.stringify(data, null, 2).replace(/[\u007f-\uffff]/g, value => '\\u' + value.charCodeAt(0).toString(16).padStart(4, '0')) + '\n');
console.log(JSON.stringify({ version: edition, modelExamples: modelExamples.length, publishedYears: publishedScenarios.length }));
