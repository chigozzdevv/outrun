#!/usr/bin/env python3
"""Check the page's preserved public data and exact accounting, offline."""
from compute.src.paths import ROOT, CODE_ROOT
from pathlib import Path
from decimal import Decimal, getcontext
from collections import defaultdict
import csv
import hashlib
import json
import subprocess
import zipfile
from compute.src.withdrawals.matches import EXHIBIT, exhibit_bytes


DATA = ROOT / 'data'
getcontext().prec = 80
manifest = json.loads((DATA/'provenance/manifest.json').read_text())
ledger = json.loads((DATA/'verified/supply-ledger.json').read_text())['rows']
windows = json.loads((DATA/'verified/matched-supply-windows.json').read_text())['windows']
wallets = {'buybacks.multisignature.near', 'fefundsadmin.sputnik-dao.near', '1csfundsadmin.sputnik-dao.near'}

with zipfile.ZipFile(DATA/'provenance/source-data.zip') as archive:
    for item in manifest['files']:
        assert hashlib.sha256(archive.read(item['path'])).hexdigest() == item['sha256'], item['path']
    def document(name):
        value = json.loads(archive.read(name))
        return value.get('result', value)
    def header(name):
        return document(name)['header']
    assert len(ledger) == 220
    for row in ledger:
        first = document(row['firstBlockFile'])
        before = header(row['predecessorFile'])
        h = first['header']
        assert h['hash'] == row['hash'] and h['prev_hash'] == before['hash']
        assert h['epoch_id'] != before['epoch_id'] and h['next_epoch_id'] == before['hash']
        burn = sum(int(c['balance_burnt']) for c in first['chunks'] if c['height_included'] == h['height'])
        assert int(h['total_supply']) - int(before['total_supply']) + burn == int(row['grossMintedYocto'])
    for earlier, later in zip(ledger, ledger[1:]):
        assert header(later['firstBlockFile'])['epoch_id'] == header(earlier['predecessorFile'])['hash']
        assert header(later['predecessorFile'])['epoch_id'] == header(earlier['firstBlockFile'])['epoch_id']

    receipts = {}
    for name in archive.namelist():
        if '/token-flows/cursor-history/' not in name or name.endswith('/manifest.json'):
            continue
        for row in json.loads(archive.read(name), parse_float=Decimal).get('txns', []):
            receipts[row['receipt_id']] = row
    native = defaultdict(Decimal)
    for row in receipts.values():
        if not row.get('receipt_outcome', {}).get('status'):
            continue
        if row['receiver_account_id'] not in wallets or row['predecessor_account_id'] in wallets:
            continue
        ts = int(row['receipt_block']['block_timestamp'])
        for action in row.get('actions', []):
            if action['action'] == 'TRANSFER':
                native[ts] += Decimal(str(action['deposit'])) / Decimal(10)**24
    from datetime import datetime, timezone
    for window in windows:
        start = int(datetime.fromisoformat(window['startInclusiveUTC'].replace('Z', '+00:00')).timestamp())*10**9
        end = int(datetime.fromisoformat(window['endExclusiveUTC'].replace('Z', '+00:00')).timestamp())*10**9
        payouts = [r for r in ledger if start <= int(r['timestampNs']) < end]
        assert len(payouts) == window['payoutCount']
        assert sum(int(r['grossMintedYocto']) for r in payouts) == int(window['grossMintedYocto'])
        assert sum((v for t, v in native.items() if start <= t < end), Decimal(0)) == Decimal(window['externalNativeWalletReceiptsNEAR'])
        assert int(window['closingSupplyYocto'])-int(window['openingSupplyYocto']) == int(window['netSupplyChangeYocto'])
        assert int(window['grossMintedYocto'])-int(window['nativeBurnDerivedYocto']) == int(window['netSupplyChangeYocto'])

payloads = json.loads((DATA/'verified/withdrawal-payloads.json').read_text())
js = r'''
import {createHash,createPublicKey,verify} from 'node:crypto';
let input='';for await(const chunk of process.stdin)input+=chunk;
const rows=JSON.parse(input),alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
function base58(s){let n=0n;for(const c of s){const i=alphabet.indexOf(c);if(i<0)throw Error('Invalid base58');n=n*58n+BigInt(i);}let h=n.toString(16);if(h.length%2)h='0'+h;let zeros=0;for(const c of s){if(c!=='1')break;zeros++;}return Buffer.concat([Buffer.alloc(zeros),n===0n?Buffer.alloc(0):Buffer.from(h,'hex')]);}
function u32(n){const b=Buffer.alloc(4);b.writeUInt32LE(n);return b;}
function text(s){const b=Buffer.from(s);return Buffer.concat([u32(b.length),b]);}
function digest(p){const nonce=Buffer.from(p.nonce,'base64');if(nonce.length!==32)throw Error('Nonce length');return createHash('sha256').update(Buffer.concat([u32(2147484061),text(p.message),nonce,text(p.recipient),p.callbackUrl==null?Buffer.from([0]):Buffer.concat([Buffer.from([1]),text(p.callbackUrl)])])).digest();}
function check(pub,p,sig){const key=createPublicKey({key:Buffer.concat([Buffer.from('302a300506032b6570032100','hex'),pub]),format:'der',type:'spki'});return verify(null,digest(p),key,sig);}
const vector=check(Buffer.from('e2e9cb7ac57cb46d4da1ce1d1cc2c33bdfe17407c517916b522724a8ea2c6c50','hex'),{message:'Hello, world!',nonce:Buffer.alloc(32).toString('base64'),recipient:'intents.near'},Buffer.from('e2ff6254871a3fec1853c167b42f0f14248c4cf7fef5452dc24d8dbdc5c4bf183ab707322b4d782d5f5a05571bae476c5f7ee41c473f3002e600865e46b75d0f','hex'));
if(!vector||rows.length!==17)throw Error('Positive vector or sample coverage failed');
for(const r of rows){const pub=base58(r.publicKey.split(':')[1]),sig=base58(r.signature.split(':')[1]);if(JSON.parse(r.payload.message).signer_id!==pub.toString('hex')||!check(pub,r.payload,sig)||check(pub,{...r.payload,message:r.payload.message+' '},sig))throw Error('Withdrawal signature check failed');}
process.stdout.write(JSON.stringify({signatures:rows.length,positiveVector:true,mutationsRejected:true}));
'''
result = subprocess.run(['node', '--input-type=module', '-e', js], input=json.dumps(payloads), text=True, capture_output=True, check=True)
reconstructed = exhibit_bytes()
assert EXHIBIT.read_bytes() == reconstructed, 'Public withdrawal exhibit differs from archival reconstruction'
matched_by_window = defaultdict(Decimal)
for row in csv.DictReader(reconstructed.decode().splitlines()):
    matched_by_window[row['window_start_utc']] += Decimal(row['indexed_receipt_near'])
coverage = [
    round(100 * matched_by_window[w['startInclusiveUTC'][:10]] / Decimal(w['externalNativeWalletReceiptsNEAR']), 2)
    for w in windows
]
assert coverage == [Decimal('37.33'), Decimal('26.17')], 'Historical all-wallet receipt coverage changed'
print(json.dumps({'payloadHashes':len(manifest['files']), 'payoutBoundaries':len(ledger), 'matchedWindows':len(windows), 'includedPayouts':sum(w['payoutCount'] for w in windows), 'withdrawalMatches':len(reconstructed.splitlines())-1, 'matchedSharesPct':[str(value) for value in coverage], **json.loads(result.stdout), 'status':'passed'}))
