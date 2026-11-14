"""Verify indexed raw execution records and quantify signed receipt attribution without describing index data as direct RPC."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
from collections import Counter,defaultdict
import csv
from decimal import Decimal,localcontext
import gzip
import hashlib
import io
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from functools import lru_cache
from compute.src.collectors.batches import BATCH,packet
from compute.src.collectors.exchanges import verify_sigs
from compute.src.collectors.finality import effects,BLOCKS
from compute.src.collectors.opening import verify as first_day
from compute.src.collectors.receipts import RPC,archived,verify_effects,rows_available
from compute.src.collectors.report import summarize,unique,units,input_units
from compute.src.collectors.large import load_tier
from compute.src.collectors.history import DATA,ROOT,PAGES,IDS,WRAP,VERIFIER,ORIGINAL_START,ORIGINAL_END

MATTERS=('status','logs','receipt_ids','executor_id','gas_burnt','tokens_burnt')
@lru_cache(maxsize=12)
def primary_block_receipts(height):
    path=BLOCKS/(str(height)+'.json.gz')
    if not path.exists():return None
    body=json.loads(gzip.decompress(path.read_bytes()));header=body['block']['header'];assert header['height']==height
    lookup={entry['receipt']['receipt_id']:entry for shard in body['shards'] for entry in shard['receipt_execution_outcomes'] if entry['receipt']['receiver_id']=='intents.near'}
    return header,lookup

def primary_receipt_match(txhash,indexed):
    e=indexed['execution_outcome'];rid=e['id'];p=primary_block_receipts(int(e['block_height']))
    if not p:return False
    header,lookup=p;entry=lookup[rid]
    assert e['block_hash']==header['hash'] and int(e['block_timestamp'])==int(header['timestamp_nanosec']) and entry['tx_hash']==txhash
    assert_outcome(e,entry['execution_outcome'])
    assert all(indexed['receipt'][k]==entry['receipt'][k] for k in ('predecessor_id','receiver_id','receipt_id','receipt'))
    return True
def assert_outcome(indexed,rpc):
    assert indexed['id']==rpc['id'] and indexed['block_hash']==rpc['block_hash']
    assert all(indexed['outcome'].get(k)==rpc['outcome'].get(k) for k in MATTERS),(indexed['id'],'outcome mismatch')

def assert_finalized(tx,original,archive):
    h=tx['transaction']['hash']
    if h in original:raw=archive.read(h+'.json')
    elif (RPC/(h+'.json.gz')).is_file():raw=gzip.decompress((RPC/(h+'.json.gz')).read_bytes())
    else:return False
    rpc=json.loads(raw)['result'];assert rpc['final_execution_status']=='FINAL' and tx['transaction']==rpc['transaction']
    assert_outcome(tx['execution_outcome'],rpc['transaction_outcome'])
    body={r['receipt_id']:r for r in rpc['receipts']};outcomes={o['id']:o for o in rpc['receipts_outcome']}
    match=set()
    for entry in tx['receipts']:
        e=entry['execution_outcome'];r=entry['receipt']
        if not e or e['outcome']['executor_id']!='intents.near':continue
        assert r is not None and r['receipt_id']==e['id'] and r['receiver_id']=='intents.near'
        assert_outcome(e,outcomes[e['id']]);match.add(e['id'])
        if e['id'] in body:
            old=body[e['id']];assert all(r[k]==old[k] for k in ('predecessor_id','receiver_id','receipt_id','receipt')),(h,'original receipt mismatch')
        else:
            assert rpc['transaction_outcome']['outcome']['receipt_ids']==[e['id']] and rpc['transaction']['signer_id']==rpc['transaction']['receiver_id']
            assert r['receipt']['Action']['actions']==rpc['transaction']['actions']
    assert match=={i for i,v in outcomes.items() if v['outcome']['executor_id']=='intents.near'},h
    return True

def process(tx,lookup,retained,seen,source_match,signatures,unexplained,allmovements,allowed_blocks=None):
    txhash=tx['transaction']['hash'];assert tx['execution_outcome']['id']==txhash
    for r in tx['receipts']:
        out=r['execution_outcome'];orig=r['receipt']
        if not out or not orig or out['outcome']['executor_id']!='intents.near':continue
        if not ORIGINAL_START<int(out['block_timestamp'])<=ORIGINAL_END:continue
        state=effects(dict(receipt=orig,execution_outcome=out,tx_hash=txhash))
        if not state:continue
        rid,_,events,signed,accounts,moves=state
        for owner,net in accounts.items():
            key=(owner,rid);assert key not in seen,(txhash,key,'duplicate outcome');seen.add(key)
            data=[m for m in moves if m['owner']==owner];obs=lookup.get(key,[])
            if obs:
                assert all(r['transaction_id'] in (txhash,None) and int(r['block_height'])==int(out['block_height']) and int(r['block_timestamp'])==int(out['block_timestamp']) for r in obs)
                canon=Counter((m['asset'],m['origin'] if m['change']>0 else m['target'],m['change']) for m in data)
                indexed=Counter((r['asset_id'][len(VERIFIER):],r['other_account_id'],int(r['amount'])) for r in obs)
                assert not indexed-canon,(key,'indexed and raw movement disagree',indexed-canon)
            for m in data:
                direction='in' if m['change']>0 else 'out'
                allmovements[owner,m['asset'],direction]+=abs(m['change'])
            positive=[m for m in data if m['asset']=='nep141:wrap.near' and m['change']>0]
            if not positive:continue
            debit=Counter()
            for m in data:
                if m['change']<0 and m['asset']!='nep141:wrap.near':debit[m['asset']]-=m['change']
            c=dict(collector=owner,tx=txhash,stamp=int(out['block_timestamp']),rid=rid,block=int(out['block_height']),w=net['nep141:wrap.near'],debits=debit)
            proof,sigs=verify_effects(c,events,signed)
            proof['block_hash']=out['block_hash'];proof['primary_source']='FastNear Explorer raw transaction/receipt index';proof['independently_matched_archival_rpc']=str(source_match).lower()
            proof['independently_matched_finalized_block_receipt']=str((allowed_blocks is None or rid in allowed_blocks) and primary_receipt_match(txhash,r)).lower()
            if int(proof['unattributed_net_wnear_raw']):unexplained.append((txhash,rid,proof['unattributed_net_wnear_raw']))
            retained[key]=proof
            for sig in sigs:signatures[sig['hash']]=sig

def run(write,verify=False,release_dir=DATA):
    release_dir.mkdir(parents=True,exist_ok=True)
    manifest_path=release_dir/'batch-classification-release-inputs.json'
    frozen=json.loads(manifest_path.read_text()) if verify else None
    page_files=[PAGES/n for n in frozen['transferPages']] if frozen else sorted(PAGES.glob('*.json.gz'))
    txn_files=[BATCH/n for n in frozen['rawTransactionPackets']] if frozen else sorted(BATCH.glob('*.json.gz'))
    rpc_allow=set(frozen['directRpcComparisons']) if frozen else None
    block_allow=set(frozen['directBlockComparisons']) if frozen else None
    cohorts={'firstDay':[r for r in first_day()[1] if int(r['amount'])>0]}
    for threshold in (1000,250,100):cohorts['atLeast'+str(threshold)]=load_tier(threshold)
    base=rows_available(page_files);rows=unique(base+[r for group in cohorts.values() for r in group])
    cohorts['availableIndexedCreditsAtLeast1']=[r for r in rows if r['asset_id']==WRAP and int(r['amount'])>=10**24]
    by=defaultdict(list)
    for r in rows:by[r['account_id'],r['receipt_id']].append(r)
    signatures={};seen=set();retained={};unexplained=[];allmoves=Counter();hashes=set();matched_hashes=set();queried=set();meta=input_units()
    if release_dir.name.startswith('expanded-priority-'):
        from compute.src.collectors.metadata import verified_units
        meta.update(verified_units())
    with archived() as archive:
        original={n[:-5] for n in archive.namelist()}
        for p in txn_files:
            request,txs=packet(p);queried.update(request)
            for tx in txs:
                h=tx['transaction']['hash']
                if h in hashes:continue
                hashes.add(h);confirmed=(rpc_allow is None or h in rpc_allow) and assert_finalized(tx,original,archive)
                if confirmed:matched_hashes.add(h)
                process(tx,by,retained,seen,confirmed,signatures,unexplained,allmoves,allowed_blocks=block_allow)
    if signatures:
        vals=list(signatures.values())
        for i in range(0,len(vals),4096):verify_sigs(vals[i:i+4096])
    prior=json.loads((ROOT/'data/verified/withdrawal-rpc-2026-11-02/conversion-summary.json').read_text())
    flows=json.loads((DATA/'full-period-wnear-bridge.json').read_text())
    gross=Decimal(flows['grossReceiptsWNEAR']);observed=sum(int(v['observed_wnear_credits_raw']) for v in retained.values())
    fresh=[v for v in retained.values() if v['tx'] not in original and int(v['signed_exchange_wnear_raw'])>0]
    original_amount=Decimal(prior['wrappedNEARReceivedInMatchedConversionLegs'])
    identified=original_amount+Decimal(units(sum(int(v['signed_exchange_wnear_raw']) for v in fresh)))
    assert Decimal(units(observed))<=gross
    assert all(int(v['signed_exchange_wnear_raw'])<=int(v['observed_wnear_credits_raw']) for v in retained.values())
    coverage=dict(fullPeriodGrossWNEARCredits=str(gross),capturedWholeReceiptGrossWNEARCredits=units(observed),uncapturedGrossWNEARCredits=str(gross-Decimal(units(observed))),
                  capturedPercent=format(100*Decimal(units(observed))/gross,'.8f'),distinctNewSignedExchangeReceiptsOutside794=len(fresh),newSignedExchangeWNEAROutside794=str(identified-original_amount),
                  original794SignedExchangeWNEAR=str(original_amount),identifiedSignedExchangeWNEARIncludingOriginal794=str(identified),identifiedExchangeShareOfAllGrossCreditsPct=format(100*identified/gross,'.8f'))
    states={v['tag']:v for v in json.loads((DATA/'boundary-states.json').read_text())};positions=[]
    for owner in IDS:
        start=states['Aug23']['balances'][owner];end=states['Oct28']['balances'][owner]
        for token in sorted(set(start)|set(end)|{k for o,k,_ in allmoves if o==owner}):
            a=int(start.get(token,0));b=int(end.get(token,0));incoming=allmoves[owner,token,'in'];outgoing=allmoves[owner,token,'out']
            if not any((a,b,incoming,outgoing)):continue
            positions.append(dict(owner=owner,assetId=token,openingRaw=str(a),capturedGrossCreditRaw=str(incoming),capturedGrossDebitRaw=str(outgoing),closingRaw=str(b),netMovementOutsideCapturedTreesRaw=str(b-a-incoming+outgoing)))
    assert sum(int(r['capturedGrossCreditRaw']) for r in positions if r['assetId']=='nep141:wrap.near')==observed
    report=dict(indexEndpoint='https://tx.main.fastnear.com/v0/transactions',capturePackets=len(txn_files),distinctRequestedTransactionHashes=len(queried),distinctReturnedTransactionTrees=len(hashes),returnedTreesIndependentlyComparedWithFinalizedRPC=len(matched_hashes),
                collectorVerifierReceiptsInCapturedTrees=len(seen),wNEARCreditedCollectorReceiptsInCapturedTrees=len(retained),independentlyCheckedEd25519Signatures=len(signatures),
                creditedReceiptsDirectlyCheckedByArchivedRPCOrFinalizedBlock=sum(r['independently_matched_archival_rpc']=='true' or r['independently_matched_finalized_block_receipt']=='true' for r in retained.values()),
                fullPeriodCoverage=coverage,
                capturedCollectorAssetRows=len(positions),
                unexplainedNetEntries=unexplained,cohorts={k:summarize(rs,retained,original,meta) for k,rs in cohorts.items()},
                scope='Raw original transaction actions, individual receipt bodies, outcome statuses, log events, execution heights, inclusion block hashes and UTC nanoseconds come from the historical Explorer API index. Collector messages and supported payer signatures are independently checked. Receipt trees with separately saved direct archival RPC responses are cross-checked on original transaction contents, verifier actions, receipt identities, block hashes, logs, execution status and gas/token quantities. Other indexed rows are expressly not described as direct RPC confirmations. The full set of captured trees is not a complete lifetime account history.')
    if write or verify:
        release=dict(transferPages=[p.name for p in page_files],rawTransactionPackets=[p.name for p in txn_files],directRpcComparisons=sorted(matched_hashes),directBlockComparisons=sorted({r['receipt_id'] for r in retained.values() if r['independently_matched_finalized_block_receipt']=='true'}))
        content={manifest_path:(json.dumps(release,indent=2)+'\n').encode(),release_dir/'batch-acquisition-summary.json':(json.dumps(report,indent=2)+'\n').encode()}
        if positions:
            stream=io.StringIO(newline='');w=csv.DictWriter(stream,fieldnames=list(positions[0]),lineterminator='\n');w.writeheader();w.writerows(positions)
            content[release_dir/'batch-asset-state-bridge.csv']=stream.getvalue().encode()
        vals=sorted(retained.values(),key=lambda r:(r['block_height'],r['collector'],r['receipt_id']))
        if vals:
            stream=io.StringIO(newline='');w=csv.DictWriter(stream,fieldnames=list(vals[0]),lineterminator='\n');w.writeheader();w.writerows(vals)
            content[release_dir/'batch-raw-receipt-classification.csv.gz']=gzip.compress(stream.getvalue().encode(),mtime=0)
        for path,body in content.items():
            if verify:assert path.read_bytes()==body,path
            else:
                with NamedTemporaryFile(dir=release_dir,delete=False) as out:out.write(body);temp=Path(out.name)
                temp.replace(path)
        from compute.src.collectors.issuance import calculate as acquisition_issuance
        issuance_bytes=(json.dumps(acquisition_issuance(release_dir/'batch-raw-receipt-classification.csv.gz'),indent=2)+'\n').encode()
        issuance_path=release_dir/'acquisition-issuance-reconciliation.json'
        if verify:assert issuance_path.read_bytes()==issuance_bytes,issuance_path
        else:
            with NamedTemporaryFile(dir=release_dir,delete=False) as out:out.write(issuance_bytes);temp=Path(out.name)
            temp.replace(issuance_path)
    print({k:report[k] for k in ('capturePackets','distinctRequestedTransactionHashes','distinctReturnedTransactionTrees','returnedTreesIndependentlyComparedWithFinalizedRPC','wNEARCreditedCollectorReceiptsInCapturedTrees','independentlyCheckedEd25519Signatures')})
    for k,v in report['cohorts'].items():print(k,v['receiptVerifiedReceipts'],v['indexCreditReceipts'],v['canonicalWholeReceiptSignedExchangeWNEAR'],v['awaitingCanonicalIndexWNEAR'])
    return report
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--write',action='store_true');p.add_argument('--verify',action='store_true');args=p.parse_args()
    with localcontext() as ctx:ctx.prec=95;run(args.write,args.verify)
