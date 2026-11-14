import { runForwardModel } from '../../../compute/src/models/model.ts';
import { fullNumber, money, pct, shortDate, usd } from '../../../compute/src/format.ts';
import { currentEconomics, requiredGrowth } from '../../../compute/src/models/underwriting.ts';
import { scenarioAssumptions, underwritingInputs } from '../../../compute/src/models/inputs.ts';
import { chainResearchFacts } from '../evidence/treasury-facts.ts';
import { atomicFloor, onchainResults } from '../evidence/onchain-results.ts';
import { interfaceRouting, routingResearchFacts } from '../evidence/routing-facts.ts';
import { DAY, WALLETS, type LiveSnapshot, type SupplyWindow } from '../../../compute/src/live/schema.ts';
import { change, movement, periodLabel, comparisonLabel } from '../live/metrics.ts';
import { snapshotDownload } from './downloads.ts';

export function thesisFacts(s: LiveSnapshot): Record<string, string> {
  const [a, b] = s.operating.periods;
  const [sa, sb] = s.operating.supply.windows;
  const intensityA = a.volumeUsd > 0 ? a.collectionsUsd / a.volumeUsd * 10_000 : null;
  const intensityB = b.volumeUsd > 0 ? b.collectionsUsd / b.volumeUsd * 10_000 : null;
  const usdChange = change(b.collectionsUsd, a.collectionsUsd);
  const nativeChange = change(b.nativeReceiptsNear, a.nativeReceiptsNear);
  const assumptions = scenarioAssumptions(s);
  const holder = runForwardModel(assumptions).summary.final;
  const holderStake = s.chain.activeValidatorStakeNear === undefined
    ? `The scenario assumes active validator stake equals ${pct(assumptions.stakingRatio,0)} of supply.`
    : `The [active validator set](https://nearblocks.io/blocks/${s.chain.blockHeight}) represented ${fullNumber(s.chain.activeValidatorStakeNear,0)} NEAR in stake, or ${pct(assumptions.stakingRatio,2)} of supply on ${shortDate(s.chain.asOf)} ${s.chain.asOf.slice(0,4)}. The model holds this share constant.`;
  const inputs = underwritingInputs(s);
  const e = currentEconomics(inputs);
  const flatPriceHurdle = requiredGrowth(inputs, 5, 100, 1);
  const long = s.operating.longPeriods;
  const longSummary = 'Fee income can keep funding buybacks only if customers keep coming back and partners keep sending trades on similar terms.' + (long ? ` Across the 90-day periods ${periodLabel(long[0])} and ${periodLabel(long[1])}, settled volume ${movement(change(long[1].volumeUsd, long[0].volumeUsd))} and dollar-valued collections ${movement(change(long[1].collectionsUsd, long[0].collectionsUsd))}. Those aggregates establish the scale of activity; the account and distribution evidence tests its recurrence.` : ' Account recurrence and distribution relationships help test that persistence, while retained fee income determines how much of it can fund purchases.');
  const burnShare = (w: SupplyWindow) => BigInt(w.grossMintedYocto) === 0n ? null : Number(w.nativeBurnDerivedYocto) / Number(w.grossMintedYocto);
  const earlierShare = burnShare(sa), laterShare = burnShare(sb);
  const ratioChange = intensityA === null || intensityB === null ? 'Zero settlement volume prevents a comparison of collections per dollar settled.' : `Collections per dollar settled ${intensityB === intensityA ? 'stayed at ' + intensityB.toFixed(2) + ' basis points' : (intensityB > intensityA ? 'rose' : 'fell') + ' from ' + intensityA.toFixed(2) + ' to ' + intensityB.toFixed(2) + ' basis points'}.`;
  const issuance = s.chain.issuanceRate;
  const operatingEnd = new Date(Date.parse(b.endExclusive) - DAY).toISOString();
  const walletNames: Record<string, string> = { [WALLETS[0]]: 'the buyback wallet', [WALLETS[1]]: 'the frontend treasury', [WALLETS[2]]: 'the 1Click treasury' };
  const priceDate = `${shortDate(s.market.asOf)} ${s.market.asOf.slice(0, 4)}`;
  const volumeChange = change(b.volumeUsd, a.volumeUsd);
  const trend = [volumeChange, usdChange, nativeChange];
  const trendLead = trend.every(value => value !== null && value < 0) ? 'The last 30 days went the wrong way.' : trend.every(value => value !== null && value > 0) ? 'The last 30 days improved.' : 'The last 30 days were mixed.';
  const coverage = e.purchaseCoverage;
  const growth = flatPriceHurdle?.growth ?? null;
  // HSP-029 outlines a 1.6% issuance target; the hurdle scales with issued tokens.
  const targetIssuance = 0.016;
  // The measured share comes from the fixed 23 Aug–28 Oct study; the best case uses the live 30-day pace.
  const measuredShare = `about ${Math.round(Number(onchainResults.expandedAcquisitions.minimumDeliveredShareOfGrossMintingPct))}%`;
  const studied = `We followed the money behind NEAR’s buybacks. From August to October they equaled ${measuredShare} of the new NEAR the network created`;
  // Year-on-year growth and fee capture use the full daily series; any missing day leaves the measure undefined.
  const lastDate = s.operating.daily.at(-1)?.date;
  const byDate = new Map(s.operating.daily.map(row => [row.date, row]));
  const windowSum = (days: number, offset: number, key: 'volumeUsd' | 'collectionsUsd') => {
    if (!lastDate) return null;
    let total = 0;
    for (let i = 0; i < days; i++) {
      const value = byDate.get(new Date(Date.parse(lastDate) - (offset + i) * DAY).toISOString().slice(0, 10))?.[key];
      if (value === null || value === undefined) return null;
      total += value;
    }
    return total;
  };
  const yearOnYear = (days: number) => {
    const now = windowSum(days, 0, 'volumeUsd'), before = windowSum(days, 365, 'volumeUsd');
    return now === null || before === null || before === 0 ? null : now / before - 1;
  };
  const capture = (days: number) => {
    const fees = windowSum(days, 0, 'collectionsUsd'), volume = windowSum(days, 0, 'volumeUsd');
    return fees === null || volume === null || volume === 0 ? null : fees / volume * 10_000;
  };
  const yoy30 = yearOnYear(30), yoy90 = yearOnYear(90);
  const captures: [number, number][] = [];
  for (const days of [180, 90, 30]) { const value = capture(days); if (value !== null) captures.push([days, value]); }
  const captureList = captures.map(([days, value], i) => `${value.toFixed(2)}${i === 0 ? ' basis points of settled volume' : ''} over the last ${days} days`).join(', ').replace(/, ([^,]*)$/, ' and $1');
  const higher = (value: number) => `${pct(Math.abs(value), 0)} ${value >= 0 ? 'higher' : 'lower'}`;
  const slowing = yoy30 !== null && yoy90 !== null && (yoy30 < yoy90 || (volumeChange !== null && volumeChange < 0));
  const growthLead = yoy30 === null || yoy90 === null ? null
    : yoy30 >= 0.2 ? (slowing ? 'Growth is strong but slowing.' : 'Growth is strong.')
    : yoy30 > 0 ? (slowing ? 'Growth is modest and slowing.' : 'Growth is modest.')
    : 'Volume is below last year’s level.';
  const monthOnMonth = `settled volume ${movement(volumeChange, 1)}, fee collections ${movement(usdChange, 1)} and NEAR received by the treasury wallets ${movement(nativeChange, 1)}`;
  const yearsToHurdle = yoy30 !== null && yoy30 > 0 && e.volumeHurdle !== null && e.volumeHurdle > 1 ? Math.log(e.volumeHurdle) / Math.log(1 + yoy30) : null;
  const control = onchainResults.treasuryControl.wallets['buybacks.multisignature.near'];
  const sharedSource = onchainResults.inflowOrigins.sharedSourceMatches;
  return {
    premise: issuance > 0
      ? `NEAR pays its validators by creating new tokens, up to ${pct(issuance, 1)} of supply a year, so a holder who does not stake owns a slightly smaller share each year. Part of the fees from NEAR Intents swaps goes to three collection accounts, called collectors below. They buy NEAR and send it to a buyback treasury. If those buybacks grow large enough, they offset the new NEAR. This page measures how close they are.`
      : 'NEAR is not creating new tokens at the moment, so there is no new supply for buybacks to offset. Part of the fees from NEAR Intents swaps goes to three collection accounts, called collectors below. They buy NEAR and send it to a buyback treasury. This page measures what those buybacks add up to and what NEAR’s price assumes about Intents’ growth.',
    heroAnswer: coverage === null
      ? `${studied}. With no new NEAR being issued now, the question is whether fee income can support NEAR’s price.`
      : coverage < 1 ? `Not yet. ${studied}, and even if every fee dollar went to buybacks, today’s volume would cover only about ${pct(coverage, 0)}.`
      : `At today’s volume, possibly. ${studied}, but if every fee dollar went to buybacks, today’s volume would cover about ${pct(coverage, 0)}.`,
    findingHurdle: issuance === 0 ? '**No issuance hurdle.** With issuance at zero, buybacks have no new NEAR to offset.'
      : e.requiredDailyVolumeUsd === null ? '**No volume would be enough.** Without a fee budget, purchases cannot match the new NEAR at any volume.'
      : e.volumeHurdle !== null && e.volumeHurdle <= 1 ? `**Recent volume clears the bar.** Matching the new NEAR takes about ${usd(e.requiredDailyVolumeUsd, 0)} of swaps a day under the same assumptions.`
      : `**Matching the new NEAR takes ${e.volumeHurdle === null ? 'far more volume' : `about ${Number(e.volumeHurdle.toFixed(1))} times today’s volume`}.** That is about ${usd(e.requiredDailyVolumeUsd, 0)} of swaps a day under the same assumptions.`,
    findingPrice: growth === null ? '**The price test needs fee income.** With no starting fee revenue, the growth needed to support NEAR’s price cannot be expressed as a yearly rate.'
      : growth >= 0 ? `**Today’s price already assumes growth.** To keep NEAR’s price flat for five years, swap volume must grow about ${pct(growth, 0)} a year, if the market then values all NEAR at 100 times Intents’ yearly fees.`
      : `**Today’s price leaves room.** NEAR’s price could stay flat for five years even if swap volume shrank about ${pct(Math.abs(growth), 0)} a year, if the market then values all NEAR at 100 times Intents’ yearly fees.`,
    findingTrend: growthLead === null
      ? `**${trendLead}** From ${periodLabel(a)} to ${periodLabel(b)}, ${monthOnMonth}.`
      : `**${growthLead}** Settled volume in the last 90 days was ${higher(yoy90!)} than in the same 90 days a year earlier, and the last 30 days were ${higher(yoy30!)}. ${yoy30! > 0 && volumeChange !== null && volumeChange < 0 ? 'But compared' : 'Compared'} with the 30 days before, ${monthOnMonth}.`,
    verdictLead: `**Our view:** ${coverage === null ? 'with no new NEAR being issued, the case rests on whether fee income can support NEAR’s price' : coverage < 0.5 ? 'the buyback route works, but at today’s volume it is far too small to offset the new NEAR' : coverage < 1 ? 'the buyback route works, but at today’s volume it does not yet offset the new NEAR' : 'the buyback route could keep pace with the new NEAR at today’s volume, if Intents keeps its assumed share of each swap'}${growth !== null && growth > 0 ? `, and today’s price already assumes about ${pct(growth, 0)} yearly volume growth for five years` : ''}.${growth !== null && growth > 0 && yoy90 !== null ? (yoy90 > growth ? ' Volume grew much faster than that over the past year, so the long-term case stays open. The buyback route alone does not yet make today’s price an attractive entry point.' : ' Volume grew more slowly than that over the past year, which weakens the long-term case.') : ''}`,
    caseForGrowth: yoy90 === null ? '' : yoy90 > 0
      ? `**Volume has grown fast.** Settled volume in the last 90 days was ${pct(yoy90, 0)} higher than in the same 90 days a year earlier${growth !== null && growth > 0 ? (yoy90 > growth ? `, well above the roughly ${pct(growth, 0)} a year that today’s price needs` : `, but below the roughly ${pct(growth, 0)} a year that today’s price needs`) : ''}.`
      : '',
    caseForFees: captures.length > 1 && captures.at(-1)![1] > captures[0][1]
      ? `**More of each swap is reaching the treasury.** Collections equaled ${captureList}.`
      : '',
    caseForRoute: `**The buyback route works.** On 23 August 2026, one collector turned ${onchainResults.funding.bridge.totalCreditEvents} swap payments received in USDT into NEAR and delivered at least ${atomicFloor(onchainResults.funding.chronology.identifiedSwapFundedNativeDeliveryRaw, 24, 1)} NEAR to the buyback treasury.`,
    caseForPartners: `**Partners keep sending trades.** From 27 October to 2 November, ${pct(interfaceRouting.cohort.returningShareOfLateVolume, 1)} of third-party volume came from apps that had already routed swaps during 4–26 October.`,
    caseAgainstSmall: coverage === null ? '' : `**Buybacks are small.** From August to October they equaled ${measuredShare} of the new NEAR created. Even in the best case, today’s fees would cover about ${pct(coverage, 0)}.`,
    caseAgainstSlowing: slowing
      ? `**Growth is slowing.** Volume was ${pct(yoy90!, 0)} higher than a year earlier over the last 90 days, but only ${pct(yoy30!, 0)} higher over the last 30. Compared with the 30 days before, settled volume ${movement(volumeChange, 1)}.`
      : volumeChange !== null && volumeChange < 0 ? `**Volume fell last month.** Compared with the 30 days before, settled volume ${movement(volumeChange, 1)}.` : '',
    caseAgainstControl: `**A few keys control the treasury.** ${control.requiredConfirmations} of ${control.accessKeys.length} keys can move NEAR out of the buyback wallet, and owning NEAR gives no vote over any of the three treasury wallets.`,
    cannotMeasureFee: `**Partners’ share of each fee.** Volume shares are public, but the fee each partner keeps is not, so the model assumes Intents keeps ${inputs.retainedFeeBps} basis points.`,
    cannotMeasureFunding: `**Who funded most of the other NEAR.** ${fullNumber(sharedSource.exactEarlierAmountOwnerMatches, 0)} payouts into the treasury wallets trace back to one account whose own funding is unknown.`,
    feeRateBasis: `Why ${inputs.retainedFeeBps} basis points? ${captures.length ? `Collections into the treasury wallets equaled ${captureList}. ` : ''}Collections are close to the fee Intents keeps but are not the same measure: they arrive later, are valued in dollars and can include other inflows. The model therefore treats ${inputs.retainedFeeBps} basis points as an assumption, and the valuation chapter also tests 3.`,
    checkpointVolume: issuance === 0 ? '**Volume.** With no new NEAR being issued, there is no supply-matching volume to reach.'
      : e.requiredDailyVolumeUsd === null ? '**Volume.** Without a fee budget, no volume level funds purchases that match the new NEAR.'
      : `**Volume.** Swap volume would need to hold above about ${usd(e.requiredDailyVolumeUsd, 0)} a day for fee purchases to match the new NEAR, at ${inputs.retainedFeeBps} basis points and the ${priceDate} price. The last 30 days averaged ${usd(inputs.dailyVolumeUsd, 0)} a day. ${yearsToHurdle === null ? '' : `If volume kept growing at its latest year-on-year rate (${pct(yoy30!, 0)}), it would reach that level in about ${Math.max(1, Math.round(yearsToHurdle))} years. `}A lower NEAR price lowers this bar; a higher price raises it.`,
    checkpointGrowth: growth === null ? '**Growth.** With no starting fee revenue, no growth rate can be computed for the price test.'
      : growth >= 0 ? `**Growth.** Volume would need to grow faster than about ${pct(growth, 0)} a year for five years for NEAR’s price to hold at 100 times Intents’ yearly fees.`
      : `**Growth.** At 100 times Intents’ yearly fees, NEAR’s price could hold even if volume shrank about ${pct(Math.abs(growth), 0)} a year.`,
    checkpointFee: `**Retained fee.** The case assumes Intents keeps ${inputs.retainedFeeBps} basis points of each swap after partners’ shares. ${intensityB === null ? 'The latest 30 days had no settled volume to compare against.' : `Wallet collections equaled ${intensityB.toFixed(2)} basis points of settled volume in the latest 30 days, a related but not identical measure.`} A higher retained fee would strengthen the case; a lower one would weaken it.`,
    checkpointIssuance: issuance <= targetIssuance ? `**Issuance.** The observed ${pct(issuance, 2)} cap is already at or below the 1.6% that HSP-029 outlines.`
      : e.requiredDailyVolumeUsd === null ? '**Issuance.** Cutting issuance to the 1.6% that HSP-029 outlines would lower the bar, but without a fee budget no volume level reaches it.'
      : `**Issuance.** If issuance fell from ${pct(issuance, 2)} to the 1.6% that HSP-029 outlines, the volume needed to match the new NEAR would fall by about ${pct(1 - targetIssuance / issuance, 0)}, to about ${usd(e.requiredDailyVolumeUsd * targetIssuance / issuance, 0)} a day.`,
    checkpointWorse: `**Trend.** Falling volume or a smaller retained fee would weaken the case further. From ${periodLabel(a)} to ${periodLabel(b)}, settled volume ${movement(volumeChange, 1)}.`,
    ...chainResearchFacts(),
    ...routingResearchFacts(),
    position: b.collectionsUsd > 0 ? 'Intents can become a material source of NEAR demand if repeat paid execution sustains retained revenue and treasury policy transmits that capacity to the token. The test is whether those economics can grow into the valuation.' : 'The latest window does not demonstrate continuing wallet collections. The fee-based investment case requires renewed evidence of external earnings and their transmission to NEAR.',
    windows: comparisonLabel(s),
    openingCase: coverage === null ? '**There is no new NEAR to match.** With zero modeled issuance, the valuation test is how much fee income would justify NEAR’s price.' : `**${coverage < 1 ? 'Even the best case falls short.' : 'At the recent pace, fees could match the new NEAR.'}** At the pace of the 30 days ending ${shortDate(operatingEnd)} ${operatingEnd.slice(0,4)} (${usd(inputs.dailyVolumeUsd)} of swaps a day) and the ${priceDate} price of ${money(inputs.priceUsd)}, a year of fees could buy ${pct(coverage,1)} of a year’s new NEAR. That assumes Intents keeps ${inputs.retainedFeeBps} basis points (${pct(inputs.retainedFeeBps / 10_000, 2)}) of each swap, has no costs, spends every dollar on NEAR at that price, and that issuance stays at its ${pct(issuance,2)} annual cap.`,
    collectionComparison: `[Collections per dollar settled](${snapshotDownload(s, 'daily').href}) show how much of the volume reaches the treasury wallets, a closer test of fee capture than volume alone. From ${periodLabel(a)} to ${periodLabel(b)}, settled volume ${movement(change(b.volumeUsd, a.volumeUsd))} while dollar-valued collections ${movement(usdChange)}. ${ratioChange} Collection timing and asset prices also affect this ratio, so the model does not substitute it for an observed retained external-fee rate.`,
    nativeComparison: `[Native receipts](${snapshotDownload(s, 'native').href}) provide a quantity check on accumulation, rather than a second measure of income to add to dollar collections. External native receipts ${movement(nativeChange)}, from ${fullNumber(a.nativeReceiptsNear,0)} to ${fullNumber(b.nativeReceiptsNear,0)} NEAR across the three wallets. The native ledger counts transfers whose immediate sender is outside the three treasury wallets, which can include treasury capital returning through an intermediary. Wrapping, conversion timing and valuation prices can also separate the native and dollar trends.`,
    longSummary,

    purchaseHurdle: e.requiredDailyVolumeUsd === null ? 'With no effective acquisition budget, no finite daily-volume hurdle funds purchases equal to issuance.' : issuance === 0 ? 'The observed issuance cap is zero, so there is no positive issuance hurdle to match.' : `Costs and allocation materially change the adoption required to match issuance. At the reference price, the current ${pct(issuance,2)} cap and the ${inputs.retainedFeeBps}-basis-point retained fee, acquisitions equal one year’s gross issuance at ${usd(e.requiredDailyVolumeUsd,2)} of daily volume${e.volumeHurdle === null ? '' : `, or ${e.volumeHurdle.toFixed(2)} times the starting pace`}, assuming zero cash costs and full purchase allocation. If only 70% remains after costs and half funds purchases, the required volume rises to ${usd(e.requiredDailyVolumeUsd / .35,2)} per day. The zero-cost case therefore measures favorable acquisition capacity, not an observed cash budget.`,
    supplyComparison: `${sa.receiptQuantityToGrossMintingPct === null || sb.receiptQuantityToGrossMintingPct === null ? 'The treasury wallets’ receipts cannot be compared with the new NEAR in a period without minting.' : `In the two 30-day periods, the treasury wallets received NEAR equal to ${Number(sa.receiptQuantityToGrossMintingPct).toFixed(2)}% and ${Number(sb.receiptQuantityToGrossMintingPct).toFixed(2)}% of the new NEAR created.`} The periods, ${periodLabel(a)} and ${periodLabel(b)}, contain ${sa.payoutCount} and ${sb.payoutCount} verified epoch payouts, minting ${fullNumber(Number(sa.grossMintedNEAR))} and ${fullNumber(Number(sb.grossMintedNEAR))} NEAR. These compare received and newly minted quantities; they do not establish purchases, permanent retention or selling by issuance recipients.`,
    burnComparison: `Derived native burns were ${earlierShare !== null && laterShare !== null ? `equivalent to ${pct(earlierShare,3)} and ${pct(laterShare,3)} of gross minting` : `${fullNumber(Number(sa.nativeBurnDerivedNEAR))} and ${fullNumber(Number(sb.nativeBurnDerivedNEAR))} NEAR`} in the two periods. We infer burns as gross minting less the change between [opening total supply](https://nearblocks.io/blocks/${sa.openingBlock}) and [closing total supply](https://nearblocks.io/blocks/${sb.closingBlock}) over the same dates. That calculation does not identify which burns, if any, came from Intents.`,
    holderExample: `After five years at the ${pct(issuance,2)} annual issuance cap with no burns, the unstaked holder’s supply-share index is ${holder.nonStakerSupplyShareIndex.toFixed(2)} and the participating staker’s is ${holder.stakerSupplyShareIndex.toFixed(2)}, from 100 at the start. ${holderStake} The scenario allocates ${pct(assumptions.stakerIssuanceShare,0)} of issuance in proportion to active stake, assumes full validator performance and deducts ${pct(assumptions.validatorCommission,0)} pool commission from rewards.`,
    protocolPolicy: `NEAR’s annual issuance cap was ${pct(issuance, 2)} on ${shortDate(s.chain.asOf)} ${s.chain.asOf.slice(0,4)}. Actual minting and burns are measured separately. HSP-029 outlined a transition toward 1.6%; ${Math.abs(issuance - .016) < 1e-10 ? 'the observed cap now equals that target' : `the observed cap is ${pct(issuance, 2)}`}. A proposal alone does not establish activation or sustainable validator economics.`,
    multipleExample: issuance > 0 ? `At 200 times retained revenue, zero cash costs, full purchase allocation and the observed ${pct(issuance, 2)} issuance cap, the algebraic purchase-coverage ratio is ${pct(1 / (200 * issuance), 2)}. At 100 times it is ${pct(1 / (100 * issuance), 2)}; at 40 times it is ${pct(1 / (40 * issuance), 2)}. These ratios compare acquisitions with new issuance. Full issuance coverage is not a requirement for price appreciation.` : 'The observed issuance cap is zero, so purchase-to-issuance ratios have no nonzero denominator. No coverage percentage or fair-value ceiling is inferred.',

    inventory: s.chain.wallets.length ? `On ${shortDate(s.chain.asOf)} ${s.chain.asOf.slice(0,4)}, ${s.chain.wallets.map(wallet => `${walletNames[wallet.account]} held ${fullNumber(wallet.nativeNear)} NEAR`).join('; ')}.${s.chain.frontendUsdc === null ? '' : ` The frontend treasury also held ${fullNumber(s.chain.frontendUsdc, 6)} USDC.`}` : '',
    investmentConclusion: [
      e.purchaseCoverage === null ? 'With zero modeled issuance, the investment question turns on durable acquisition funding and valuation rather than matching new tokens.' : `At the recent pace and a ${inputs.retainedFeeBps}-basis-point retained fee, ${e.purchaseCoverage < 1 ? 'spending every retained dollar on NEAR would still buy fewer tokens than the network creates' : 'spending every retained dollar on NEAR would buy at least as many tokens as the network creates, if Intents keeps that share and spends all of it'}. Holding those tokens would also leave total supply unchanged.`,
      flatPriceHurdle ? `The price needs more than buybacks. If NEAR is valued at 100 times Intents’ yearly fees in Year 5, keeping today’s price takes about ${usd(flatPriceHurdle.requiredRevenueUsd, 1)} a year in fees, against ${usd(e.revenueUsd, 1)} at the recent pace before costs. The 100× multiple is our assumption, not a fair value.` : 'With no starting fee revenue, the model cannot express the five-year price hurdle as a compound growth rate.',
    ].join(' '),
  };
}

