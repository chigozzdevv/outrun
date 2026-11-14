import { useLiveData } from '../live/provider';
import { ChartFrame } from '../charts/views';
import { pct, usd, compact, shortDate } from '../../../compute/src/format';
import { DAY } from '../../../compute/src/live/schema';
import { purchaseCapacityByWindow, underwritingInputs } from '../../../compute/src/models/inputs';
import { currentEconomics, requiredGrowth, terminalEconomics } from '../../../compute/src/models/underwriting';
import { ReturnEquation, RequiredGrowthEquation } from './equations';

function observationTime(asOf: string) {
  if (asOf.length === 10) return `${shortDate(asOf)} ${asOf.slice(0, 4)}`;
  return new Date(asOf).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'UTC' }) + ' UTC';
}

export function CurrentValuation() {
  const { snapshot } = useLiveData();
  const a = underwritingInputs(snapshot), e = currentEconomics(a);
  const hurdle = requiredGrowth(a, 5, 100, 1);
  const end = new Date(Date.parse(snapshot.operating.endExclusive) - DAY).toISOString().slice(0, 10);
  return <div className="thesis-prose current-valuation">
    <p>{hurdle ? <>{hurdle.growth < 0 ? <>Keeping NEAR’s price unchanged at the end of Year 5 permits settled volume to contract by <strong>{pct(Math.abs(hurdle.growth), 2)} a year</strong></> : <>Keeping NEAR’s price unchanged at the end of Year 5 requires <strong>{pct(hurdle.growth, 2)} compound annual growth in settled volume</strong></>} if the market values the entire token supply at 100× the annual fees Intents retains in that year.</> : 'With zero modeled fee revenue today, a compound growth rate for the next five years is undefined.'} The scenario holds the retained fee at {a.retainedFeeBps} basis points and assumes {pct(a.issuanceRate, 2)} annual supply growth with no native burns.</p>
    <p>The assumed fee rate converts the recent settlement pace into {usd(e.revenueUsd, 2)} in annual revenue before cash costs. That starting fee pool uses the observed average of {usd(a.dailyVolumeUsd)} a day over the 30 days ending {observationTime(end)}; it does not treat the value settled as revenue.</p>
    <p>The <a href="https://coins.llama.fi/prices/current/coingecko:near" target="_blank" rel="noreferrer">reference quote</a> was ${a.priceUsd.toFixed(3)} on <time dateTime={snapshot.market.asOf}>{observationTime(snapshot.market.asOf)}</time>. <a href={`https://nearblocks.io/blocks/${snapshot.chain.blockHeight}`} target="_blank" rel="noreferrer">NEAR’s total supply of {compact(a.supplyNear, 3)} tokens</a> was recorded on <time dateTime={snapshot.chain.asOf}>{observationTime(snapshot.chain.asOf)}</time>. Together they imply {usd(e.capitalizationUsd, 2)} in token capitalization. {e.revenueMultiple === null ? 'The starting revenue multiple is also undefined.' : `Attributing all of it to Intents gives a starting multiple of ${e.revenueMultiple.toFixed(1)}× modeled annual revenue.`}</p>
  </div>;
}

export function PurchaseCapacityComparison() {
  const { snapshot } = useLiveData();
  const inputs = underwritingInputs(snapshot);
  const { recent, trailing } = purchaseCapacityByWindow(snapshot);
  const constrained = currentEconomics({ ...inputs, cashAvailability: .7, purchaseAllocation: .5 });
  const end = new Date(Date.parse(snapshot.operating.endExclusive) - DAY).toISOString();
  const rows = [
    { basis: 'Latest 30 days, annualized', capacity: recent },
    { basis: 'Actual trailing 365 days', capacity: trailing },
  ];
  return <ChartFrame title="Annual NEAR purchase capacity" subtitle={`Settlement periods ending ${shortDate(end)} ${end.slice(0, 4)} UTC`}>
    <div className="table-scroll"><table aria-label="Modeled purchase capacity by settlement period">
      <thead><tr><th scope="col">Volume basis</th><th scope="col">Annual volume</th><th scope="col">NEAR purchasable</th><th scope="col">Share of annual issuance</th></tr></thead>
      <tbody>{rows.map(({basis, capacity}) => <tr key={basis}><th scope="row">{basis}</th>
        <td>{capacity ? usd(capacity.annualVolumeUsd, 2) : '—'}</td>
        <td>{capacity ? compact(capacity.acquiredNear, 2) : '—'}</td>
        <td>{capacity ? pct(capacity.purchaseCoverage, 2) : '—'}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="figure-note">The model assumes {inputs.retainedFeeBps} bps in retained fees, zero cash costs and full allocation to purchases at ${inputs.priceUsd.toFixed(3)} per NEAR. Annual gross issuance is modeled at {compact(recent.issuedNear, 2)} NEAR ({pct(inputs.issuanceRate, 2)} of supply). {constrained.purchaseCoverage !== null && `If 70% of revenue remains after costs and half of that cash funds purchases, coverage at the recent pace is ${pct(constrained.purchaseCoverage, 2)}.`} {trailing === null && 'The trailing year lacks a complete daily volume record.'}</p>
  </ChartFrame>;
}

export function ReverseUnderwriting() {
  const { snapshot } = useLiveData();
  const a = underwritingInputs(snapshot);
  const rows = [100, 60].flatMap(multiple => [1, 2].map(target => ({ multiple, target, result: requiredGrowth(a, 5, multiple, target) })));
  const sensitivity = [{growth: 0, bps: 5}, {growth: .2, bps: 5}, {growth: .5, bps: 5}, {growth: .5, bps: 3}].map(({growth, bps}) => ({
    growth, bps, ...terminalEconomics({...a, years: 5, volumeGrowth: growth, retainedRateGrowth: (bps / a.retainedFeeBps) ** (1 / 5) - 1, terminalMultiple: 100}),
  }));
  return <div className="thesis-prose">
    <p>Let P denote token price, R annual retained revenue, S total supply and m the capitalization-to-revenue multiple. Subscript 0 marks the starting point and T marks the final year. Since price equals the multiple times revenue divided by supply, the change in price over the period follows from three ratios.</p>
    <ReturnEquation/>
    <p>With positive starting revenue, an unchanged multiple and a constant fee rate, settled volume need only grow as fast as supply to keep the price flat. If investors pay a lower multiple in Year 5, Intents must also earn enough to offset that decline. Any value NEAR derives from its use as money or collateral would require a separate estimate.</p>
    <p>Working backward, let g be the annual volume growth needed over T years, r the annual supply-growth rate and h the target price divided by the starting price. An unchanged price gives h = 1, and a doubling gives h = 2. The equation assumes a constant retained fee rate.</p>
    <RequiredGrowthEquation/>
    <ChartFrame title="What five years must deliver" subtitle="Constant 5 bps retained rate · no burns · full capitalization attributed to Intents">
      <div className="table-scroll"><table aria-label="Required five-year volume growth"><thead><tr><th>Terminal multiple</th><th>Price outcome</th><th>Required daily volume</th><th>Volume CAGR</th></tr></thead><tbody>{rows.map(({multiple,target,result}) => <tr key={`${multiple}-${target}`}><td>{multiple}×</td><td>{target === 1 ? 'Unchanged' : '2× reference price'}</td><td>{result ? usd(result.requiredDailyVolumeUsd,2) : 'Not defined'}</td><td>{result ? pct(result.growth,2) : 'Not defined'}</td></tr>)}</tbody></table></div>
    </ChartFrame>
    <p>The 60× and 100× multiples test the growth required under chosen valuations of revenue before cash costs. The treasury evidence does not establish either multiple as fair. At a given revenue, supply and multiple, the equation assigns the same price even if the treasury spends nothing on NEAR. A case supported by recurring token purchases therefore also requires cash remaining after costs and a treasury allocation to those purchases.</p>
    <ChartFrame title="Growth, fee retention and the price outcome" subtitle="Year 5 capitalization equals 100× annual retained revenue. Price returns exclude staking rewards.">
      <div className="table-scroll"><table aria-label="Five-year growth and valuation sensitivity"><thead><tr><th>Volume CAGR / terminal fee rate</th><th>Year 5 revenue</th><th>Conditional price</th><th>Price return</th></tr></thead><tbody>{sensitivity.map(row =>
        <tr key={`${row.growth}-${row.bps}`}><td>{pct(row.growth,0)} / {row.bps} bps</td><td>{usd(row.revenueUsd,2)}</td><td>${row.priceUsd.toFixed(2)}</td><td>{row.priceReturn > 0 ? '+' : ''}{pct(row.priceReturn,1)}</td></tr>
      )}</tbody></table></div>
    </ChartFrame>
    <p>The return case is sensitive to retained fees as well as settlement growth. With flat settlement and a constant fee rate, Year 5 revenue stays at its starting level; at 100× that revenue, the model gives a price of ${sensitivity[0].priceUsd.toFixed(2)} against a reference quote of ${a.priceUsd.toFixed(2)}. In the two 50% growth cases, lowering the Year 5 retained fee from 5 to 3 basis points cuts revenue and the implied price by 40%{a.dailyVolumeUsd > 0 ? '.' : ' when starting revenue is positive.'} Sustaining the fee rate is therefore a substantive part of the return case.</p>
  </div>;
}
