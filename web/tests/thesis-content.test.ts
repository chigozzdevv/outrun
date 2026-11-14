import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import App from '../src/app.tsx';
import { SupplyEvidence } from '../src/evidence/supply.tsx';
import { FixedScenarioComparison, PublishedModelAudit, ThesisText, type ThesisSection } from '../src/thesis/essay.tsx';
import thesis from '../src/thesis/article.json';
import audit from '../../data/evidence/2026-11-02/baselines/publisher.json';
import { RESEARCH_BASELINE } from '../../compute/src/models/baseline.ts';
import { runModel, type ModelAssumptions } from '../../compute/src/models/model.ts';
import { encodeScenario } from '../../compute/src/models/scenario.ts';
import { fallbackSnapshot } from '../../compute/src/live/snapshot.ts';
import { LiveDataProvider } from '../src/live/provider.tsx';

const root = new URL('../../', import.meta.url);
const path = (relative: string) => fileURLToPath(new URL(relative, root));
const read = (relative: string) => readFileSync(path(relative), 'utf8');
const json = <T>(relative: string) => JSON.parse(read(relative)) as T;
const excluded = /archivi\.ng|brightest\s+teenagers|nearhackathon\.com|HACKATHON\.md|editorial reference|official brief/i;
const near = (actual: number | null, expected: number | null) => {
  assert.notEqual(actual, null);
  assert.notEqual(expected, null);
  assert.ok(Math.abs(actual! - expected!) <= Math.max(1e-9, Math.abs(expected!) * 1e-12), `${actual} differs from ${expected}`);
};

function render(assumptions: ModelAssumptions): string {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'window');
  try {
    Object.defineProperty(globalThis, 'window', { configurable: true, value: { location: { search: `?scenario=${encodeScenario(assumptions)}` } } });
    return renderToStaticMarkup(createElement(App));
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'window', descriptor);
    else Reflect.deleteProperty(globalThis, 'window');
  }
}

/** Read each complete authored wrapper, including its nested text blocks. */
function authoredSections(html: string): Map<string, string> {
  const result = new Map<string, string>();
  const opening = /<div\b[^>]*\bdata-authored-section="([^"]+)"[^>]*>/g;
  for (let match = opening.exec(html); match; match = opening.exec(html)) {
    const tags = /<div\b[^>]*>|<\/div\s*>/g;
    tags.lastIndex = match.index;
    let depth = 0;
    let closing = -1;
    for (let tag = tags.exec(html); tag; tag = tags.exec(html)) {
      depth += tag[0].startsWith('</') ? -1 : 1;
      if (depth === 0) { closing = tags.lastIndex; break; }
    }
    assert.ok(closing > match.index, `Unclosed authored section: ${match[1]}`);
    assert.ok(!result.has(match[1]), `Duplicate authored section: ${match[1]}`);
    result.set(match[1], html.slice(match.index, closing));
  }
  return result;
}

test('the page keeps source citations in the argument without a duplicate reference list', () => {
  const data = json<{ sources: { url: string; title: string; kind?: string }[] }>('data/evidence/2026-11-02/baselines/observations.json');
  assert.ok(data.sources.length, 'The article must retain the sources supporting its claims.');
  data.sources.forEach(entry => assert.equal(excluded.test(`${entry.url} ${entry.title} ${entry.kind ?? ''}`), false, entry.title));
  const html = render({ ...RESEARCH_BASELINE });
  assert.equal(excluded.test(html), false);
  assert.equal(Object.hasOwn(thesis.chapters, 'research'), false, 'Verified findings belong in the article rather than a separate research chapter.');
  assert.equal((html.match(/<main\b/g) ?? []).length, 1);
  assert.doesNotMatch(html, /class="essential-references"|id="sources"|<h2>References<\/h2>/);
  assert.equal(/href="\/(?:downloads|research)\//i.test(html), false, 'The page must not route readers to duplicate papers.');
  assert.equal(/href="\/data\/(?:library|[^"]*catalog)/i.test(html), false, 'The page must not link a separate research catalog.');
  assert.equal(/(?:id|href)="#?research"/i.test(html), false, 'There is no separate research destination.');
});

test('every complete authored section renders once and old scenario URLs cannot rewrite the page', () => {
  const baseline = render({ ...RESEARCH_BASELINE });
  const changed = render({ ...RESEARCH_BASELINE, volumeGrowth: 0.2, priceMode: 'multiple', retainedFeeBps: 3, burnShare: 0.3, issuanceRate: 0.016 });
  const originalSections = authoredSections(baseline);
  const changedSections = authoredSections(changed);
  const keys = Object.keys(thesis.chapters) as ThesisSection[];
  assert.deepEqual([...originalSections.keys()].sort(), [...keys].sort());
  assert.deepEqual([...changedSections.keys()].sort(), [...keys].sort());
  keys.forEach(section => {
    const expected = renderToStaticMarkup(createElement(ThesisText, { section }));
    assert.equal(originalSections.get(section), expected, `Missing or altered authored content in ${section}.`);
    assert.equal(changedSections.get(section), expected, `A legacy scenario URL rewrote authored content in ${section}.`);
  });
  assert.equal(changed, baseline, 'The article and fixed figures must ignore old calculator parameters.');
});

test('actual matched supply evidence remains inline and unchanged by reader scenarios', () => {
  const supply = renderToStaticMarkup(createElement(SupplyEvidence));
  assert.ok(supply.includes('aria-label="Historical minting and native burns"'));
  assert.ok(supply.includes('20.65%'));
  assert.ok(supply.includes('17.26%'));
  assert.ok(supply.includes('Wallet receipts / gross minting'));
  const baseline = render({ ...RESEARCH_BASELINE });
  assert.match(baseline, /they do not establish purchases, permanent retention or selling by issuance recipients/);
  const changed = render({ ...RESEARCH_BASELINE, issuanceRate: 0.016, annualGasBurnNear: 1e6, burnShare: 0.3 });
  assert.ok(baseline.includes(supply), 'The observed matched windows must appear in the article.');
  assert.ok(changed.includes(supply), 'Reader supply assumptions must not rewrite historical observations.');
});

test('source links attach to the relevant names and live claims without losing references', () => {
  const snapshot = fallbackSnapshot();
  snapshot.review.sources.forEach(source => { source.state = 'unchanged'; });
  const sections = (Object.keys(thesis.chapters) as ThesisSection[]).map(section => createElement(ThesisText, { section, key: section }));
  const html = renderToStaticMarkup(createElement(LiveDataProvider, { snapshot, children: sections }));
  assert.match(html, /<a href="\/data\/collectors\/20260823-20261028\/withdrawals\.csv" download="withdrawals\.csv"[^>]*>794 withdrawals<\/a>/);
  assert.match(html, /<a href="https:\/\/docs.near-intents.org\/integration\/distribution-channels\/1click-api\/explorer\/introduction"[^>]*>1Click Explorer<\/a>/);
  assert.match(html, /<a href="https:\/\/docs\.across\.to\/introduction\/fees"[^>]*>Across<\/a> and <a [^>]+>LI\.FI<\/a> demonstrate/);
  assert.match(html, /<a href="https:\/\/api\.llama\.fi\/summary\/dexs\/near-intents"[^>]*>settled volume<\/a>/i);
  assert.match(html, /<a [^>]+>Derived native burns<\/a> were/);
  assert.match(html, /<a [^>]+>HSP-029<\/a> outlined/);
  assert.doesNotMatch(html, />Original report, The Reflexive Dynamic<|>Volume series<|>Aave methodology</);
  assert.equal(/\{\{\w+\}\}|NaN|Infinity/.test(html), false);
  for (const [kind, filename] of [['daily', 'daily-observations.csv'], ['native', 'native-receipts.csv']]) {
    assert.ok(html.includes(`href="/api/thesis/${kind}.csv?snapshot=${snapshot.id}" download="${filename}"`), 'Inline downloads must use the displayed snapshot.');
  }
  const [earlier, later] = snapshot.operating.supply.windows;
  assert.ok(html.includes(`href="https://nearblocks.io/blocks/${earlier.openingBlock}"`));
  assert.ok(html.includes(`href="https://nearblocks.io/blocks/${later.closingBlock}"`));
});

test('the full article displays all publisher years including the stronger 2029 and 2030 bull cases', () => {
  const html = render({ ...RESEARCH_BASELINE });
  const published = renderToStaticMarkup(createElement(PublishedModelAudit));
  assert.ok(html.includes(published), 'The complete audit component must render within the article.');
  assert.deepEqual(audit.publishedScenarios.map(row => row.year), [2026, 2027, 2028, 2029, 2030]);
  const start = html.indexOf('<div class="full-model-audit">');
  assert.ok(start >= 0);
  const prefix = html.slice(0, start);
  assert.equal((prefix.match(/<details\b/g) ?? []).length, (prefix.match(/<\/details>/g) ?? []).length, 'The full audit must not be hidden inside a collapsed disclosure.');
  for (const row of audit.publishedScenarios) {
    const tableRow = published.match(new RegExp(`<tr><td>${row.year}</td>([\\s\\S]*?)</tr>`));
    assert.ok(tableRow, `Missing published year ${row.year}.`);
    assert.ok(tableRow[1].includes(`${row.purchase_coverage_pct_at_high_price.toFixed(1)}–${row.purchase_coverage_pct_at_low_price.toFixed(1)}%`));
    const revenue = row.published_base_case_daily_volume_usd * 365 * row.reconstructed_net_fee_bps / 10_000;
    near(row.reconstructed_annual_net_fees_usd, revenue);
    near(row.purchase_coverage_pct_at_low_price, revenue / row.published_base_price_low / row.published_annual_issued_near * 100);
    near(row.purchase_coverage_pct_at_high_price, revenue / row.published_base_price_high / row.published_annual_issued_near * 100);
  }
  assert.ok(audit.publishedScenarios.find(row => row.year === 2029)!.purchase_coverage_pct_at_low_price > 100);
  assert.ok(audit.publishedScenarios.find(row => row.year === 2030)!.purchase_coverage_pct_at_high_price > 100);
});

test('the gross-versus-retained denominator audit uses the published inputs and reconciles independently', () => {
  assert.deepEqual(json('data/evidence/2026-11-02/inputs/published-denominator.json'), audit.denominator);
  assert.deepEqual(json('data/evidence/2026-11-02/inputs/published-scenarios.json'), audit.publishedScenarios);
  const d = audit.denominator;
  assert.equal(d.published_market_cap_usd, 1_490_000_000);
  assert.equal(d.published_90day_volume_usd, 6_930_000_000);
  assert.equal(d.gross_fee_rate_bps, 19);
  assert.equal(d.net_rate_bps, 6.101);
  const gross = 6_930_000_000 * 4 * 19 / 10_000;
  const retained = 6_930_000_000 * 4 * 6.101 / 10_000;
  near(d.annual_gross_fees_usd, gross);
  near(d.annual_net_fees_at_published_model_rate_usd, retained);
  near(d.market_cap_to_gross_fees, 1_490_000_000 / gross);
  near(d.market_cap_to_modelled_net_capture, 1_490_000_000 / retained);
  near(d.denominator_ratio_gross_over_net, gross / retained);
  const html = renderToStaticMarkup(createElement(PublishedModelAudit));
  assert.ok(html.includes(`${d.market_cap_to_gross_fees.toFixed(2)}×`));
  assert.ok(html.includes(`${d.market_cap_to_modelled_net_capture.toFixed(2)}×`));
});

test('preserved operating-period examples remain reproducible alongside the live forward comparison', () => {
  const patches: Record<string, Partial<ModelAssumptions>> = {
    fixed50: {},
    fixed20: { volumeGrowth: 0.2 },
    both50: { priceMode: 'growth', priceGrowth: 0.5 },
    multiple200: { priceMode: 'multiple', revenueMultiple: 200 },
  };
  assert.deepEqual(audit.modelExamples.map(row => row.id).sort(), Object.keys(patches).sort());
  for (const example of audit.modelExamples) {
    const assumptions: ModelAssumptions = { ...RESEARCH_BASELINE, ...patches[example.id] };
    assert.deepEqual(example.patch, patches[example.id]);
    assert.deepEqual(example.assumptions, assumptions);
    const final = runModel(assumptions).summary.final;
    near(example.revenueUsd, final.retainedRevenueUsd);
    near(example.acquiredNear, final.purchasedNear);
    near(example.coverage, final.purchaseCoverage);
    near(example.netSupplyGrowth, final.netSupplyGrowth);
    near(example.holderSupplyShareIndex, final.nonStakerSupplyShareIndex);
    near(example.holderMarkedValueIndex, final.nonStakerMarkedValueIndex);
  }
  const table = renderToStaticMarkup(createElement(FixedScenarioComparison));
  assert.ok(render({ ...RESEARCH_BASELINE }).includes(table));
  assert.ok(render({ ...RESEARCH_BASELINE, volumeGrowth: 0.2, priceMode: 'multiple' }).includes(table), 'Reader changes must not rewrite the authored comparison.');
});

test('the reader follows the live scenarios before the publisher comparison and its figures', () => {
  const html = render({ ...RESEARCH_BASELINE });
  assert.ok(html.indexOf('Annual acquisition capacity in Year 5') < html.indexOf('Testing the published bull case'));
  assert.ok(html.indexOf('full-model-audit') < html.indexOf('What comparables can establish'));
  assert.match(html, /NEAR acquired in Year 5/);
  assert.match(html, /Year 5 purchases \/ issuance/);
  assert.doesNotMatch(html, /Write total token capitalization|Under this conditional attribution/);
  assert.doesNotMatch(html.replace(/<[^>]*>/g, " "), /\bAppFees\b|\bANY_INPUT\b|economically paid execution|normalized margin|consolidated cost boundary/i);
  const definition = html.indexOf('Let P denote token price');
  const equation = html.indexOf('aria-label="The terminal price ratio');
  assert.ok(definition >= 0 && equation > definition);
});


test('every article exhibit link resolves to a published CSV with a short download name', () => {
  const snapshot = fallbackSnapshot();
  snapshot.review.sources.forEach(source => { source.state = 'unchanged'; });
  const html = renderToStaticMarkup(createElement(LiveDataProvider, { snapshot, children: createElement(App) }));
  const links = [...html.matchAll(/href="(\/data\/[^"?]+\.csv)" download="([^"]+)"/g)];
  assert.ok(links.length > 20);
  for (const [, href, filename] of links) {
    assert.ok(read('web/public' + href).length > 0, `Missing exhibit: ${href}`);
    assert.match(filename, /^[a-z0-9]+(?:-[a-z0-9]+){0,2}\.csv$/);
  }
});
