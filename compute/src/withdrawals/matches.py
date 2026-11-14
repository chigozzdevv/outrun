#!/usr/bin/env python3
"""Reconstruct the dated public withdrawal exhibit from the archived service and indexer responses."""
from compute.src.paths import ROOT, CODE_ROOT
import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from fnmatch import fnmatchcase
from io import StringIO
import json
from pathlib import Path
from zipfile import ZipFile


ARCHIVE = ROOT / 'data/provenance/source-data.zip'
EXHIBIT = ROOT / 'public/data/withdrawal-matches-2026-10-28.csv'
START, MIDPOINT, END = '2026-08-23', '2026-09-22', '2026-10-22'
YOCTO = Decimal(10) ** 24
BUYBACK = 'buybacks.multisignature.near'
DAO = '1csfundsadmin.sputnik-dao.near'
EXPECTED_WINDOWS = [
    (338, Decimal('205264.255304490462777309926916')),
    (365, Decimal('120962.782041818647701399044612')),
]
SERVICE_PAGES = (
    'research/current/execution-raw/aggregation-[12]-page-*.json',
    'research/institutional-2026-10-28/revenue/raw/discovered-collector-*.json',
)
INDEXER_PAGES = 'research/near-deep-research/token-flows/cursor-history/*.near-*.json'


def receipt_timestamp(timestamp_ns):
    seconds, nanoseconds = divmod(int(timestamp_ns), 10**9)
    date = datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    return f'{date}.{nanoseconds:09}Z'


def native_transfers(archive):
    receipts = defaultdict(dict)
    for name in sorted(n for n in archive.namelist() if fnmatchcase(n, INDEXER_PAGES)):
        for raw in json.loads(archive.read(name), parse_float=Decimal)['txns']:
            recipient = raw['receiver_account_id']
            if (raw['receipt_outcome']['status'] is not True or raw['predecessor_account_id'] != 'intents.near'
                    or recipient not in (BUYBACK, DAO)):
                continue
            block = raw['receipt_block']
            utc = receipt_timestamp(block['block_timestamp'])
            if not START <= utc[:10] < END:
                continue
            actions = [a for a in raw['actions'] if a['action'] == 'TRANSFER']
            if not actions:
                continue
            key = (raw['transaction_hash'], recipient)
            receipt_id = raw['receipt_id']
            deposit = sum((Decimal(a['deposit']) for a in actions), Decimal(0))
            record = (utc, block['block_height'], deposit)
            old = receipts[key].get(receipt_id)
            if old is not None and old[:3] != record:
                raise ValueError(f'Conflicting copies of receipt {receipt_id}')
            receipts[key].setdefault(receipt_id, (*record, name))
    return receipts


def derive(archive):
    receipts = native_transfers(archive)
    rows, seen_hashes, seen_receipts = [], set(), set()
    excluded = Counter()
    for name in sorted(n for n in archive.namelist() if any(fnmatchcase(n, p) for p in SERVICE_PAGES)):
        service = json.loads(archive.read(name))
        if service.get('statusCode') == 404:
            continue  # A collector probe with no withdrawal history.
        recipient, asset = service['recipient'], service['asset']
        if recipient not in (BUYBACK, DAO) or asset != 'nep141:wrap.near':
            raise ValueError(f'Unexpected collector destination or output asset in {name}')
        for record in service['withdrawals']:
            service_utc = record['timestamp']
            if not START <= service_utc[:10] < END:
                continue
            if record['status'] != 'SUCCESS':
                excluded[record['status']] += 1
                continue
            tx = record['hash']
            candidates = receipts.get((tx, recipient), {})
            if len(candidates) != 1:
                raise ValueError(f'Expected one external native receipt for {tx}, found {len(candidates)}')
            receipt_id, (utc, block, deposit, indexer_source) = next(iter(candidates.items()))
            if tx in seen_hashes or receipt_id in seen_receipts:
                raise ValueError(f'Duplicate matched withdrawal or receipt for {tx}')
            seen_hashes.add(tx)
            seen_receipts.add(receipt_id)
            output = Decimal(record['amountOut'])
            difference = abs(deposit - output) / YOCTO
            if (output <= 0 or output / YOCTO != Decimal(record['amountOutFormatted'])
                    or float(output) != float(deposit) or difference > Decimal('1e-12')):
                raise ValueError(f'Withdrawal and indexer transfer amounts disagree for {tx}')
            if (utc[:10] < MIDPOINT) != (service_utc[:10] < MIDPOINT):
                raise ValueError(f'Withdrawal and receipt fall in different study windows for {tx}')
            rows.append({
                'window_start_utc': START if utc[:10] < MIDPOINT else MIDPOINT,
                'window_end_exclusive_utc': MIDPOINT if utc[:10] < MIDPOINT else END,
                'withdrawal_utc': service_utc, 'receipt_utc': utc,
                'transaction_hash': tx, 'receipt_id': receipt_id, 'receipt_block': block,
                'collector_account': service['affiliateRecipient'], 'treasury_recipient': recipient,
                'service_output_asset': asset, 'service_output_near': record['amountOutFormatted'],
                'receipt_asset': 'native NEAR', 'indexed_receipt_near': format(deposit / YOCTO, 'f'),
                'absolute_difference_near': format(difference, 'f'),
                'transaction_url': f'https://nearblocks.io/txns/{tx}',
                'withdrawal_archive_path': name, 'receipt_archive_path': indexer_source,
            })
    rows.sort(key=lambda row: (row['receipt_utc'], row['transaction_hash']))
    summary = []
    for start in (START, MIDPOINT):
        window = [r for r in rows if r['window_start_utc'] == start]
        summary.append((len(window), sum(Decimal(r['service_output_near']) for r in window)))
    if summary != EXPECTED_WINDOWS or excluded != {'FAILED': 4} or len({r['collector_account'] for r in rows}) != 3:
        raise ValueError(f'Dated collector population or totals changed: {summary}, exclusions {excluded}')
    return rows


def exhibit_bytes():
    with localcontext() as precision, ZipFile(ARCHIVE) as archive:
        precision.prec = 80
        rows = derive(archive)
    stream = StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=rows[0], lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


if __name__ == '__main__':
    EXHIBIT.write_bytes(exhibit_bytes())
    print(EXHIBIT.relative_to(ROOT))
