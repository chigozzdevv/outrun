#!/usr/bin/env python3
"""Compile the page's dated observations from preserved inputs, offline.

The article lives only in web/src/thesis/article.json. This command produces data
assets for that page; it does not fetch sources or generate another document.
"""
from compute.src.paths import ROOT, CODE_ROOT
from compute.src.catalog import load_catalog
from collections import defaultdict
import csv
import datetime as dt
from decimal import Decimal, getcontext
import json
from pathlib import Path
import shutil


ORIGINAL = ROOT / 'data' / 'original'
VERIFIED = ROOT / 'data' / 'verified'
OUT = ROOT / 'data' / 'baselines'
PUBLIC = ROOT / 'public' / 'data'
CUTOFF = '2026-10-21'
NATIVE_START = '2026-08-23'
getcontext().prec = 60


def load(path):
    return json.loads(path.read_text())


def series(filename):
    result = {}
    for timestamp, value in load(ORIGINAL / filename)['totalDataChart']:
        date = dt.datetime.fromtimestamp(int(timestamp), dt.timezone.utc).date().isoformat()
        if date in result:
            raise ValueError(f'Duplicate UTC observation: {filename}: {date}')
        result[date] = value
    return result


def save_json(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    volume = series('volume.json')
    fees = series('fees.json')
    collections = series('collections.json')
    native = defaultdict(Decimal)
    receipts = []
    seen = set()
    with (ORIGINAL / 'native-receipts.csv').open() as f:
        for row in csv.DictReader(f):
            if row['classification'] != 'external_native_inflow':
                continue
            if row['receipt_id'] in seen:
                raise ValueError('Duplicate external receipt: ' + row['receipt_id'])
            seen.add(row['receipt_id'])
            native[row['utc'][:10]] += Decimal(row['amount_near'])
            receipts.append(row)

    daily = []
    dates = set()
    with (ORIGINAL / 'prices-and-observations.csv').open() as f:
        for row in csv.DictReader(f):
            date = row['date_utc']
            if date > CUTOFF:
                continue
            if date in dates:
                raise ValueError('Duplicate price-observation date: ' + date)
            dates.add(date)
            daily.append({
                'date': date,
                'volumeUsd': volume.get(date),
                'grossFeesUsd': fees.get(date),
                'collectionsUsd': collections.get(date),
                'nativeReceiptsNear': float(native[date]) if NATIVE_START <= date <= CUTOFF else None,
                'priceUsd': float(row['near_price_start_utc']) if row['near_price_start_utc'] else None,
            })
    assert daily == sorted(daily, key=lambda row: row['date'])

    periods = []
    for window in load(ORIGINAL / 'native-receipt-analysis.json')['windows']:
        start, end = window['start_utc'], window['end_exclusive_utc']
        rows = [row for row in daily if start <= row['date'] < end]
        assert len(rows) == 30
        assert all(row[key] is not None for row in rows for key in ['volumeUsd', 'grossFeesUsd', 'collectionsUsd'])
        exact_native = sum((native[row['date']] for row in rows), Decimal(0))
        assert exact_native == Decimal(window['total_external_native_inflows_near'])
        periods.append({
            'start': start, 'endExclusive': end, 'days': len(rows),
            'volumeUsd': sum(row['volumeUsd'] for row in rows),
            'grossFeesUsd': sum(row['grossFeesUsd'] for row in rows),
            'collectionsUsd': sum(row['collectionsUsd'] for row in rows),
            'nativeReceiptsNear': float(exact_native),
            'wallets': [
                {'account': account, 'amountNear': float(value['amount_near']), 'receiptCount': value['count']}
                for account, value in window['by_destination'].items()
            ],
        })
    assert [period['days'] for period in periods] == [30, 30]
    assert [period['collectionsUsd'] for period in periods] == [1_373_136, 2_282_005]

    sources = [
        {'id': 'volume', 'title': 'Intents settled volume', 'publisher': 'DefiLlama', 'url': 'https://api.llama.fi/summary/dexs/near-intents', 'kind': 'Public data'},
        {'id': 'fees', 'title': 'Intents gross fees', 'publisher': 'DefiLlama', 'url': 'https://api.llama.fi/summary/fees/near-intents?dataType=dailyFees', 'kind': 'Public data'},
        {'id': 'collections', 'title': 'Captured collections', 'publisher': 'DefiLlama', 'url': 'https://api.llama.fi/summary/fees/near-intents?dataType=dailyRevenue', 'kind': 'Public data'},
        {'id': 'adapter', 'title': 'Collection adapter and definitions', 'publisher': 'DefiLlama', 'url': 'https://github.com/DefiLlama/dimension-adapters/blob/acd40121e87b1ca65bf94f04a37a7f1910867f8e/fees/near-intents/index.ts', 'kind': 'Methodology'},
        {'id': 'rpc', 'title': 'Blocks and supply', 'publisher': 'NEAR Docs', 'url': 'https://docs.near.org/api/rpc/block-chunk', 'kind': 'Official documentation'},
        {'id': 'validators', 'title': 'Validator reward RPC', 'publisher': 'NEAR Docs', 'url': 'https://docs.near.org/api-reference/post-validators', 'kind': 'Official documentation'},
        {'id': 'tokenomics', 'title': 'Evolving NEAR tokenomics', 'publisher': 'NEAR', 'url': 'https://near.org/blog/evolving-near-tokenomics', 'kind': 'Official economic case'},
        {'id': 'svrn', 'title': 'A bottom-up investment case for NEAR', 'publisher': 'SVRN', 'url': 'https://svrn.net/research/near-protocol', 'kind': 'Published model'},
        {'id': 'fees-policy', 'title': 'Intents fee policy', 'publisher': 'NEAR Intents', 'url': 'https://docs.near-intents.org/resources/fees', 'kind': 'Official documentation'},
    ]
    data = {
        'version': load_catalog()['editions']['observations'],
        'retrievedAt': '2026-10-25',
        'cleanCutoff': CUTOFF,
        'nativeCoverage': {
            'start': NATIVE_START, 'end': CUTOFF,
            'internalTransfersExcludedNear': 502794, 'externalReceiptCount': len(receipts),
        },
        'limitations': [
            'Collections are wallet inflows valued in USD, not audited operating profit or proof of a new market purchase.',
            'Native TRANSFER receipts are rounded to indexer precision; the preserved CSV retains the reported decimal strings.',
            'The historical chart retains its 25 October source vintage and excludes potentially partial 22 October data.',
            'External means outside the three-wallet cluster, not necessarily an independent customer or outside the operating entity.',
            'The 90-day native comparison is price-normalized estimation, separate from the two 30-day receipt reconstructions.',
        ],
        'daily': daily,
        'periods': periods,
        'context': {
            'ninetyDayVolumeChange': 0.7039, 'ninetyDayCollectionsChange': 1.4926,
            'ninetyDayEstimatedNativeChangeLow': 0.55, 'ninetyDayEstimatedNativeChangeHigh': 0.60,
        },
        'valuationModelAudit': load(ORIGINAL / 'published-scenarios.json'),
        'seed': {
            'dailyVolumeUsd': periods[-1]['volumeUsd'] / 30,
            'openingSupplyNear': 1309924364.915439, 'initialPriceUsd': 4.534303591584394,
            'supplyBlock': 221562163, 'supplyDate': '2026-10-25',
            'quoteTimestamp': '2026-10-25T16:15:00Z',
        },
        'sources': sources,
        'sampleReceipts': [
            {'date': row['utc'], 'amountNear': float(row['amount_near']), 'from': row['predecessor'], 'to': row['receiver'],
             'receiptId': row['receipt_id'], 'tx': row['transaction_hash'],
             'url': 'https://nearblocks.io/txns/' + row['transaction_hash'],
             'classification': 'External native receipt; market acquisition not established by this receipt alone'}
            for row in sorted(receipts, key=lambda row: Decimal(row['amount_near']), reverse=True)[:12]
        ],
    }
    save_json('observations.json', data)

    supply = load(VERIFIED / 'matched-supply-windows.json')
    supply['audit'] = load(VERIFIED / 'supply-audit.json')
    assert len(supply['windows']) == 2
    for window in supply['windows']:
        assert int(window['grossMintedYocto']) - int(window['nativeBurnDerivedYocto']) == int(window['netSupplyChangeYocto'])
        assert int(window['closingSupplyYocto']) - int(window['openingSupplyYocto']) == int(window['netSupplyChangeYocto'])
    assert sum(window['payoutCount'] for window in supply['windows']) == 197
    assert supply['audit']['status'] == 'passed'
    save_json('supply.json', supply)

    with (PUBLIC / 'daily-observations.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(daily[0]))
        writer.writeheader()
        writer.writerows(daily)
    shutil.copy2(ORIGINAL / 'native-receipts.csv', PUBLIC / 'native-receipts.csv')
    shutil.copy2(ORIGINAL / 'published-model.csv', PUBLIC / 'published-model-reconstruction.csv')
    print(json.dumps({'observations': len(daily), 'externalReceipts': len(receipts), 'matchedSupplyPayouts': 197, 'cleanCutoff': CUTOFF}))


if __name__ == '__main__':
    main()
