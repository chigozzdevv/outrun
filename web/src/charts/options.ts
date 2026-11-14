import type { EChartsOption } from 'echarts';
import { compact } from '../../../compute/src/format';

export const COLORS = { accent: '#111111', green: '#426b52', orange: '#c66c42', ink: '#263d35', muted: '#a5aaa0' };
export interface Series { name: string; color: string; values: (number | null)[]; dashed?: boolean }
export interface LineInput { series: Series[]; labels: string[]; yLabel: string; format?: (n: number) => string; tooltipFormat?: (n: number) => string; reference?: number; includeZero?: boolean }
const fontFamily = 'Inter, Helvetica Neue, Arial, sans-serif';
const base: EChartsOption = {
  animationDuration: 1000, animationDurationUpdate: 400, animationEasing: 'cubicOut',
  textStyle: { fontFamily, color: COLORS.ink, fontSize: 11 },
  tooltip: { trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: '#a5aaa0', type: 'dashed' } }, renderMode: 'richText', confine: true, backgroundColor: '#f7f5ee', borderColor: '#dcded3', textStyle: { fontFamily, color: COLORS.ink, fontSize: 11 } },
};

export function lineOptions({ series, labels, yLabel, format = compact, tooltipFormat = format, reference, includeZero = true }: LineInput): EChartsOption {
  const measured = series.flatMap(s => s.values.filter((v): v is number => v !== null && Number.isFinite(v)));
  const minimum = Math.min(...measured, reference ?? Infinity);
  const dateTicks = labels.length > 15 ? new Set([0, Math.round((labels.length - 1) / 3), Math.round(2 * (labels.length - 1) / 3), labels.length - 1]) : null;
  return {
    ...base,
    aria: { enabled: true, label: { description: `${series.map(s => s.name).join(' and ')}. ${yLabel}. ${labels.length} observations. Missing observations are gaps. Data follows in a table.` } },
    grid: { left: 0, right: 10, top: series.length > 1 ? 54 : 32, bottom: 8, outerBoundsMode: 'same', outerBoundsContain: 'axisLabel' },
    legend: { show: series.length > 1, top: 0, left: 0, itemWidth: 18, itemHeight: 3, icon: 'roundRect', textStyle: { fontFamily, color: COLORS.ink, fontSize: 11 }, data: series.map(s => s.name) },
    xAxis: { type: 'category', boundaryGap: false, data: [...labels], axisLine: { lineStyle: { color: '#dcded3' } }, axisTick: { show: false }, axisLabel: { color: '#6e776d', fontSize: 11, interval: index => dateTicks ? dateTicks.has(index) : true, hideOverlap: true, showMinLabel: true, showMaxLabel: true } },
    yAxis: { type: 'value', name: yLabel, nameLocation: 'end', nameTextStyle: { align: 'left', color: '#6e776d', fontSize: 11 }, nameGap: 16, scale: !includeZero, min: includeZero ? undefined : Number.isFinite(minimum) ? Math.floor(minimum * .98) : undefined, axisLabel: { color: '#6e776d', fontSize: 11, formatter: value => format(value) }, splitNumber: 4, splitLine: { lineStyle: { color: '#e1e3d9' } } },
    tooltip: { ...base.tooltip, valueFormatter: value => typeof value === 'number' ? tooltipFormat(value) : 'Not covered' },
    series: series.map((s, i) => ({
      id: s.name, name: s.name, type: 'line',
      data: s.values.map(v => v !== null && Number.isFinite(v) ? v : null),
      connectNulls: false, showSymbol: labels.length < 15, symbolSize: 5,
      itemStyle: { color: s.color }, lineStyle: { color: s.color, width: s.dashed ? 1.8 : 2.6, type: s.dashed ? 'dashed' : 'solid' },
      emphasis: { focus: 'series' },
      markLine: i === 0 && reference !== undefined ? { silent: true, symbol: 'none', lineStyle: { color: '#a5aaa0', type: 'dashed' }, label: { show: false }, data: [{ yAxis: reference }] } : undefined,
    })),
  };
}

export function comparisonOptions(earlier: number, later: number, color: string, format: (n: number) => string): EChartsOption {
  return {
    ...base,
    grid: { left: 52, right: 92, top: 0, bottom: 0 },
    tooltip: { ...base.tooltip, valueFormatter: value => typeof value === 'number' ? format(value) : 'Not covered' },
    xAxis: { type: 'value', show: false, min: 0, max: Math.max(earlier, later, 1) },
    yAxis: { type: 'category', inverse: true, data: ['Earlier', 'Later'], axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: '#6e776d', fontSize: 11 } },
    series: [{ id: 'windows', type: 'bar', barWidth: 14, label: { show: true, position: 'right', distance: 8, color: COLORS.ink, opacity: 1, fontSize: 11, formatter: p => format(Number(p.value)) }, data: [{ name: 'Earlier', value: earlier, itemStyle: { color, opacity: .28 } }, { name: 'Later', value: later, itemStyle: { color } }] }],
  };
}

export interface MultipleRow { label: string; rate: string; revenue: number; multiple: number; color: string }
export function multipleOptions(rows: MultipleRow[]): EChartsOption {
  return {
    ...base, grid: { left: 0, right: 62, top: 0, bottom: 0 },
    xAxis: { type: 'value', show: false, min: 0 },
    yAxis: { type: 'category', inverse: true, show: false, data: rows.map(row => row.label) },
    tooltip: { ...base.tooltip, formatter: params => {
      const p = Array.isArray(params) ? params[0] : params;
      const row = rows[p.dataIndex];
      return `${row.label}\n${row.multiple.toFixed(2)}× market cap / annual fees\n${row.rate} · $${row.revenue.toLocaleString('en-US', { maximumFractionDigits: 0 })} annualized`;
    } },
    series: [{ id: 'fee-multiples', type: 'bar', barWidth: 22, label: { show: true, position: 'right', color: COLORS.ink, fontSize: 14, formatter: p => `${Number(p.value).toFixed(2)}×` }, data: rows.map(row => ({ name: row.label, value: row.multiple, itemStyle: { color: row.color } })) }],
  };
}
