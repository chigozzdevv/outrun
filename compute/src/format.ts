export const usd = (n: number, digits = 1) => '$' + compact(n, digits);
export function compact(n: number, digits = 1): string {
  const a = Math.abs(n);
  const scale = a >= 1e9 ? 1e9 : a >= 1e6 ? 1e6 : a >= 1e3 ? 1e3 : 1;
  const suffix = scale === 1e9 ? 'bn' : scale === 1e6 ? 'm' : scale === 1e3 ? 'k' : '';
  return (n / scale).toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: scale > 1 ? digits : 0 }) + suffix;
}
export const pct = (n: number | null, digits = 1) => n === null ? 'Not defined' : (n * 100).toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: digits }) + '%';
export const signed = (n: number, format: (v: number) => string = compact) => (n > 0 ? '+' : n < 0 ? '−' : '') + format(Math.abs(n));
export const money = (n: number) => '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const shortDate = (date: string) => new Date(date.slice(0, 10) + 'T12:00:00Z').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
export const fullNumber = (n: number, digits = 2) => n.toLocaleString('en-US', { maximumFractionDigits: digits });
