import { pct, shortDate } from '../../../compute/src/format.ts';
import { DAY, type Period, type LiveSnapshot } from '../../../compute/src/live/schema.ts';

export function change(later: number, earlier: number): number | null { return earlier === 0 ? null : later / earlier - 1; }

export function movement(value: number | null, digits = 2): string {
  if (value === null) return 'cannot be compared with a zero starting value';
  if (Math.abs(value) < 0.5 * 10 ** (-digits - 2)) return 'changed by 0%';
  return `${value > 0 ? 'increased' : 'decreased'} ${pct(Math.abs(value), digits)}`;
}

export function periodLabel(p: Pick<Period, 'start' | 'endExclusive'>, year = true): string {
  const end = new Date(Date.parse(p.endExclusive) - DAY).toISOString().slice(0, 10);
  if (p.start.slice(0,4) !== end.slice(0,4)) return `${shortDate(p.start)} ${p.start.slice(0,4)}–${shortDate(end)} ${end.slice(0,4)}`;
  return `${shortDate(p.start)}–${shortDate(end)}${year ? ` ${end.slice(0, 4)}` : ''}`;
}

export function comparisonLabel(s: LiveSnapshot): string { return s.operating.periods.map(p => periodLabel(p)).join(' → '); }
