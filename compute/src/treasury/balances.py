#!/usr/bin/env python3
"""Pin archival RPC states and reconcile native treasury accumulation to the receipt ledger.

Run with --collect for read-only public RPC calls; run without it to reproduce
the accounting solely from the saved requests, responses and exact receipt copy.
The residual is an accounting difference, not an inferred sale or operating cost.
"""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import base64
import csv
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen
from zipfile import ZipFile, ZIP_DEFLATED


OUT = ROOT / 'data/verified/treasury-rpc-2026-11-02'
RPC = 'https://archival-rpc.mainnet.fastnear.com'
WALLETS = ['buybacks.multisignature.near', 'fefundsadmin.sputnik-dao.near', '1csfundsadmin.sputnik-dao.near']
USDC = '17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1'
PUBLIC = ROOT / 'public/data/near-intents-treasury-balance-reconciliation-2026-09-01-to-2026-10-31.csv'
YOCTO = 10 ** 24


def rpc(method, params, collect):
    body = {'jsonrpc': '2.0', 'id': 'thesis-readonly', 'method': method, 'params': params}
    request_text = json.dumps(body, sort_keys=True, separators=(',', ':'))
    key = hashlib.sha256(request_text.encode()).hexdigest()
    path = OUT / 'rpc' / f'{key}.json'
    if collect and not path.exists():
        request = Request(RPC, data=request_text.encode(), headers={'Content-Type': 'application/json', 'User-Agent': 'NEARIntentsThesis/1.0 read-only public research'})
        for attempt in range(3):
            try:
                with urlopen(request, timeout=25) as response:
                    raw = response.read().decode()
                parsed = json.loads(raw)
                if 'error' in parsed:
                    raise ValueError(parsed['error'])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'url': RPC, 'request': body, 'fetchedAt': datetime.now(timezone.utc).isoformat(), 'responseSha256': hashlib.sha256(raw.encode()).hexdigest(), 'responseText': raw}, indent=2) + '\n')
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)
        time.sleep(.15)
    saved = json.loads(path.read_text())
    assert saved['request'] == body and saved['url'] == RPC
    assert hashlib.sha256(saved['responseText'].encode()).hexdigest() == saved['responseSha256']
    payload = json.loads(saved['responseText'])
    assert 'error' not in payload
    return payload['result']


def near(value):
    return format(Decimal(value) / Decimal(YOCTO), 'f')


def view_token(token, wallet, block_hash, collect):
    result = rpc('query', {'request_type': 'call_function', 'account_id': token, 'method_name': 'ft_balance_of', 'args_base64': base64.b64encode(json.dumps({'account_id': wallet}).encode()).decode(), 'block_id': block_hash}, collect)
    assert result['block_hash'] == block_hash
    amount = json.loads(bytes(result['result']))
    assert isinstance(amount, str) and amount.isdigit()
    return amount


def view_json(contract, method, arguments, block_hash, collect):
    result = rpc('query', {'request_type': 'call_function', 'account_id': contract, 'method_name': method, 'args_base64': base64.b64encode(json.dumps(arguments).encode()).decode(), 'block_id': block_hash}, collect)
    assert result['block_hash'] == block_hash
    return json.loads(bytes(result['result']))


def main():
    collect = argparse.ArgumentParser()
    collect.add_argument('--collect', action='store_true')
    args = collect.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.collect and not (OUT / 'receipt-ledger.json').exists():
        cache = json.loads((ROOT / '.cache/live-thesis/collector.json').read_text())
        snapshot = json.loads((ROOT / '.cache/live-thesis/snapshot.json').read_text())
        assert cache['receiptSchema'] == 3 and cache['receiptStart'] <= '2026-09-01' and cache['receiptEnd'] >= '2026-10-31'
        (OUT / 'receipt-ledger.json').write_text(json.dumps({'snapshotId': snapshot['id'], 'receiptSchema': 3, 'receiptStart': cache['receiptStart'], 'receiptEnd': cache['receiptEnd'], 'receipts': cache['receipts']}, indent=2) + '\n')
        (OUT / 'supply-windows.json').write_text(json.dumps(snapshot['operating']['supply']['windows'], indent=2) + '\n')
    ledger = json.loads((OUT / 'receipt-ledger.json').read_text())
    windows = json.loads((OUT / 'supply-windows.json').read_text())
    assert windows[0]['startInclusiveUTC'] == '2026-09-01T00:00:00Z' and windows[-1]['endExclusiveUTC'] == '2026-10-31T00:00:00Z'
    rows = ledger['receipts']
    assert len(rows) == len({row['id'] for row in rows})
    indexed_archive = OUT / 'incoming-indexer-responses.zip'
    if args.collect and not indexed_archive.exists():
        with ZipFile(indexed_archive, 'w', ZIP_DEFLATED) as archive:
            for source in sorted((ROOT / '.cache/live-thesis/sources').glob('*.json')):
                try:
                    saved = json.loads(source.read_text())
                except (ValueError, OSError):
                    continue
                if 'api.nearblocks.io/v3/accounts/' in saved.get('url', '') and '/receipts?' in saved['url']:
                    archive.write(source, source.name)
    indexed = {}
    start_ns = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()) * 10 ** 9
    end_ns = int(datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp()) * 10 ** 9
    with ZipFile(indexed_archive) as archive:
        for name in archive.namelist():
            source = json.loads(archive.read(name))
            assert hashlib.sha256(source['body'].encode()).hexdigest() == source['sha256']
            for raw in json.loads(source['body'])['data']:
                stamp = str(raw['block']['block_timestamp'])
                if (not start_ns <= int(stamp) < end_ns or raw['receiver_account_id'] not in WALLETS or raw['outcome']['status'] is not True or not raw['actions'] or not all(a['action'] == 'TRANSFER' for a in raw['actions'])):
                    continue
                amount = Decimal(raw['actions_agg']['deposit'])
                assert amount == int(amount)
                if amount == 0:
                    continue
                row = {'id': raw['receipt_id'], 'timestampNs': stamp, 'block': int(raw['block']['block_height']), 'predecessor': raw['predecessor_account_id'], 'receiver': raw['receiver_account_id'], 'amountYocto': str(int(amount)), 'transactionHash': raw['transaction_hash']}
                assert row['id'] not in indexed or indexed[row['id']] == row
                indexed[row['id']] = row
    assert indexed == {row['id']: row for row in rows}, 'Saved raw indexer receipts do not reproduce the native ledger'
    states = {}
    heights = sorted({w[key] for w in windows for key in ['openingBlock', 'closingBlock']})
    for height in heights:
        header = rpc('block', {'block_id': height}, args.collect)['header']
        assert header['height'] == height
        block_hash = header['hash']
        states[height] = {'height': height, 'hash': block_hash, 'timestampNs': header['timestamp_nanosec'], 'wallets': {}}
        for wallet in WALLETS:
            account = rpc('query', {'request_type': 'view_account', 'account_id': wallet, 'block_id': block_hash}, args.collect)
            assert account['block_hash'] == block_hash and account['block_height'] == height
            states[height]['wallets'][wallet] = {'nativeYocto': account['amount'], 'lockedYocto': account['locked'], 'codeHash': account['code_hash'], 'wrappedNearYocto': view_token('wrap.near', wallet, block_hash, args.collect)}
        states[height]['wallets'][WALLETS[1]]['usdcMicrounits'] = view_token(USDC, WALLETS[1], block_hash, args.collect)
    result = []
    for w in windows:
        a, b = states[w['openingBlock']], states[w['closingBlock']]
        matched = [r for r in rows if a['height'] < r['block'] <= b['height']]
        assert sum(int(r['amountYocto']) for r in matched if r['receiver'] in WALLETS and r['predecessor'] not in WALLETS) == int(Decimal(w['externalNativeWalletReceiptsNEAR']) * YOCTO)
        for wallet in WALLETS:
            incoming = [r for r in matched if r['receiver'] == wallet]
            external = sum(int(r['amountYocto']) for r in incoming if r['predecessor'] not in WALLETS)
            internal = sum(int(r['amountYocto']) for r in incoming if r['predecessor'] in WALLETS)
            opening, closing = a['wallets'][wallet], b['wallets'][wallet]
            delta = int(closing['nativeYocto']) - int(opening['nativeYocto'])
            residual = external + internal - delta
            result.append({'windowStartUTC': w['startInclusiveUTC'], 'windowEndExclusiveUTC': w['endExclusiveUTC'], 'wallet': wallet, 'openingBlock': a['height'], 'closingBlock': b['height'], 'openingBlockHash': a['hash'], 'closingBlockHash': b['hash'], 'openingNativeNEAR': near(opening['nativeYocto']), 'closingNativeNEAR': near(closing['nativeYocto']), 'nativeBalanceChangeNEAR': near(delta), 'indexedExternalNativeInflowNEAR': near(external), 'indexedInternalNativeInflowNEAR': near(internal), 'inflowsLessNativeBalanceChangeNEAR': near(residual), 'openingWrappedNEAR': near(opening['wrappedNearYocto']), 'closingWrappedNEAR': near(closing['wrappedNearYocto']), 'openingLockedNEAR': near(opening['lockedYocto']), 'closingLockedNEAR': near(closing['lockedYocto']), 'incomingReceiptCount': len(incoming)})
    closing_hash = states[heights[-1]]['hash']
    governance = {}
    for wallet in WALLETS[1:]:
        count = view_json(wallet, 'get_last_proposal_id', {}, closing_hash, args.collect)
        assert isinstance(count, int) and 0 <= count <= 10000
        proposals = []
        for start in range(0, count, 100):
            proposals += view_json(wallet, 'get_proposals', {'from_index': start, 'limit': 100}, closing_hash, args.collect)
        assert len({p['id'] for p in proposals}) == len(proposals)
        governance[wallet] = {'nextProposalId': count, 'returnedProposals': len(proposals), 'proposals': proposals}
    (OUT / 'dao-proposals.json').write_text(json.dumps({'blockHash': closing_hash, 'accounts': governance}, indent=2) + '\n')
    governance_rows = []
    for wallet, content in governance.items():
        for proposal in content['proposals']:
            transfer = proposal['kind'].get('Transfer', {})
            token = transfer.get('token_id')
            decimals = 24 if token == '' else 6 if token in [USDC, 'a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48.factory.bridge.near'] else None
            governance_rows.append({'wallet': wallet, 'proposalId': proposal['id'], 'statusAtClosingBlock': proposal['status'], 'closingBlock': heights[-1], 'submissionTimeNs': proposal['submission_time'], 'kind': next(iter(proposal['kind'])), 'tokenContract': token, 'receiver': transfer.get('receiver_id', ''), 'transferAmountRaw': transfer.get('amount', ''), 'transferAmountUnits': format(Decimal(transfer['amount']) / Decimal(10 ** decimals), 'f') if decimals is not None else '', 'description': proposal['description'], 'kindJSON': json.dumps(proposal['kind'], separators=(',', ':'))})
    governance_path = ROOT / 'public/data/near-intents-treasury-governance-to-2026-10-31.csv'
    with governance_path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=governance_rows[0], lineterminator='\n')
        writer.writeheader()
        writer.writerows(governance_rows)
    internal = [r for r in rows if r['predecessor'] in WALLETS and r['receiver'] in WALLETS and heights[0] < r['block'] <= heights[-1]]
    traces = []
    for row in internal:
        trace = rpc('EXPERIMENTAL_tx_status', {'tx_hash': row['transactionHash'], 'sender_account_id': row['predecessor'], 'wait_until': 'FINAL'}, args.collect)
        assert trace['transaction']['hash'] == row['transactionHash'] and trace['final_execution_status'] == 'FINAL'
        receipt = next(r for r in trace['receipts'] if r['receipt_id'] == row['id'])
        outcome = next(r for r in trace['receipts_outcome'] if r['id'] == row['id'])
        assert receipt['predecessor_id'] == row['predecessor'] and receipt['receiver_id'] == row['receiver']
        actions = receipt['receipt']['Action']['actions']
        assert all('Transfer' in a for a in actions) and sum(int(a['Transfer']['deposit']) for a in actions) == int(row['amountYocto'])
        assert 'SuccessValue' in outcome['outcome']['status']
        traces.append({'hash': row['transactionHash'], 'receiptId': row['id'], 'block': row['block'], 'predecessor': row['predecessor'], 'receiver': row['receiver'], 'nativeNEAR': near(row['amountYocto']), 'transferReceiptSucceeded': True, 'transactionStatus': trace['status']})
    with PUBLIC.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=result[0], lineterminator='\n')
        writer.writeheader()
        writer.writerows(result)
    summary = {'rpc': RPC, 'states': list(states.values()), 'windows': result, 'internalTransferTraces': traces, 'governanceCounts': {wallet: {'nextProposalId': row['nextProposalId'], 'returnedProposals': row['returnedProposals']} for wallet, row in governance.items()}, 'scope': 'Archival RPC account and token states at pinned block hashes. Incoming native transfer ledger enumerated using NearBlocks, without pretending RPC enumerates account history. Inflows less native balance growth is a net accounting residual: it is not identified sales, burns, costs, or gross outflows. Internal receipts are included per wallet; eliminate intra-group flows for consolidated accounting. Wrapped token balances and native locked balances do not prove the absence of deposits in staking-pool contracts.', 'csv': PUBLIC.name}
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({'rpcCallsSaved': len(list((OUT / 'rpc').glob('*.json'))), 'rows': result}, indent=2))


if __name__ == '__main__':
    with localcontext() as precision:
        precision.prec = 80
        main()
