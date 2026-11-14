#!/usr/bin/env python3
"""Build the small article dataset from the independently reproducible on-chain exhibits."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import gzip
import hashlib
import json
from decimal import Decimal, localcontext
from pathlib import Path


TARGET = ROOT / 'data/verified/onchain-results-2026-11-02.json'
SOURCES = {
    'flow': 'collector-history-2026-11-02/full-period-wnear-bridge.json',
    'creditCensus': 'collector-history-2026-11-02/wnear-credit-census.json',
    'assetInventory': 'collector-history-2026-11-02/full-period-all-asset-summary.json',
    'inflowOrigins': 'treasury-inflow-origins-2026-11-02/page-summary.json',
    'recurringOwner': 'treasury-recurring-owner-2026-11-02/top-200-page-summary.json',
    'funding': 'collector-history-2026-11-02/stable-funding-summary.json',
    'acquisitions': 'collector-history-2026-11-02/acquisition-summary.json',
    'indexedAcquisitions': 'collector-history-2026-11-02/indexed-acquisition-cohort-summary.json',
    'expandedAcquisitions': 'collector-history-2026-11-02/expanded-priority-checkpoint-2026-11-02/published-summary.json',
    'intermediary': 'treasury-inflow-origins-2026-11-02/intermediary-summary.json',
    'treasury': 'treasury-outflows-2026-11-02/summary.json',
    'treasuryHistory': 'treasury-outflows-2026-11-02/account-history.json.gz',
    'treasuryControl': 'treasury-control-2026-11-02/summary.json',
    'nativeAdjustments': 'treasury-outflows-2026-11-02/gas-rewards-bridge.json',
    'onward': 'treasury-outflows-2026-11-02/one-hop-12000.json',
    'cohort': 'paid-activity-2026-11-02/summary.json',
    'allocation': 'paid-activity-2026-11-02/aggregation-transfer-summary.json',
    'destinations': 'paid-activity-2026-11-02/destination-summary.json',
}


def derive():
    source = {key: json.loads(gzip.decompress((ROOT / 'data/verified' / path).read_bytes()) if path.endswith('.gz') else (ROOT / 'data/verified' / path).read_text()) for key, path in SOURCES.items()}
    flow, funding, treasury, cohort = [source[key] for key in ['flow', 'funding', 'treasury', 'cohort']]
    assert all(int(row['unexplainedNetWNEAR'].replace('.', '')) == 0 and int(row['grossWNEARDebitsOutside794'].replace('.', '')) == 0 for row in flow['perCollector'])
    origins = source['inflowOrigins']
    assert 'unavailable_transaction_tree' not in origins['coverageByClass']
    assert sum(row['receipts'] for row in origins['coverageByClass'].values()) == origins['frozenReceipts']
    with localcontext() as context:
        context.prec = 100
        assert sum(Decimal(row['nativeNEAR']) for row in origins['coverageByClass'].values()) == Decimal(origins['grossNEAR'])
    assert origins['archivalRPCRows'] + origins['indexedTreeRows'] == origins['frozenReceipts']
    assert origins['independentlyVerifiedEd25519Intents'] == sum(origins['coverageByClass'][key]['receipts'] for key in ['original_three_verifier_owners', 'additional_verifier_owners'])
    assert source['creditCensus']['allGrossCreditsWNEAR'] == flow['grossReceiptsWNEAR']
    assert source['assetInventory']['completeForAllCollectors'] and source['assetInventory']['assetsWithReadableUnits'] == source['assetInventory']['distinctAssetIDs']
    recurring = source['recurringOwner']
    assert recurring['closedAtZeroWithdrawals'] == recurring['indexedDebitEvents']
    with localcontext() as context:
        context.prec = 100
        raw = lambda value: int(Decimal(value) * 10**24)
        assert int(recurring['opening']['inventoryRaw']) + raw(recurring['grossCreditsWNEAR']) - raw(recurring['grossWithdrawalsNEAR']) == int(recurring['closing']['inventoryRaw'])
        assert raw(recurring['signedExchangeAcquisitionsWNEAR']) == int(recurring['minimumNativeTreasuryDeliveryFromIdentifiedExchangesRaw']) + raw(recurring['maximumIdentifiedWNEARRetained'])
    assert funding['unclassifiedIncomingRaw'] == 0
    assert funding['independentlyVerifiedPayerSignatures'] == funding['bridge']['totalCreditEvents']
    trace = funding['chronologicalTrace']
    assert trace['firstOpeningInventoryTrade']['rawUSDTBalanceAfterTrade'] == '0'
    assert all(row['rawVerifierBalanceAfterWithdrawal'] == '0' for row in trace['payments'])
    assert int(trace['whollySwapFundedWNEARAcquiredRaw']) == int(trace['identifiedSwapFundedNativeDeliveryRaw']) + int(trace['identifiedWNEARAfterFinalDeliveryRaw'])
    assert int(source['nativeAdjustments']['reconciliation']['unexplainedYocto']) == 0
    assert cohort['indeterminateBaseline'] == 0 and cohort['candidateAccounts'] == cohort['baselineVerifiedAccounts'] + cohort['candidatesWithoutMatchingFee']
    assert all(row['unknown'] == 0 and sum(row.values()) == cohort['baselineVerifiedAccounts'] for row in cohort['cohortOutcomes'].values())
    symbols = {'NEAR': 'NEAR', '17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1': 'USDC', 'a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48.factory.bridge.near': 'Bridged USDC', 'intents.near/nep141:wrap.near': 'Internal wNEAR'}
    movements = [{**row, 'symbol': symbols.get(row['asset'], row['asset'])} for row in treasury['verifiedOutgoingByRecipient'] if row['entryType'] not in ['matched_proposal_deposit_return', 'attached_deposit']]
    approved = [row for row in treasury['verifiedFrontendTransferProposals'] if row['statusAtClosingBlock'] == 'Approved']
    assert all(row['successfulTransfer'] is not None for row in approved)
    wallet_entries = [row for wallet in source['treasuryHistory'].values() for row in wallet['rows']]
    control = source['treasuryControl']['wallets']
    buyback = control['buybacks.multisignature.near']
    assert all(key['access_key']['permission']['FunctionCall']['receiver_id'] == 'buybacks.multisignature.near' for key in buyback['accessKeys'])
    for wallet in ['fefundsadmin.sputnik-dao.near', '1csfundsadmin.sputnik-dao.near']:
        assert not control[wallet]['accessKeys']
        roles = {row['name']: row for row in control[wallet]['policy']['roles']}
        assert set(roles['Requestor']['kind']['Group']) == set(roles['Admin']['kind']['Group']) == set(roles['Approver']['kind']['Group'])
        assert roles['Admin']['vote_policy']['policy']['threshold'] == roles['Admin']['vote_policy']['upgrade_self']['threshold']
    return {
        'schemaVersion': 1,
        'sourceDigests': {path: hashlib.sha256((ROOT / 'data/verified' / path).read_bytes()).hexdigest() for path in SOURCES.values()},
        'flow': {key: flow[key] for key in ['startUTC', 'endUTC', 'openingBlock', 'closingBlock', 'perCollector', 'grossReceiptsWNEAR', 'grossDebitsWNEAR', 'nativeDelivered794NEAR']},
        'creditCensus': source['creditCensus'],
        'assetInventory': {key: source['assetInventory'][key] for key in ['collectorAssetRows', 'distinctAssetIDs', 'assetsWithReadableUnits', 'publicCsv']},
        'inflowOrigins': origins,
        'recurringOwner': source['recurringOwner'],
        'funding': {'bridge': funding['bridge'], 'verifiedPayerSignatures': funding['independentlyVerifiedPayerSignatures'], 'chronology': trace, 'nativeDeliveredTotalRaw': funding['minimumSwapLinkedTreasuryDelivery']['grossNativeDeliveredRaw']},
        'acquisitions': {'firstDay': {key: source['acquisitions']['cohorts']['firstDay'][key] for key in ['indexCreditReceipts', 'verifiedExchangeReceipts', 'canonicalWholeReceiptSignedExchangeWNEAR', 'paidInputAssets', 'canonicalWholeReceiptExplicitNetTransfersWNEAR', 'indexGrossWNEAR']}},
        'indexedAcquisitions': {'cohorts': {'atLeast100': {key: source['indexedAcquisitions']['cohort'][key] for key in ['indexCreditReceipts', 'indexGrossWNEAR', 'verifiedExchangeReceipts', 'canonicalWholeReceiptSignedExchangeWNEAR', 'canonicalWholeReceiptExplicitNetTransfersWNEAR']}}, 'treesComparedWithRPC': source['indexedAcquisitions']['directArchivalRpcMatches'], 'scope': source['indexedAcquisitions']['source']},
        'expandedAcquisitions': source['expandedAcquisitions'],
        'intermediary': source['intermediary'],
        'treasury': {'blockHeight': treasury['blockHeight'], 'utc': treasury['utc'], 'indexCoverage': treasury['fastNearAccountHistory'], 'distinctIndexedTransactions': len({row['transaction_hash'] for row in wallet_entries}), 'verifiedTransactions': treasury['rpcVerifiedTransactions'], 'movements': movements, 'verifiedFrontendTransferProposals': len(approved), 'executionBySigner': treasury['reportedExecutionByOriginalSigner'], 'staking': treasury['staking']},
        'nativeAdjustments': source['nativeAdjustments']['reconciliation'],
        'treasuryControl': source['treasuryControl'],
        'onward': {'incomingRaw': source['onward']['incomingYocto'], 'incomingUTC': source['onward']['incomingOccurredUTC'], 'transfers': [{**a, 'transactionHash': row['transactionHash']} for row in source['onward']['followOn'] for a in row['actions'] if a['action'] == 'Transfer']},
        'cohort': {key: cohort[key] for key in ['windows', 'candidateAccounts', 'baselineVerifiedAccounts', 'candidatesWithoutMatchingFee', 'cohortOutcomes', 'outcomesByWitnessRole', 'baselineReferralCounts', 'primaryBaselineRPCConfirmations', 'primaryBaselineBlockConfirmations', 'scope']},
        'allocation': source['allocation'],
        'destinations': source['destinations'],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--build', action='store_true')
    args = parser.parse_args()
    result = json.dumps(derive(), indent=2) + '\n'
    if args.build:
        TARGET.write_text(result)
    else:
        assert TARGET.read_text() == result, 'Article data differs from the frozen source exhibits'
    print('On-chain article dataset reproduced')
