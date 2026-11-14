import assert from 'node:assert/strict';
import test from 'node:test';
import { init } from '../src/charts/runtime.ts';
import { COLORS, comparisonOptions, lineOptions, multipleOptions } from '../src/charts/options.ts';
import { fallbackSnapshot } from '../../compute/src/live/snapshot.ts';
import { scenarioAssumptions } from '../../compute/src/models/inputs.ts';
import { runModel } from '../../compute/src/models/model.ts';
import { usd } from '../../compute/src/format.ts';
import audit from '../../data/evidence/2026-11-02/baselines/publisher.json' with { type: 'json' };

test('the ECharts renderer updates holder outcomes from the same snapshot without altering evidence', () => {
  const snapshot = fallbackSnapshot(), original = structuredClone(snapshot);
  const holderOption = () => {
    const model = runModel(scenarioAssumptions(snapshot));
    return lineOptions({ labels: ['Start', ...model.rows.map(row => `Year ${row.year}`)], yLabel: 'Holder supply-share index', reference: 100, includeZero: false, series: [{ name: 'Staked holder', color: COLORS.green, values: [100, ...model.rows.map(row => row.stakerSupplyShareIndex)] }] });
  };
  const chart = init(null, undefined, { renderer: 'svg', ssr: true, width: 760, height: 300 });
  try {
    chart.setOption({ ...holderOption(), animation: false });
    const before = chart.renderToSVGString();
    assert.match(before, /<svg/);
    assert.match(before, /<path/);
    assert.deepEqual(snapshot, original, 'Rendering cannot mutate the shared snapshot.');
    snapshot.chain.issuanceRate = 0;
    const next = holderOption();
    chart.setOption({ ...next, animation: false }, { replaceMerge: ['series'] });
    const after = chart.renderToSVGString();
    assert.notEqual(after, before, 'The existing chart instance must reflect updated model inputs.');
    const series = chart.getOption().series as { data: number[] }[];
    assert.ok(series[0].data.every(value => value === 100));
    assert.equal(/NaN|Infinity/.test(after), false);
  } finally { chart.dispose(); }
});

test('unmeasured native history stays disconnected without changing the source values', () => {
  const snapshot = fallbackSnapshot(), rows = snapshot.operating.daily;
  const values = rows.map(row => row.nativeReceiptsNear);
  values[0] = null;
  const original = [...values];
  const option = lineOptions({ labels: rows.map(row => row.date), yLabel: 'Native receipts (NEAR / day)', series: [{ name: 'Native receipts', color: COLORS.orange, values }] });
  const series = option.series as { data: (number | null)[]; connectNulls: boolean }[];
  assert.deepEqual(series[0].data, original);
  assert.equal(series[0].connectNulls, false);
  assert.notEqual(series[0].data, values, 'The chart must own its input array.');
  const axis = option.xAxis as { axisLabel: { interval: (index: number) => boolean } };
  assert.equal(rows.filter((_, index) => axis.axisLabel.interval(index)).length, 4);
  assert.equal(axis.axisLabel.interval(0), true);
  assert.equal(axis.axisLabel.interval(rows.length - 1), true);
  assert.deepEqual(values, original);
  const moneyAxis = lineOptions({ labels: ['Day 1'], yLabel: 'USD', format: usd, series: [{ name: 'Collections', color: COLORS.green, values: [200000000] }] }).yAxis as { axisLabel: { formatter: (value: number, index: number) => string } };
  assert.equal(moneyAxis.axisLabel.formatter(200000000, 3), '$200.0m', 'ECharts tick indices must not become decimal precision.');
});

test('window bars retain raw quantities and render a zero starting window without division', () => {
  const chart = init(null, undefined, { renderer: 'svg', ssr: true, width: 320, height: 68 });
  try {
    chart.setOption({ ...comparisonOptions(0, 25, COLORS.green, usd), animation: false });
    const series = chart.getOption().series as { data: { value: number }[] }[];
    assert.deepEqual(series[0].data.map(row => row.value), [0, 25]);
    const svg = chart.renderToSVGString();
    assert.match(svg, /\$25/);
    assert.equal(/NaN|Infinity/.test(svg), false);
  } finally { chart.dispose(); }
});

test('fee-multiple bar lengths represent multiples rather than their reciprocal revenue denominators', () => {
  const d = audit.denominator;
  const option = multipleOptions([
    { label: 'Gross ecosystem fees', rate: '19 bps', revenue: d.annual_gross_fees_usd, multiple: d.market_cap_to_gross_fees, color: COLORS.muted },
    { label: 'Modeled retained fees', rate: '6.101 bps', revenue: d.annual_net_fees_at_published_model_rate_usd, multiple: d.market_cap_to_modelled_net_capture, color: COLORS.accent },
  ]);
  const series = option.series as { data: { value: number }[] }[];
  assert.equal(series[0].data[0].value, d.market_cap_to_gross_fees);
  assert.equal(series[0].data[1].value, d.market_cap_to_modelled_net_capture);
  assert.ok(series[0].data[1].value > series[0].data[0].value);
});
