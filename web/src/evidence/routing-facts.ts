import routing from '../../../data/evidence/2026-11-02/summaries/routing.json' with { type: 'json' };
import { fullNumber, pct, usd } from '../../../compute/src/format.ts';

export const interfaceRouting = routing;

export function routingResearchFacts(): Record<string, string> {
  const { volumes: v, cohort: c } = routing;
  return {
    routingSummary: `A few partner apps bring ${v.topFiveShareOfThirdParty > 0.5 ? 'more than half' : 'a large share'} of Intents’ third-party volume. The five largest interfaces carried ${pct(v.topFiveShareOfThirdParty, 2)} of that volume, or ${pct(v.topFiveShareOfAll, 2)} of the entire dataset, so the commercial terms of those relationships matter for fee income. [NearStats’ interface attribution](https://nearstats.org/integrators) covers ${fullNumber(v.totalSwaps, 0)} successful 1Click swaps worth ${usd(v.allUsd, 2)} from 4 October to 2 November 2026 at 13:35 UTC; the last day is partial. Untagged swaps account for ${pct(v.untaggedShareOfAll, 2)} of the dataset’s volume. These shares describe this 1Click dataset rather than all Intents settlement.`,
    routingRepeat: `Established distributors supplied ${pct(c.returningShareOfLateVolume, 2)} of third-party volume from 27 October to that cutoff. Of the ${c.lateLabels} active third-party interface labels, ${c.returningLabels} had also routed swaps during 4–26 October. This supports persistence in the distribution relationships over those periods of unequal length. It does not establish persistence in their fee rates or the income retained after partner shares.`,
  };
}
