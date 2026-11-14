"""Reproduce the 794 dated candidates, then independently verify each successful native receipt via archival RPC."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import base64
from collections import Counter
import concurrent.futures
import csv
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import gzip
import hashlib
import http.client
import io
import json
from pathlib import Path
import ssl
import threading
import time
import zipfile


OUT = ROOT / 'data/verified/withdrawal-rpc-2026-11-02'
BASE = ROOT / 'public/data/withdrawal-matches-2026-10-28.csv'
ORIGINAL = ROOT / 'data/provenance/source-data.zip'
PAGES = OUT / 'extension-indexer-pages.zip'
RAW = OUT / 'archival-responses.zip'
ENDPOINT = 'https://archival-rpc.mainnet.fastnear.com'
SOURCE = (ROOT / '.cache/live-thesis/sources')
WORK = Path('/private/tmp/intent-withdrawal-rpc-2026-11-02')
pace_lock = threading.Lock()
connections = threading.local()
next_request_at = 0.0
YOCTO = Decimal(10) ** 24
PREFIX = 'research/institutional-2026-10-28/revenue/raw/new-withdrawals-'
FIELDNAMES = ['withdrawal_utc', 'receipt_utc', 'transaction_hash', 'receipt_id', 'receipt_block', 'collector_account', 'treasury_recipient', 'service_output_near', 'indexed_receipt_near', 'withdrawal_archive_path', 'receipt_archive_path', 'indexed_block_hash', 'service_reported_withdrawal_fee_near']
RPCFIELDS = ['rpc_transfer_near', 'rpc_receipt_status', 'rpc_receipt_block_hash', 'indexed_block_equals_rpc_outcome_block', 'wrap_near_withdraw_receipt_id',
    'transaction_tree_tokens_burnt_near', 'transaction_signer', 'transaction_url']


def extra_service():
    with zipfile.ZipFile(ORIGINAL) as archive:
        names = sorted(n for n in archive.namelist() if n.startswith(PREFIX))
        assert len(names) == 3
        sources = {entry['archivePath']:entry for entry in json.loads((ROOT / 'data/provenance/manifest.json').read_text())['requests']}
        result = []
        for name in names:
            data = archive.read(name)
            assert hashlib.sha256(data).hexdigest() == sources[name]['sha256']
            batch = json.loads(data)
            assert batch['asset'] == 'nep141:wrap.near' and batch['recipient'] == 'buybacks.multisignature.near'
            for w in batch['withdrawals']:
                assert w['status'] == 'SUCCESS' and w['timestamp'] >= '2026-10-22'
                assert Decimal(w['amountOutFormatted']) * YOCTO == int(w['amountOut'])
                result.append((batch, w, name))
        assert len(result) == 91 and len({w['hash'] for _,w,_ in result}) == 91
        return result


def prepare_pages():
    # Save complete original indexer responses, including retrieval time, endpoint URL and body digest.
    needed = {w['hash'] for _,w,_ in extra_service()}
    with zipfile.ZipFile(PAGES, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(SOURCE.glob('*.json')):
            saved = json.loads(file.read_text())
            if '/v3/accounts/buybacks.multisignature.near/receipts?' not in saved.get('url',''):
                continue
            raw = saved['body']
            assert hashlib.sha256(raw.encode()).hexdigest() == saved['sha256']
            found = needed & {r['transaction_hash'] for r in json.loads(raw)['data']}
            if found:
                archive.write(file, file.name)
                needed -= found
            if not needed:
                break
    assert not needed, f'Missing indexer pages for {len(needed)} transactions'


def records():
    output = [{k:r[k] for k in FIELDNAMES if k not in ('indexed_block_hash','service_reported_withdrawal_fee_near')} for r in csv.DictReader(BASE.open())]
    assert len(output) == 703
    with zipfile.ZipFile(ORIGINAL) as archive:
        by_archive={}
        for r in output:
            path=r['receipt_archive_path']
            if path not in by_archive:
                batch=json.loads(archive.read(path))
                by_archive[path]={entry['receipt_id']:entry['receipt_block'] for entry in batch['txns']}
            block=by_archive[path][r['receipt_id']]
            assert int(block['block_height'])==int(r['receipt_block'])
            r['indexed_block_hash']=block['block_hash']
    with zipfile.ZipFile(PAGES) as archive:
        by_tx = {}
        for name in archive.namelist():
            saved = json.loads(archive.read(name))
            assert hashlib.sha256(saved['body'].encode()).hexdigest() == saved['sha256']
            for r in json.loads(saved['body'])['data']:
                if (r['receiver_account_id'] != 'buybacks.multisignature.near' or r['predecessor_account_id'] != 'intents.near' or
                        not r['outcome']['status'] or not r['actions'] or any(a['action'] != 'TRANSFER' for a in r['actions'])):
                    continue
                by_tx.setdefault(r['transaction_hash'],{})[r['receipt_id']] = (r, name)
        for batch, w, service_path in extra_service():
            matches = by_tx.get(w['hash'],{})
            assert len(matches) == 1, w['hash']
            rid,(r,path) = next(iter(matches.items()))
            assert int(r['actions_agg']['deposit']) == int(w['amountOut'])
            sec,nano=divmod(int(r['block']['block_timestamp']),10**9)
            utc = datetime.fromtimestamp(sec,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+f'.{nano:09d}Z'
            assert utc >= '2026-10-22'
            output.append(dict(zip(FIELDNAMES,[w['timestamp'],utc,w['hash'],rid,r['block']['block_height'],
              batch['affiliateRecipient'],batch['recipient'],w['amountOutFormatted'],format(Decimal(r['actions_agg']['deposit']) / YOCTO, 'f'),service_path,path,r['block']['block_hash']])))
    with zipfile.ZipFile(ORIGINAL) as archive:
        service_files={}
        for r in output:
            path=r['withdrawal_archive_path']
            if path not in service_files:
                batch=json.loads(archive.read(path))
                service_files[path]={w['hash']:w for w in batch['withdrawals'] if w['status']=='SUCCESS'}
            w=service_files[path][r['transaction_hash']]
            fee=Decimal(w['withdrawFeeFormatted'])
            assert fee*YOCTO==int(w['withdrawFee'])
            r['service_reported_withdrawal_fee_near']=format(fee,'f')
    assert len({r['transaction_hash'] for r in output}) == len({r['receipt_id'] for r in output}) == 794
    return sorted(output, key=lambda r:r['receipt_utc'])


def verify(r, raw):
    x = json.loads(raw)['result']
    assert x['transaction']['hash'] == r['transaction_hash']
    assert x['final_execution_status'] == 'FINAL'
    matching = [b for b in x['receipts'] if b['receipt_id'] == r['receipt_id']]
    assert len(matching) == 1, 'Missing exact indexed receipt body'
    body = matching[0]
    assert body['predecessor_id'] == 'intents.near' and body['receiver_id'] == r['treasury_recipient']
    actions = body['receipt']['Action']['actions']
    assert len(actions) == 1 and list(actions[0]) == ['Transfer']
    amount = Decimal(actions[0]['Transfer']['deposit']) / YOCTO
    assert amount == Decimal(r['service_output_near']), f'Different exact native amount: {amount}'
    outcomes = [o for o in x['receipts_outcome'] if o['id'] == r['receipt_id']]
    assert len(outcomes) == 1
    outcome = outcomes[0]
    status = next(iter(outcome['outcome']['status']))
    assert status in ('SuccessValue','SuccessReceiptId') and outcome['outcome']['executor_id'] == r['treasury_recipient'], 'Native delivery failed'
    ev = [v for v in x['receipts_outcome'] if r['receipt_id'] in v['outcome']['receipt_ids']]
    assert len(ev) == 1 and ev[0]['outcome']['executor_id'] == 'intents.near', 'Unlinked withdrawal receipt'
    tree = [x['transaction_outcome'], *x['receipts_outcome']]
    by_receipt={o['id']:o['outcome'] for o in x['receipts_outcome']}
    target=int(actions[0]['Transfer']['deposit'])
    unwraps=[]
    for b in x['receipts']:
        if (b['predecessor_id'],b['receiver_id'])!=('intents.near','wrap.near'):
            continue
        for a in b['receipt'].get('Action',{}).get('actions',[]):
            fc=a.get('FunctionCall',{})
            if fc.get('method_name')=='near_withdraw' and int(json.loads(base64.b64decode(fc['args']))['amount'])==target:
                unwraps.append((b['receipt_id'],int(fc['deposit'])))
    assert len(unwraps)==1,(r['transaction_hash'],'unwrap action')
    unwrap,attached=unwraps[0]
    callback=by_receipt[unwrap]
    assert any(s.startswith('Success') for s in callback['status'])
    paid=[b['receipt_id'] for b in x['receipts'] if b['receipt_id'] in callback['receipt_ids'] and
          (b['predecessor_id'],b['receiver_id'])==('wrap.near','intents.near') and
          b['receipt'].get('Action',{}).get('actions')==[{'Transfer':{'deposit':str(target+attached)}}]]
    assert len(paid)==1 and any(s.startswith('Success') for s in by_receipt[paid[0]]['status']), (r['transaction_hash'],'unwrap native payout')
    assert len({o['id'] for o in tree})==len(tree)
    execution_tokens=sum(int(o['outcome']['tokens_burnt']) for o in tree)
    return dict(r,rpc_transfer_near=format(amount,'f'),rpc_receipt_status=status,rpc_receipt_block_hash=outcome['block_hash'],
        indexed_block_equals_rpc_outcome_block=str(outcome['block_hash']==r['indexed_block_hash']).lower(),
        wrap_near_withdraw_receipt_id=unwrap,
        transaction_tree_tokens_burnt_near=format(Decimal(execution_tokens)/YOCTO,'f'),
        transaction_signer=x['transaction']['signer_id'],transaction_url='https://nearblocks.io/txns/'+r['transaction_hash'])


def get(r):
    global next_request_at
    with localcontext() as c:
        c.prec=80
        path=WORK/(r['transaction_hash']+'.gz')
        if path.exists():
            verify(r,gzip.decompress(path.read_bytes()))
            return True
        request={'jsonrpc':'2.0','id':'near-intents-withdrawal-audit','method':'EXPERIMENTAL_tx_status','params':{'tx_hash':r['transaction_hash'],'sender_account_id':r['treasury_recipient'],'wait_until':'FINAL'}}
        for attempt in range(5):
            with pace_lock:
                time.sleep(max(0, next_request_at-time.monotonic()))
                next_request_at=time.monotonic()+1.2
            try:
                if not getattr(connections,'rpc',None):
                    connections.rpc=http.client.HTTPSConnection('archival-rpc.mainnet.fastnear.com', timeout=25, context=ssl.create_default_context())
                connections.rpc.request('POST','/',body=json.dumps(request),headers={'Content-Type':'application/json','User-Agent':'IntentThesis/1.0 public archival research'})
                response=connections.rpc.getresponse()
                raw=response.read()
                if response.status==429:
                    with pace_lock:
                        retry=response.getheader('Retry-After','0')
                        next_request_at=max(next_request_at,time.monotonic()+max(30,int(retry) if retry.isdigit() else 30))
                    raise ValueError('RPC returned HTTP 429')
                if response.status!=200:
                    raise ValueError(f'RPC returned HTTP {response.status}')
                try:
                    verify(r,raw)
                except AssertionError as error:
                    (WORK/(r['transaction_hash']+'.mismatch.gz')).write_bytes(gzip.compress(raw,mtime=0))
                    print('mismatch',r['transaction_hash'],str(error),flush=True)
                    return False
                path.write_bytes(gzip.compress(raw,mtime=0))
                return True
            except (OSError, http.client.HTTPException, KeyError, ValueError) as error:
                if getattr(connections,'rpc',None):
                    connections.rpc.close()
                    connections.rpc=None
                if attempt==4:
                    print('unavailable',r['transaction_hash'],type(error).__name__,str(error)[:140],flush=True)
                    return False
                time.sleep(1+attempt)


def save_csv(rows):
    p=OUT/'withdrawal-rpc-matches.csv'
    with p.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDNAMES+RPCFIELDS,lineterminator='\n')
        w.writeheader()
        w.writerows(rows)


def checkpoint(candidates):
    tmp=RAW.with_suffix('.part')
    with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=5) as archive:
        for r in candidates:
            p=WORK/(r['transaction_hash']+'.gz')
            if not p.exists():
                p=WORK/(r['transaction_hash']+'.mismatch.gz')
            if p.exists():
                archive.writestr(r['transaction_hash']+'.json',gzip.decompress(p.read_bytes()))
    tmp.replace(RAW)


def result(candidates,raws):
    verified=[]
    mismatches=[]
    collected_names=set(raws.namelist())
    for r in candidates:
        name=r['transaction_hash']+'.json'
        if name in collected_names:
            try:
                verified.append(verify(r,raws.read(name)))
            except AssertionError as error:
                mismatches.append({'hash':r['transaction_hash'],'reason':str(error)})
    save_csv(verified)
    summary={'rpcEndpoint': ENDPOINT, 'method':'EXPERIMENTAL_tx_status, FINAL',
             'indexedCandidates':len(candidates), 'directlyVerified':len(verified),
             'receiptAmountNEAR':str(sum(Decimal(r['rpc_transfer_near']) for r in verified)),
             'firstWithdrawalUTC':min(r['withdrawal_utc'] for r in verified) if verified else None,
             'lastWithdrawalUTC':max(r['withdrawal_utc'] for r in verified) if verified else None,
             'extensionFrom':'2026-10-22T00:00:00Z','extensionIndexedCandidates':91,
             'extensionDirectlyVerified':sum(r['withdrawal_utc']>='2026-10-22' for r in verified),
             'extensionTotalNEAR':str(sum(Decimal(r['service_output_near']) for r in candidates if r['withdrawal_utc']>='2026-10-22')),
             'scope':'Three known collector histories in the 60-day study plus two buyback collector responses retrieved 28 Oct at approximately 07:39 UTC. Service and independent indexed receipt timestamps recorded separately. Exact native-transfer receipt execution checks do not identify funding source, native burn, or downstream use.',
             'missingRPCTransactions':sorted({r['transaction_hash'] for r in candidates}-{r['transaction_hash'] for r in verified})}
    summary['rpcMismatches']=mismatches
    summary['differentIndexedAndRpcBlockHashes']=sum(r['indexed_block_equals_rpc_outcome_block']=='false' for r in verified)
    summary['serviceReportedWithdrawalFeesNEAR']=str(sum(Decimal(r['service_reported_withdrawal_fee_near']) for r in candidates))
    summary['transactionTreeTokensBurntNEAR']=str(sum(Decimal(r['transaction_tree_tokens_burnt_near']) for r in verified))
    summary['transactionSigners']=dict(sorted(Counter(r['transaction_signer'] for r in verified).items()))
    summary['executionAmountScope']='Sum of tokens_burnt reported across unique transaction and receipt outcomes in the matched trees, including any bundled operations. It does not establish attributable company operating costs or treasury destruction of acquired tokens.'
    matched={r['transaction_hash']:r for r in verified}
    conflicts={m['hash'] for m in mismatches}
    public=ROOT/'public/data/near-intents-collector-withdrawals-2026-08-23-to-2026-10-28.csv'
    with public.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDNAMES+RPCFIELDS+['rpc_check'],lineterminator='\n')
        writer.writeheader()
        for candidate in candidates:
            tx=candidate['transaction_hash']
            public_row=matched.get(tx,dict(candidate,transaction_url='https://nearblocks.io/txns/'+tx))
            writer.writerow(dict(public_row,rpc_check='verified' if tx in matched else 'conflicting' if tx in conflicts else 'not verified'))
    for collector in sorted({r['collector_account'] for r in verified}):
        subset = [r for r in verified if r['collector_account']==collector]
        summary.setdefault('collectors',[]).append({'id':collector,'recipient':subset[0]['treasury_recipient'],'verifiedWithdrawals':len(subset),
          'nativeTransferredNEAR':str(sum(Decimal(r['rpc_transfer_near']) for r in subset)),
          'serviceCoverageStartsUTC':min(r['withdrawal_utc'] for r in subset),'lastVerifiedWithdrawalUTC':max(r['withdrawal_utc'] for r in subset)})
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='missingRPCTransactions'}),flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['prepare','collect','checkpoint','verify'])
    mode=parser.parse_args().mode
    OUT.mkdir(exist_ok=True)
    if mode=='prepare':
        prepare_pages()
        cs=records()
        print('indexed:',len(cs), 'service output total:',sum(Decimal(c['service_output_near']) for c in cs))
        return
    cs=records()
    if mode=='collect':
        WORK.mkdir(exist_ok=True)
        if RAW.exists():
            with zipfile.ZipFile(RAW) as old:
                for r in cs:
                    name=r['transaction_hash']+'.json'
                    p=WORK/(r['transaction_hash']+'.gz')
                    if p.exists() or name not in old.namelist():
                        continue
                    raw=old.read(name)
                    try:
                        verify(r,raw)
                    except AssertionError:
                        continue
                    p.write_bytes(gzip.compress(raw,mtime=0))
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures=[pool.submit(get,r) for r in cs]
            success=0
            for i,future in enumerate(concurrent.futures.as_completed(futures),1):
                success+=int(future.result())
                if i%25==0:print('checked',i,'verified',success,flush=True)
    if mode in ('collect','checkpoint'):
        checkpoint(cs)
    with zipfile.ZipFile(RAW) as archive:
        result(cs,archive)


if __name__=='__main__':
    with localcontext() as c:
        c.prec=80
        main()
