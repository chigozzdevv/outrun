import { validateAssumptions, type ModelAssumptions, type ModelResult } from './model';

export const SCENARIO_VERSION = '2026-10-26.1';
export function encodeScenario(a: ModelAssumptions): string {
  return btoa(JSON.stringify({ version: SCENARIO_VERSION, assumptions: a })).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
export function decodeScenario(value: string, baseline: ModelAssumptions): ModelAssumptions | null {
  if (value.length > 12000) return null;
  try {
    const decoded = JSON.parse(atob(value.replace(/-/g, '+').replace(/_/g, '/')));
    if (decoded.version !== SCENARIO_VERSION || !decoded.assumptions || typeof decoded.assumptions !== 'object') return null;
    const result = { ...baseline };
    for (const key of Object.keys(baseline) as (keyof ModelAssumptions)[]) {
      const v: unknown = decoded.assumptions[key];
      if (key === 'priceMode') {
        if (v !== 'fixed' && v !== 'growth' && v !== 'multiple') return null;
        result.priceMode = v;
      } else {
        if (typeof v !== 'number' || !Number.isFinite(v)) return null;
        (result as unknown as Record<string, unknown>)[key] = v;
      }
    }
    return validateAssumptions(result).length ? null : result;
  } catch { return null; }
}

export interface AssumptionCommand { patch: Partial<ModelAssumptions>; explanation: string }
export function parseAssumptionCommand(text: string): AssumptionCommand | null {
  const s = text.toLowerCase().trim();
  if (/\?|^(?:is|are|can|could|does|do|will|would|how|what|when|where|which)\b/.test(s)) return null;
  if (/\b(?:why|should|recommend|fair|undervalued?|overvalued?|target|forecast|research|prove)\b/.test(s)) return null;
  const repeatedFields = [
    /\b(?:volume\s+growth|intents?\s+(?:growth|grow\w*)|volume\s+grow\w*|growth)\b/g,
    /\b(?:price|near\s+at)\b/g,
    /\b(?:retained(?:\s+(?:fees?|rate))?|retention)\b/g,
    /\b(?:issuance|emissions?)\b/g,
    /\bburn(?:\s+share)?\b/g,
  ];
  if (repeatedFields.some(pattern => (s.match(pattern) || []).length > 1)) return null;
  const matches: AssumptionCommand[] = [];
  const growth = s.match(/(?:volume\s+growth|intents?\s+(?:growth|grow\w*)|volume\s+grow\w*|growth)(?:\s+(?:of|to|at|is|by|annually|annual|instead))*\s*(-?\d+(?:\.\d+)?)\s*%/);
  if (growth && !/revenue\s+growth/.test(s)) {
    const v = Number(growth[1]);
    if (v >= -50 && v <= 150) matches.push({ patch: { volumeGrowth: v / 100 }, explanation: `Annual Intents volume growth set to ${v}%. Fee terms and other inputs stay as selected.` });
  }
  const price = s.match(/(?:price(?:\s+(?:is|at|to|of|equals))?|near\s+at)\s*\$\s*(\d+(?:\.\d+)?)/);
  if (price && Number(price[1]) > 0 && Number(price[1]) <= 1000) matches.push({ patch: { initialPriceUsd: Number(price[1]), priceMode: 'fixed' }, explanation: `Execution price set to a fixed $${price[1]}. This is a scenario assumption.` });
  const retained = s.match(/(?:retained(?:\s+(?:fees?|rate))?|retention)(?:\s+(?:of|to|at|is))*\s*(\d+(?:\.\d+)?)\s*(?:bps|basis points)/);
  if (retained && Number(retained[1]) <= 100) matches.push({ patch: { retainedFeeBps: Number(retained[1]) }, explanation: `Retained fee assumption set to ${retained[1]} basis points of settled volume.` });
  const issuance = s.match(/(?:issuance|emissions?)(?:\s+(?:of|to|at|is|falls))*\s*(\d+(?:\.\d+)?)\s*%/);
  if (issuance && Number(issuance[1]) <= 10) matches.push({ patch: { issuanceRate: Number(issuance[1]) / 100 }, explanation: `Annual issuance set to ${issuance[1]}%. This changes the scenario, not the active protocol policy.` });
  const burn = s.match(/burn(?:\s+(?:share|of|to|at|is))*\s*(\d+(?:\.\d+)?)\s*%/);
  if (burn && Number(burn[1]) <= 100) matches.push({ patch: { burnShare: Number(burn[1]) / 100, stakeShare: 0 }, explanation: `${burn[1]}% of modeled acquisitions assigned to native burning; the remainder is held. Staked allocation reset to zero so disposition sums to 100%.` });
  return matches.length === 1 ? matches[0] : null;
}

export function downloadFile(name: string, text: string, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 500);
}
export function exportScenario(result: ModelResult, evidenceVersion: string, cutoff: string) {
  return JSON.stringify({ modelVersion: SCENARIO_VERSION, timeBasis: result.timeBasis, exportedAt: new Date().toISOString(), evidenceVersion, historicalCutoff: cutoff, warning: 'Conditional model, not a market forecast. Collections are not independently verified external operating revenue.', assumptions: result.assumptions, rows: result.rows, warnings: result.warnings }, null, 2);
}
export function scenarioCsv(result: ModelResult): string {
  const keys = Object.keys(result.rows[0]) as (keyof typeof result.rows[0])[];
  return [keys.join(','), ...result.rows.map(row => keys.map(k => row[k] ?? '').join(','))].join('\n');
}
