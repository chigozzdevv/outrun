"""Publish and independently replay the fixed 100-wNEAR cohort from the raw Explorer transaction API."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
from collections import Counter,defaultdict
import csv
from decimal import Decimal,localcontext
import hashlib
import io
import json
from compute.src.collectors.history import DATA,ROOT
from compute.src.collectors.batches import BATCH,packet
from compute.src.collectors.validation import assert_finalized,process
from compute.src.collectors.receipts import archived
from compute.src.collectors.report import summarize,units
from compute.src.collectors.large import load_tier
from compute.src.collectors.exchanges import input_units,verify_sigs

MANIFEST=DATA/'indexed-acquisition-release-inputs.json'
SUMMARY=DATA/'indexed-acquisition-cohort-summary.json'
PUBLIC=ROOT/'public/data/near-intents-collector-indexed-acquisition-receipts-2026-08-23-to-2026-10-28.csv'

def derive(frozen=None):
    rows=load_tier(100);groups=defaultdict(list);txids={r['transaction_id'] for r in rows};assert len(rows)==len(txids) and None not in txids
    for r in rows:groups[r['account_id'],r['receipt_id']].append(r)
    expected=set(groups);proofs={};seen=set();sigs={};discrepancies=[];amounts=Counter();source_files={};rpc_matched=set();block_matched=set();meta=input_units()
    archive_packets=sorted(set(frozen['sourcePacketsByHash'].values())) if frozen else [p.name for p in sorted(BATCH.glob('*.json.gz'))]
    with archived() as archive:
        original={n[:-5] for n in archive.namelist()}
        for name in archive_packets:
            _,raw=packet(BATCH/name)
            for tx in raw:
                h=tx['transaction']['hash']
                if h not in txids or h in source_files or (frozen and frozen['sourcePacketsByHash'][h]!=name):continue
                source_files[h]=name
                compare=(not frozen or h in frozen['archivalRpcComparedTransactionHashes']) and assert_finalized(tx,original,archive)
                if compare:rpc_matched.add(h)
                process(tx,groups,proofs,seen,compare,sigs,discrepancies,amounts)
    assert set(source_files)==txids and expected<=set(proofs) and not discrepancies
    verify_sigs(list(sigs.values()))
    selected={key:proofs[key] for key in expected}
    def assets(raw):
        result=[]
        for k,v in sorted(json.loads(raw).items()):
            m=meta.get(k,{});result.append(dict(token=k,symbol=m.get('symbol'),raw=str(v),units=format(Decimal(v).scaleb(-m['decimals']),'f') if 'decimals' in m else None))
        return json.dumps(result,separators=(',',':'))
    ledger=[]
    for key,v in sorted(selected.items(),key=lambda item:(item[1]['block_height'],item[0])):
        block=v['independently_matched_finalized_block_receipt']=='true' and (not frozen or v['receipt_id'] in frozen['finalizedBlockComparedReceiptIds'])
        if block:block_matched.add(v['receipt_id'])
        indexed=sum(int(r['amount']) for r in groups[key]);credit=int(v['observed_wnear_credits_raw']);swap=int(v['signed_exchange_wnear_raw']);transfer=int(v['explicit_net_wnear_transfers_raw'])
        assert indexed==credit==swap+transfer and int(v['unattributed_net_wnear_raw'])==0
        ledger.append(dict(time_utc=v['time_utc'],collector_account=v['collector'],transaction_hash=v['tx'],receipt_id=v['receipt_id'],block_height=v['block_height'],outcome_block_hash=v['block_hash'],
            indexed_credit_wnear=units(indexed),signed_collector_exchange_wnear=units(swap),explicit_net_transfer_wnear=units(transfer),collector_exchange_inputs=assets(v['input_raw_units']),included_exchange_event_fees=assets(v['exchange_fees_raw_units']),collector_exchange_intents=v['signed_exchange_intents'],directed_transfers_and_signature_bindings=v['explicit_incoming_transfers'],
            in_794_withdrawal_transactions=str(v['tx'] in original).lower(),raw_record_source='FastNear Explorer /v0/transactions',matched_to_independent_archival_rpc=str(v['tx'] in rpc_matched).lower(),matched_to_independent_finalized_block_receipt=str(block).lower(),transaction_url='https://nearblocks.io/txns/'+v['tx']))
    totals=summarize(rows,selected,original,meta);union=sum(r['matched_to_independent_archival_rpc']=='true' or r['matched_to_independent_finalized_block_receipt']=='true' for r in ledger)
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(ledger[0]),lineterminator='\n');writer.writeheader();writer.writerows(ledger);out=stream.getvalue()
    manifest=dict(sourcePacketsByHash={k:source_files[k] for k in sorted(source_files)},archivalRpcComparedTransactionHashes=sorted(rpc_matched),finalizedBlockComparedReceiptIds=sorted(block_matched))
    summary=dict(periodStartUTC='2026-08-22T23:59:59.885903781Z',periodEndUTC='2026-10-28T07:26:47.281150058Z',minimumIndividualCreditWNEAR=100,source='FastNear Explorer raw historical transaction and receipt index. All listed collector exchanges and swap-linked transfers are checked against successful receipt logs and signed instructions. The comparison fields identify the subset also reproduced from independent archival RPC or finalized full blocks.',
         documentedEndpoint='https://github.com/fastnear/explorer-api#post-v0transactions',directArchivalRpcMatches=len(rpc_matched),directFinalizedBlockReceiptMatches=len(block_matched),directOverlapAcrossEitherSource=union,publicCsvSha256=hashlib.sha256(out.encode()).hexdigest(),cohort=totals)
    if frozen:assert manifest==frozen
    return out,summary,manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['publish','verify']);args=p.parse_args()
    with localcontext() as context:context.prec=95;out,summary,manifest=derive(json.loads(MANIFEST.read_text()) if args.mode=='verify' else None)
    files={PUBLIC:out,SUMMARY:json.dumps(summary,indent=2)+'\n',MANIFEST:json.dumps(manifest,indent=2)+'\n'}
    for path,body in files.items():
        if args.mode=='verify':assert path.read_text()==body,path
        else:path.write_text(body)
    print('rows',summary['cohort']['indexCreditEvents'],'archival RPC',summary['directArchivalRpcMatches'],'finalized block',summary['directFinalizedBlockReceiptMatches'],'either',summary['directOverlapAcrossEitherSource'],'sha256',summary['publicCsvSha256'])
