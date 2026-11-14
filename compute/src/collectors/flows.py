#!/usr/bin/env python3
"""Reconcile complete daily indexed wNEAR flows with pinned verifier balance states."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import base64
import csv
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import time
from urllib.parse import quote
from urllib.request import Request, urlopen


OUT = ROOT / 'data/verified/collector-flow-accounting-2026-11-02'
COLLECTORS = ['143f2bb439575c286f4f585163f0e5fb4e464f8ce57ec06ae80741407d3cfb31', '5880ad2b362620fadf759cbceb1cd5737ce8c6ed7fb8e9942881e6731f9247dd', '0bc39dd4082e871df81eaa28250bf403fe900320a9d9929dc1304f17df462bac']
ASSET = 'nep141:wrap.near'
START, MID, END = '2026-09-01', '2026-10-01', '2026-10-31'
YOCTO = 10 ** 24


def indexed(account, collect):
    url = f'https://api.nearblocks.io/v3/mts/intents.near/tokens/{quote(ASSET, safe="")}/stats/{account}/transfers?limit=100'
    path = OUT / f'daily-{account}.json.gz'
    if collect and not path.exists():
        if account == COLLECTORS[0] and Path('/tmp/near-collector-daily.json').exists():
            raw = Path('/tmp/near-collector-daily.json').read_bytes()
            headers = Path('/tmp/near-collector-daily-headers.txt').read_text()
        else:
            request = Request(url, headers={'User-Agent': 'IntentThesis/1.0 public aggregate research'})
            with urlopen(request, timeout=35) as response:
                raw, headers = response.read(), str(response.headers)
            time.sleep(8)
        body = json.loads(raw)
        assert isinstance(body.get('data'), list)
        saved = {'url': url, 'fetchedAt': datetime.now(timezone.utc).isoformat(), 'responseSha256': hashlib.sha256(raw).hexdigest(), 'headers': headers, 'responseText': raw.decode()}
        path.write_bytes(gzip.compress(json.dumps(saved).encode(), mtime=0))
    saved = json.loads(gzip.decompress(path.read_bytes()))
    assert saved['url'] == url and hashlib.sha256(saved['responseText'].encode()).hexdigest() == saved['responseSha256']
    rows = json.loads(saved['responseText'])['data']
    assert rows and rows[-1]['date'] < START
    assert len({row['date'] for row in rows}) == len(rows)
    assert [row['date'] for row in rows] == sorted([row['date'] for row in rows], reverse=True)
    for row in rows:
        assert row['account'] == account and row['token'] == ASSET and row['contract'] == 'intents.near'
        assert all(str(row[k]).isdigit() for k in ['amount_in', 'amount_out', 'transfers'])
    return rows


def main(collect):
    OUT.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('treasury_rpc', CODE_ROOT / 'compute/src/treasury/balances.py')
    rpc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rpc)
    rpc.OUT = OUT
    states = json.loads((ROOT / 'data/verified/treasury-rpc-2026-11-02/summary.json').read_text())['states']
    assert [row['height'] for row in states] == [213761241, 217984973, 222343158]
    balances = {}
    for state in states:
        for account in COLLECTORS:
            args = {'account_id': account, 'token_ids': [ASSET]}
            response = rpc.rpc('query', {'request_type': 'call_function', 'account_id': 'intents.near', 'block_id': state['hash'], 'method_name': 'mt_batch_balance_of', 'args_base64': base64.b64encode(json.dumps(args).encode()).decode()}, collect)
            assert response['block_hash'] == state['hash'] and response['block_height'] == state['height']
            amounts = json.loads(bytes(response['result']))
            assert len(amounts) == 1 and amounts[0].isdigit()
            balances[(state['height'], account)] = int(amounts[0])
            if collect:
                time.sleep(1.5)
    accounting, daily = [], []
    native = lambda n: format(Decimal(n) / YOCTO, 'f')
    for account in COLLECTORS:
        rows = indexed(account, collect)
        for row in rows:
            if START <= row['date'] < END:
                daily.append({'dateUTC': row['date'], 'collector': account, 'assetId': ASSET, 'incomingAtomic': row['amount_in'], 'outgoingAtomic': row['amount_out'], 'incomingWNEAR': native(int(row['amount_in'])), 'outgoingWNEAR': native(int(row['amount_out'])), 'indexedTransferEvents': row['transfers']})
        for i, (start, end) in enumerate([(START, MID), (MID, END)]):
            selected = [row for row in rows if start <= row['date'] < end]
            incoming = sum(int(row['amount_in']) for row in selected)
            outgoing = sum(int(row['amount_out']) for row in selected)
            opening, closing = [balances[(state['height'], account)] for state in states[i:i+2]]
            residual = closing - opening - incoming + outgoing
            accounting.append({'collector': account, 'windowStartUTC': start, 'windowEndExclusiveUTC': end, 'openingBlock': states[i]['height'], 'closingBlock': states[i+1]['height'], 'openingAtomic': str(opening), 'closingAtomic': str(closing), 'indexedIncomingAtomic': str(incoming), 'indexedOutgoingAtomic': str(outgoing), 'residualAtomic': str(residual), 'openingWNEAR': native(opening), 'closingWNEAR': native(closing), 'indexedIncomingWNEAR': native(incoming), 'indexedOutgoingWNEAR': native(outgoing), 'indexedTransferEvents': sum(int(row['transfers']) for row in selected)})
    daily.sort(key=lambda row: (row['dateUTC'], row['collector']))
    for name, rows in [('near-intents-collector-daily-wnear-flows-2026-09-01-to-2026-10-30.csv', daily), ('near-intents-collector-wnear-reconciliation-2026-09-01-to-2026-10-30.csv', accounting)]:
        with (ROOT / 'public/data' / name).open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0], lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
    summary = {'assetId': ASSET, 'periodStartUTC': START, 'periodEndExclusiveUTC': END, 'rows': accounting, 'scope': 'Daily indexer aggregate quantities include incoming and outgoing internal wNEAR events. Incoming does not by itself identify acquisition or fee income; outgoing can include wrapped-claim burns and internal transfers. Each calendar window is independently compared with archived balance states at canonical block hashes. A zero net residual cross-checks the net accounting; it is not a cryptographic attestation of every gross event or proof of original fee provenance.'}
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--collect', action='store_true')
    with localcontext() as ctx:
        ctx.prec = 90
        main(parser.parse_args().collect)
