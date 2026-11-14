import { Fragment, type ReactNode } from 'react';
import thesis from './article.json';
import audit from '../../../data/evidence/2026-11-02/baselines/publisher.json';
import { ChartFrame, COLORS, FeeMultiples, ObservedComparison } from '../charts/views';
import { TreasuryEvidence } from '../evidence/treasury';
import { comparisonLabel } from '../live/metrics';
import { compact, pct, usd } from '../../../compute/src/format';
import { useLiveData } from '../live/provider';
import { reviewCurrent } from '../../../compute/src/live/snapshot';
import { scenarioAssumptions, scenarioExamples } from '../../../compute/src/models/inputs';
import { thesisFacts } from './facts';
import { AcquisitionEquation, CoverageEquation, FundingTraceEquation, HolderEquation, RevenueEquation, SupplyEquation } from '../components/equations';
import { TreasuryReconciliation } from '../evidence/balances';
import { InterfaceRouting } from '../evidence/distribution';
import { downloadName, downloadUrl } from './downloads';
import { CollectorFlowAccounting, NativeInflowOrigins, PaidAccountRecurrence, SwapFundedTreasury, VerifiedTreasuryPayments } from '../evidence/onchain';

const equations = { revenue: RevenueEquation, acquisition: AcquisitionEquation, supply: SupplyEquation, coverage: CoverageEquation, holder: HolderEquation, fundingTrace: FundingTraceEquation };
const evidenceFigures = {
  'native-inflow-origins': NativeInflowOrigins,
  'collector-flow': CollectorFlowAccounting,
  'swap-funded-treasury': SwapFundedTreasury,
  'treasury-payments': VerifiedTreasuryPayments,
  'paid-account-recurrence': PaidAccountRecurrence,
  'treasury-reconciliation': TreasuryReconciliation,
  'routing-concentration': InterfaceRouting,
  'published-model': PublishedModelAudit,
  'recent-receipts': RecentReceipts,
};

/** The latest 30-day fee table and the two-period comparison, shown under their own subheading. */
function RecentReceipts() {
  const { snapshot } = useLiveData();
  const { operating } = snapshot;
  const [prior, latest] = operating.periods;
  return <>
    <TreasuryEvidence/>
    <ChartFrame title="Volume, collections and NEAR receipts" subtitle={`${comparisonLabel(snapshot)} · UTC${operating.reporting ? ` · At least ${operating.reporting.bufferDays} days are allowed for reporting` : ''}`}>
      <ObservedComparison earlier={prior} later={latest}/>
    </ChartFrame>
  </>;
}

export type ThesisSection = keyof typeof thesis.chapters;
type Paragraph = string | { text: string; historical?: string; requiresReview?: string[]; links?: { phrase: string; url: string }[] };

function inline(text: string): ReactNode[] {
  return text.split(/(\[[^\]]+\]\([^)]+\)|\*\*.+?\*\*|`[^`]+`)/g).filter(Boolean).map((token, i) => {
    if (token.length > 2 && token.startsWith('`') && token.endsWith('`')) return <code key={i}>{token.slice(1, -1)}</code>;
    const link = token.match(/^\[([^\]]+)\]\((https:\/\/[^)]+)\)$/);
    if (link) return <a key={i} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
    const exhibit = token.match(/^\[([^\]]+)\]\((\/(?:data\/(?:[\w-]+\/)*[\w-]+\.csv|api\/thesis\/(?:daily|native)\.csv\?snapshot=[\w%-]+))\)$/);
    if (exhibit) return <a key={i} href={downloadUrl(exhibit[2])} download={downloadName(exhibit[2])}>{exhibit[1]}</a>;
    if (token.startsWith('**') && token.endsWith('**')) return <strong key={i}>{token.slice(2,-2)}</strong>;
    return token;
  });
}

export function ThesisText({ section }: { section: ThesisSection }) {
  const { snapshot } = useLiveData();
  const facts = thesisFacts(snapshot);
  const resolveText = (paragraph: Paragraph) => {
    // A dated observation can survive a policy change without becoming a current claim.
    const text = typeof paragraph === 'string' ? paragraph :
      reviewCurrent(snapshot, paragraph.requiresReview ?? []) ? paragraph.text : paragraph.historical;
    if (!text) return null;
    let resolved = text.replace(/\{\{(\w+)\}\}/g, (_, key: string) => {
      if (!(key in facts)) throw Error(`Missing thesis fact: ${key}`);
      return facts[key];
    });
    if (typeof paragraph !== 'string') for (const link of paragraph.links ?? []) {
      const phrase = link.phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      resolved = resolved.replace(new RegExp(phrase, 'i'), match => `[${match}](${link.url})`);
    }
    return resolved.trim() || null;
  };
  return <div className="thesis-prose" data-authored-section={section}>{thesis.chapters[section].map((block, index) => {
    const names = 'equations' in block ? block.equations ?? [] : [];
    const after = 'equationAfter' in block && block.equationAfter !== undefined ? block.equationAfter : block.paragraphs.length - 1;
    const paragraphs = block.paragraphs.map(resolveText);
    if (paragraphs.every(p => p === null)) return null;
    const Figure = 'figureAfter' in block ? evidenceFigures[block.figureAfter as keyof typeof evidenceFigures] : undefined;
    return <Fragment key={block.heading ?? index}><div className="thesis-text-block">{block.heading && <h3>{block.heading}</h3>}{paragraphs.map((p, i) => p === null ? null : <Fragment key={i}><p>{inline(p)}</p>{('equationsAfter' in block ? (block.equationsAfter as Record<string, string[]>)[i] ?? [] : i === after ? names : []).map(name => {
      const Equation = equations[name as keyof typeof equations];
      if (!Equation) throw Error(`Unknown thesis equation: ${name}`);
      return <Equation key={name}/>;
    })}</Fragment>)}</div>{Figure && <Figure/>}{'figureAfter' in block && block.figureAfter === 'acquisition-scenarios' && <div id="scenario"><ThesisText section="scenario"/><FixedScenarioComparison/></div>}</Fragment>;
  })}</div>;
}

export function PublishedModelAudit() {
  const d = audit.denominator;
  return <div className="full-model-audit">
    <ChartFrame title="Market cap / annualized fees" subtitle="Historical SVRN inputs">
      <FeeMultiples rows={[
        {label:'Gross fees',rate:'19 bps',revenue:d.annual_gross_fees_usd,multiple:d.market_cap_to_gross_fees,color:COLORS.muted},
        {label:'Modeled retained fees',rate:'6.101 bps',revenue:d.annual_net_fees_at_published_model_rate_usd,multiple:d.market_cap_to_modelled_net_capture,color:COLORS.accent},
      ]}/>
    </ChartFrame>
    <ChartFrame title="SVRN’s purchase coverage scenarios" subtitle={<><a href="/data/valuation/2026-10-28/published-model.csv" download="published-model.csv">Published assumptions</a> · 32.2m NEAR annual issuance · gas burn excluded</>}>
      <div className="table-scroll"><table><thead><tr><th>Year</th><th>Daily volume</th><th>Annual retained fees</th><th>SVRN price range</th><th>Purchase / issuance</th></tr></thead><tbody>{audit.publishedScenarios.map(r => <tr key={r.year}><td>{r.year}</td><td>{usd(r.published_base_case_daily_volume_usd)}</td><td>{usd(r.reconstructed_annual_net_fees_usd)}</td><td>${r.published_base_price_low}–${r.published_base_price_high}</td><td>{r.purchase_coverage_pct_at_high_price.toFixed(1)}–{r.purchase_coverage_pct_at_low_price.toFixed(1)}%</td></tr>)}</tbody></table></div>
    </ChartFrame>
  </div>;
}

export function FixedScenarioComparison() {
  const { snapshot } = useLiveData();
  const starting = scenarioAssumptions(snapshot);
  const examples = scenarioExamples(snapshot);
  return <ChartFrame title="Annual acquisition capacity in Year 5" subtitle={`Each case retains ${starting.retainedFeeBps} bps of settled volume and spends those fees on NEAR, assuming zero cash costs. Supply grows ${pct(starting.issuanceRate, 2)} a year for five years with no native burns.`}>
    <div className="table-scroll"><table><thead><tr><th>Assumptions</th><th>Year 5 revenue</th><th>NEAR acquired in Year 5</th><th>Year 5 purchases / issuance</th></tr></thead><tbody>{examples.map(({ id, label, model }) => <tr key={id}><td>{label}</td><td>{model ? usd(model.summary.final.retainedRevenueUsd) : 'Not defined'}</td><td>{model ? compact(model.summary.final.purchasedNear,2) : 'Not defined'}</td><td>{model ? pct(model.summary.final.purchaseCoverage,2) : 'Not defined'}</td></tr>)}</tbody></table></div>
  </ChartFrame>;
}
