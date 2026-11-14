# The thesis: can NEAR Intents outrun dilution?

Figures are as of 14 November 2026. The live page updates them and dates every number.

## The claim

**Not yet.** NEAR creates new tokens every year to pay validators, and part of the fees from NEAR Intents swaps is used to buy NEAR back. That buyback route works, but at today's volume it is far too small to offset the new NEAR, and NEAR's price already assumes strong growth. The long-term case stays open because Intents volume has grown much faster than that price requires.

## Evidence for

- **Volume has grown fast.** Settled volume in the last 90 days was 223% higher than in the same 90 days a year earlier, and the last 30 days were 53% higher. Today's price needs about 17% a year.
- **More of each swap is reaching the treasury.** Collections equaled 3.66 basis points of settled volume over the last 180 days, 4.21 over the last 90 days and 4.82 over the last 30 days.
- **The buyback route works.** On 23 August 2026, one collector turned 252 swap payments received in USDT into NEAR and delivered at least 747.3 NEAR to the buyback treasury.
- **Partners keep sending trades.** From 27 October to 2 November, 99.9% of third-party volume came from apps that had already routed swaps during 4–26 October.

## Evidence against

- **Buybacks are small.** From 23 August to 28 October 2026, buybacks equaled about 5% of the new NEAR created. Even if Intents kept 0.05% of every swap and spent all of it on NEAR, today's fees would buy only about 21% of a year's new NEAR.
- **Growth is slowing.** Volume fell 21.7% in the last 30 days compared with the 30 days before.
- **Bought NEAR is held, not burned.** It does not reduce total supply, and the treasury can move it. On 10 July 2026 the buyback wallet sent 12,000 NEAR to an address that passed nearly all of it on to a bridge account.
- **A few keys control the treasury.** 2 of 4 keys can move NEAR out of the buyback wallet, and owning NEAR gives no vote over any of the three treasury wallets.

## What would change our mind

- **Volume.** Fee purchases match the new NEAR once swap volume holds above about $600m a day, at 0.05% kept per swap and the 14 November price. The last 30 days averaged $124m a day.
- **Growth.** NEAR's price holds at 100 times Intents' yearly fees only if volume grows faster than about 17% a year for five years.
- **Retained fee.** The case assumes Intents keeps 5 basis points of each swap after partners' shares. A higher share would strengthen it; a lower one would weaken it.
- **Issuance.** If issuance fell from 2.5% to the 1.6% that HSP-029 outlines, the volume needed to match the new NEAR would fall by about 36%, to about $380m a day.

## What we couldn't measure

- **Intents' costs.** The treasury payment records do not add up to an operating budget, so the model assumes zero costs.
- **Partners' share of each fee.** Volume shares are public, but the fee each partner keeps is not.
- **Who funded most of the other NEAR.** 1,807 payouts into the treasury wallets trace back to one account whose own funding is unknown.
- **Customer retention.** The repeat-customer sample is one ten-minute window on 1 August 2026.
- **Other demand for NEAR.** Staking for NEAR AI compute and NEAR's use as money or collateral are not valued.

## How to check it

Every figure above is on the live page with its source and date. The money trail is rebuilt from saved chain data with `npm run verify:research`, and the supply accounting and signed withdrawals are rechecked with `npm run verify:data`. See [README.md](README.md) for setup.
