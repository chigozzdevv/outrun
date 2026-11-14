
export const DAY = 86_400_000;
/** Versions whose block total-supply rule (previous supply + epoch minting − burnt balance) was checked in nearcore: 86 in 2.13.4; 87 and 88 in 2.14.0. */
export const REVIEWED_PROTOCOL_VERSIONS: readonly number[] = [86, 87, 88];
export const WALLETS = ['buybacks.multisignature.near', 'fefundsadmin.sputnik-dao.near', '1csfundsadmin.sputnik-dao.near'] as const;
export type Daily = { date: string; volumeUsd: number | null; grossFeesUsd: number | null; collectionsUsd: number | null; nativeReceiptsNear: number | null; priceUsd: number | null };
export type Period = { start: string; endExclusive: string; days: number; volumeUsd: number; grossFeesUsd: number; collectionsUsd: number; nativeReceiptsNear: number; wallets: { account: string; amountNear: number; receiptCount: number }[] };
export type SupplyWindow = { startInclusiveUTC: string; endExclusiveUTC: string; openingBlock: number; closingBlock: number; openingSupplyYocto: string; closingSupplyYocto: string; payoutCount: number; grossMintedYocto: string; grossMintedNEAR: string; netSupplyChangeYocto: string; netSupplyChangeNEAR: string; nativeBurnDerivedYocto: string; nativeBurnDerivedNEAR: string; externalNativeWalletReceiptsNEAR: string; receiptQuantityToGrossMintingPct: string | null; supplyIdentityResidualYocto: string };
export type FeedHealth = { checkedAt: string | null; successAt: string | null; error: string | null };
export type ReviewSource = { id: string; name: string; url: string; reviewedHash: string | null; currentHash: string | null; checkedAt: string | null; state: 'unchanged' | 'changed' | 'unavailable' | 'unreviewed' };
export interface LiveSnapshot {
  schemaVersion: 1;
  id: string;
  generatedAt: string;
  refreshSeconds: number;
  operating: {
    checkedAt: string;
    reporting?: { bufferDays: number; latestAvailableDate: string; revisedDates: string[] };
    endExclusive: string;
    daily: Daily[];
    periods: Period[];
    longPeriods: Omit<Period, 'nativeReceiptsNear' | 'wallets'>[] | null;
    nativeCoverage: { start: string; end: string; internalTransfersExcludedNear: number; externalReceiptCount: number };
    supply: { windows: SupplyWindow[]; protocolVersions: number[] };
  };
  market: { priceUsd: number; asOf: string; source: string };
  chain: { asOf: string; blockHeight: number; blockHash: string; supplyNear: number; issuanceRate: number; stakerIssuanceShare: number; protocolVersion: number; activeValidatorStakeNear?: number; validatorEpochId?: string; validatorEpochStartHeight?: number; wallets: { account: string; nativeNear: number; codeHash: string }[]; frontendUsdc: number | null };
  review: { reviewedAt: string; sources: ReviewSource[] };
  health: { operating: FeedHealth; market: FeedHealth; chain: FeedHealth; documents: FeedHealth };
}
export const POLICY_SOURCES = [
  { id: 'fees', name: 'Fee schedule', url: 'https://docs.near-intents.org/resources/fees.md' },
  { id: 'configuration', name: 'Fee configuration', url: 'https://docs.near-intents.org/integration/distribution-channels/1click-api/fee-config.md' },
  { id: 'terms', name: '1Click terms', url: 'https://docs.near-intents.org/security-compliance/terms-of-service.md' },
  { id: 'ai', name: 'NEAR AI terms', url: 'https://near.ai/terms-of-service' },
  { id: 'adapter', name: 'Collection methodology', url: 'https://raw.githubusercontent.com/DefiLlama/dimension-adapters/master/fees/near-intents/index.ts' },
] as const;
