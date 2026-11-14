import { useMemo, useState, type ReactNode } from 'react';
import { compact, fullNumber, pct, shortDate, usd } from '../../../compute/src/format';
import { change } from '../live/metrics';
import { COLORS, comparisonOptions, lineOptions, multipleOptions, type LineInput, type MultipleRow } from './options';
import { EChart } from './chart';

export { COLORS } from './options';
export type { Series } from './options';

export function ChartFrame({ title, subtitle, children, className = '' }: { title: string; subtitle?: ReactNode; children: ReactNode; className?: string }) {
  return <figure className={`chart-frame ${className}`}><figcaption><h3>{title}</h3>{subtitle && <p>{subtitle}</p>}</figcaption>{children}</figure>;
}

export function LineChart({ height = 300, ...input }: LineInput & { height?: number }) {
  const { labels, series, yLabel, format = compact } = input;
  const option = useMemo(() => lineOptions(input), [labels, series, yLabel, format, input.tooltipFormat, input.reference, input.includeZero]);
  return <EChart option={option} label={`${yLabel} over ${labels.length} observations`} height={height} table={<table><caption>{yLabel}</caption><thead><tr><th>Observation</th>{series.map(s => <th key={s.name}>{s.name}</th>)}</tr></thead><tbody>{labels.map((label, i) => <tr key={i}><th>{label}</th>{series.map(s => <td key={s.name}>{s.values[i] === null ? 'Not covered' : format(s.values[i]!)}</td>)}</tr>)}</tbody></table>}/>;
}

export function ObservedComparison({ earlier, later }: { earlier: { volumeUsd: number; collectionsUsd: number; nativeReceiptsNear: number }; later: typeof earlier }) {
  const values = [
    { label: 'Settled volume', a: earlier.volumeUsd, b: later.volumeUsd, color: COLORS.green, format: usd },
    { label: 'Dollar-valued collections', a: earlier.collectionsUsd, b: later.collectionsUsd, color: COLORS.accent, format: usd },
    { label: 'Native wallet receipts', a: earlier.nativeReceiptsNear, b: later.nativeReceiptsNear, color: COLORS.orange, format: (n: number) => compact(n, 1) + ' NEAR' },
  ];
  return <div className="observed-comparison">{values.map(d => {
    const delta = change(d.b, d.a);
    return <div className="comparison-row" key={d.label}>
      <div className="comparison-label"><span>{d.label}</span><strong style={{ color: d.color }}>{delta === null ? '—' : `${d.b > d.a ? '+' : d.b < d.a ? '−' : ''}${pct(Math.abs(delta))}`}</strong></div>
      <EChart option={comparisonOptions(d.a, d.b, d.color, d.format)} label={`${d.label}, independently scaled: ${d.format(d.a)} earlier, ${d.format(d.b)} later`} height={60} table={<table><caption>{d.label}</caption><thead><tr><th>Window</th><th>Value</th></tr></thead><tbody><tr><th>Earlier</th><td>{d.format(d.a)}</td></tr><tr><th>Later</th><td>{d.format(d.b)}</td></tr></tbody></table>}/>
    </div>;
  })}</div>;
}

export function FeeMultiples({ rows }: { rows: MultipleRow[] }) {
  return <div className="fee-multiples"><div className="multiple-labels" aria-hidden="true">{rows.map(row => <div key={row.label}>{row.label}<small>{row.rate} · {usd(row.revenue)} annualized</small></div>)}</div><EChart option={multipleOptions(rows)} label="Market capitalization divided by annualized fees" height={128} table={<table><caption>Market capitalization / annualized fees</caption><thead><tr><th>Fee measure</th><th>Rate</th><th>Annualized fees</th><th>Multiple</th></tr></thead><tbody>{rows.map(row => <tr key={row.label}><th>{row.label}</th><td>{row.rate}</td><td>{usd(row.revenue)}</td><td>{row.multiple.toFixed(2)}×</td></tr>)}</tbody></table>}/></div>;
}

export function DailyHistory({ daily, nativeCoverage }: { daily: { date: string; volumeUsd: number | null; collectionsUsd: number | null; nativeReceiptsNear: number | null }[]; nativeCoverage: { start: string; end: string } }) {
  const [metric, setMetric] = useState<'volumeUsd' | 'collectionsUsd' | 'nativeReceiptsNear'>('volumeUsd');
  const [days, setDays] = useState<number | null>(90);
  const rows = days === null ? daily : daily.slice(-days);
  const native = metric === 'nativeReceiptsNear';
  const title = native ? 'Daily NEAR receipts' : metric === 'volumeUsd' ? 'Daily settlement volume' : 'Daily dollar-valued collections';
  const name = native ? 'Native receipts' : metric === 'volumeUsd' ? 'Settled volume' : 'Dollar-valued collections';
  const end = daily.at(-1)!.date;
  const labels = rows.map(d => new Date(d.date + 'T12:00:00Z').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: '2-digit', timeZone: 'UTC' }));
  return <ChartFrame title={title} subtitle={`Through ${shortDate(end)} ${end.slice(0, 4)} · UTC`}>
    <div className="figure-controls">
      <label>Measure<select aria-label="History measure" value={metric} onChange={e => setMetric(e.target.value as typeof metric)}><option value="volumeUsd">Settlement volume (USD)</option><option value="collectionsUsd">Collections (USD value)</option><option value="nativeReceiptsNear">Native receipts (NEAR)</option></select></label>
      <label>Period<select aria-label="History period" value={days ?? 'all'} onChange={e => setDays(e.target.value === 'all' ? null : Number(e.target.value))}><option value={30}>30 days</option><option value={90}>90 days</option><option value="all">All available</option></select></label>
    </div>
    <LineChart labels={labels} yLabel={native ? 'NEAR / day' : 'USD / day'} series={[{ name, color: native ? COLORS.orange : metric === 'collectionsUsd' ? COLORS.accent : COLORS.green, values: rows.map(d => d[metric]) }]} format={native ? compact : usd} tooltipFormat={n => native ? `${fullNumber(n, 2)} NEAR` : `$${fullNumber(n, 0)}`}/>
    {native && <p className="figure-note">Native coverage: {shortDate(nativeCoverage.start)}–{shortDate(nativeCoverage.end)} {nativeCoverage.end.slice(0,4)}. Earlier dates are unmeasured.</p>}
  </ChartFrame>;
}
