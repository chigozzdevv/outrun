import reconstruction from '../../../data/evidence/2026-11-02/summaries/treasury.json' with { type: 'json' };
import verifier from '../../../data/evidence/2026-11-02/summaries/verifier.json' with { type: 'json' };
import withdrawals from '../../../data/evidence/2026-11-02/summaries/withdrawals.json' with { type: 'json' };
import conversions from '../../../data/evidence/2026-11-02/summaries/conversions.json' with { type: 'json' };
import { fullNumber } from '../../../compute/src/format.ts';
import { enrichedOnchainFacts } from './onchain-results.ts';

const YOCTO = 10n ** 24n;
const units = (value: string) => {
  const [whole, fraction = ''] = value.split('.');
  const sign = value.startsWith('-') ? -1n : 1n;
  return sign * (BigInt(whole.replace('-', '')) * YOCTO + BigInt(fraction.padEnd(24, '0')));
};
const quantity = (value: bigint) => Number(value) / 1e24;

export const treasuryReconciliation = [...new Set(reconstruction.windows.map(row => row.wallet))].map(wallet => {
  const rows = reconstruction.windows.filter(row => row.wallet === wallet);
  return {
    wallet,
    openingNEAR: Number(rows[0].openingNativeNEAR),
    closingNEAR: Number(rows.at(-1)!.closingNativeNEAR),
    externalNEAR: quantity(rows.reduce((sum, row) => sum + units(row.indexedExternalNativeInflowNEAR), 0n)),
    increaseNEAR: quantity(rows.reduce((sum, row) => sum + units(row.nativeBalanceChangeNEAR), 0n)),
  };
});

export function chainResearchFacts(): Record<string, string> {
  const source = reconstruction.windows;
  const total = (key: 'nativeBalanceChangeNEAR' | 'indexedExternalNativeInflowNEAR', rows = source) => rows.reduce((sum, row) => sum + units(row[key]), 0n);
  const retained = total('nativeBalanceChangeNEAR', source.filter(row => row.wallet !== '1csfundsadmin.sputnik-dao.near'));
  const example = conversions.stablecoinExample;
  return {
    ...enrichedOnchainFacts(),
    protocolFeeObservation: `The verifier’s protocol fee rate was ${(verifier.feePips / 100).toFixed(2)} basis point on 2 November 2026, payable to a protocol-fee collector account inside Intents. This charge is separate from the 1Click platform fee and the 5-basis-point retained rate assumed in the valuation.`,
    verifierInventory: `On 2 November 2026, the two buyback collectors held [${verifier.positiveTokenCounts['5880ad2b362620fadf759cbceb1cd5737ce8c6ed7fb8e9942881e6731f9247dd']} and ${verifier.positiveTokenCounts['143f2bb439575c286f4f585163f0e5fb4e464f8ce57ec06ae80741407d3cfb31']} nonzero token balances](/data/collectors/2026-11-02/internal-balances.csv). The protocol-fee collector held ${verifier.positiveTokenCounts[verifier.protocolCollector as keyof typeof verifier.positiveTokenCounts]} nonzero token balances, including ${fullNumber(Number(verifier.protocolCollectorWrappedNearAtomic) / 10 ** verifier.wrappedNearDecimals, 2)} wNEAR. These holdings inside Intents are distinct from native NEAR already delivered to a treasury. A withdrawal can draw on opening inventory as well as tokens acquired during the period.`,
    collectorVerification: `A withdrawal can bundle many fee payments, so withdrawal counts are not customer counts. The collectors made [${withdrawals.directlyVerified} withdrawals](/data/collectors/20260823-20261028/withdrawals.csv) from 23 August to 28 October 2026.`,
    collectorConversions: `The collectors completed [${conversions.executedSameIntentConversions} exchanges alongside treasury withdrawals](/data/collectors/20260823-20261028/conversions.csv), acquiring ${fullNumber(Number(conversions.wrappedNEARReceivedInMatchedConversionLegs), 6)} wNEAR against other assets. Those transactions delivered ${fullNumber(Number(conversions.nativeNEARWithdrawnInThoseTransactions), 6)} native NEAR to the treasuries. The difference can draw on earlier inventory; it cannot all be attributed to the exchanges in those transactions.`,
    collectorConversionExample: `In a [5 September transaction](${example.publicURL}), the collector spent ${example.units} ${example.fromChain} ${example.fromToken} for ${fullNumber(Number(example.principalWrappedNEAR), 6)} wNEAR and withdrew ${fullNumber(Number(example.nativeWithdrawalNEAR), 6)} native NEAR. A separate relay transfer credited another ${fullNumber(Number(example.additionalRelayWrappedNEAR), 9)} wNEAR. Counting the full withdrawal as newly purchased would overstate what that exchange proves.`,
    archivalOpening: `From 1 September through 30 October 2026, the buyback and frontend treasuries added [${fullNumber(quantity(retained))} NEAR to their native balances](/data/${reconstruction.csv}), including a transfer between treasury wallets. Net accumulation measures what the wallets held, not how much NEAR they bought during those 60 days.`,
    archivalBalances: `The buyback and frontend treasuries accumulated native NEAR in both September and October 2026. Their [balance increases match recorded inflows](/data/${reconstruction.csv}), but a total for all three wallets must remove the transfers between them.`,
  };
}
