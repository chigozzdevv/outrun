"""Reconstruct the 60-day verifier wNEAR inventory of the sole recurring additional withdrawal owner."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import base64
from collections import Counter,defaultdict
import csv
import hashlib
import io
import json

from compute.src.collectors.exchanges import verify_sigs,nep413,input_units
from decimal import Decimal,localcontext
from compute.src.collectors.receipts import verify_effects
from compute.src.collectors.finality import from_actions
from compute.src.collectors.funding import payer_key
from compute.src.collectors.history import api, WRAP,utc
from compute.src.treasury.inflows import OUT,PUBLIC,ROOT,decimal_near,source_iterator,request,actions,release_file,frozen_sources
from compute.src.treasury.largest import capture
from compute.src.treasury.intermediary import RPC_HOST,request_rpc
from compute.src.treasury.funding import START,END

DEST=ROOT/'data/verified/treasury-recurring-owner-2026-11-02'
TOKEN='nep141:wrap.near'

def inputs():
    summary=json.loads((OUT/'summary.json').read_text())
    group,=[r for r in summary['distinctOwnersAndRecipients'] if not r['inOriginalThree'] and r['receipts']>1]
    owner=group['withdrawingOwner']
    withdrawals=[r for r in csv.DictReader(PUBLIC.open()) if r['withdrawal_owner']==owner]
    assert len(withdrawals)==14
    return owner,withdrawals

def inventory(owner,collect):
    base=dict(account_id=owner,asset_id=WRAP,from_timestamp_ms=START-1000,to_timestamp_ms=END+1000,limit=100,desc=False)
    records=[];seen=set();cursor=None
    for i in range(300):
        req=base if cursor is None else dict(base,resume_token=cursor)
        doc=capture(DEST/(f'page-{i:04}.json.gz'),'https://transfers.main.fastnear.com/v0/transfers',req,collect,api)
        records.extend(doc['transfers']);cursor=doc.get('resume_token')
        print('Recurring owner transfer page',i+1,'entries',len(records),'terminal',not cursor,flush=True)
        if not cursor:break
        assert cursor not in seen and doc['transfers'];seen.add(cursor)
    else:raise ValueError('Transfer history incomplete')
    assert len(records)==len({(r['block_height'],r['transfer_index']) for r in records})
    assert all(r['account_id']==owner and r['asset_id']==WRAP for r in records)
    return [r for r in records if START*10**6<=int(r['block_timestamp'])<END*10**6]

def anchor(owner,state,collect):
    method='mt_balance_of';args=base64.b64encode(json.dumps(dict(account_id=owner,token_id=TOKEN),separators=(',',':')).encode()).decode()
    params=dict(request_type='call_function',account_id='intents.near',method_name=method,args_base64=args,block_id=state['hash'])
    req=dict(jsonrpc='2.0',id='recurring-verifier-balance',method='query',params=params)
    res=capture(DEST/('balance-'+str(state['height'])+'.json.gz'),'https://'+RPC_HOST,req,collect,request_rpc)['result']
    assert res['block_hash']==state['hash'] and int(res['block_height'])==state['height']
    return dict(height=state['height'],hash=state['hash'],inventoryRaw=json.loads(bytes(res['result'])))

def gather(records,collect,limit):
    positives=Counter();debits=set()
    for r in records:
        h=r['transaction_id'];assert h
        if int(r['amount'])>0:positives[h]+=int(r['amount'])
        else:debits.add(h)
    ranked=sorted(positives,key=lambda h:(-positives[h],h))
    txs=set(ranked[:limit] if limit else ranked)|debits
    print('Unique source transactions',len(txs),flush=True)
    manifest=DEST/(f'top-{limit}-sources.json' if limit else 'all-sources.json')
    if manifest.exists():
        return {h:(tx,src) for h,tx,src in frozen_sources(txs,json.loads(manifest.read_text()))}
    trees={h:(tx,src) for h,tx,src in source_iterator(txs)}
    for path in sorted(DEST.glob('transactions-*.json.gz')):
        import gzip
        archive=json.loads(gzip.decompress(path.read_bytes()))
        query=archive['request'];body=capture(path,'https://tx.main.fastnear.com/v0/transactions',query,False,None)
        assert {tx['transaction']['hash'] for tx in body['transactions']}==set(query['tx_hashes'])
        for tx in body['transactions']:
            h=tx['transaction']['hash']
            if h in txs and h not in trees:trees[h]=(tx,'transaction index / '+str(path.relative_to(ROOT)))
    remaining=sorted(txs-trees.keys(),key=lambda h:(h not in debits,-positives[h],h));print('Missing raw trees',len(remaining),flush=True)
    for i in range(0,len(remaining),20):
        query=dict(tx_hashes=remaining[i:i+20]);tag=hashlib.sha256(json.dumps(query,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        path=DEST/('transactions-'+tag+'.json.gz')
        body=capture(path,'https://tx.main.fastnear.com/v0/transactions',query,collect,lambda q:request('/v0/transactions',q)[0])
        assert {tx['transaction']['hash'] for tx in body['transactions']}==set(query['tx_hashes'])
        for tx in body['transactions']: trees[tx['transaction']['hash']]=(tx,'transaction index / '+str(path.relative_to(ROOT)))
        print('Recovered trees',min(i+20,len(remaining)),'of',len(remaining),flush=True)
    assert trees.keys()==txs
    references={h:trees[h][1] for h in sorted(trees)}
    files={src.split(' / ',1)[1].split('#',1)[0] for src in references.values()}
    files.update(str(path.relative_to(ROOT)) for path in [*sorted(DEST.glob('page-*.gz')),*sorted(DEST.glob('balance-*.gz')),PUBLIC,OUT/'summary.json',ROOT/'data/verified/treasury-rpc-2026-11-02/summary.json'])
    release_file(manifest,json.dumps(dict(transactions=references,files={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in sorted(files)}),indent=2)+'\n',False)
    return trees

def run(collect,limit):
    DEST.mkdir(exist_ok=True)
    owner,withdrawals=inputs()
    rows=inventory(owner,collect)
    windows=json.loads((ROOT/'data/verified/treasury-rpc-2026-11-02/summary.json').read_text())['states']
    opening,closing=[anchor(owner,s,collect) for s in (windows[0],windows[-1])]
    net=sum(int(r['amount']) for r in rows)
    assert net==int(closing['inventoryRaw'])-int(opening['inventoryRaw'])
    trees=gather(rows,collect,limit)
    return owner,withdrawals,rows,opening,closing,trees

def classify(owner,receipt_rows,tx,src,sigs):
    rid=receipt_rows[0]['receipt_id'];h=tx['transaction']['hash']
    e=next(ent for ent in tx['receipts'] if ent['execution_outcome'] and ent['execution_outcome']['id']==rid)
    body=e['receipt'];out=e['execution_outcome'];assert body and body['receipt_id']==rid and body['receiver_id']==out['outcome']['executor_id']=='intents.near'
    assert any(k.startswith('Success') for k in out['outcome']['status'])
    block=int(receipt_rows[0]['block_height']);stamp=int(receipt_rows[0]['block_timestamp'])
    if 'block_timestamp' in out:assert int(out['block_height'])==block and int(out['block_timestamp'])==stamp
    events=[json.loads(v[11:]) for v in out['outcome']['logs'] if v.startswith('EVENT_JSON:')]
    moves=Counter();debits=Counter()
    for event in events:
        name=event['event']
        if name not in ('mt_transfer','mt_mint','mt_burn'):continue
        assert event['standard']=='nep245'
        for d in event['data']:
            old=d.get('old_owner_id',d.get('owner_id') if name=='mt_burn' else None)
            new=d.get('new_owner_id',d.get('owner_id') if name=='mt_mint' else None)
            if owner not in (new,old):continue
            for k,v in zip(d['token_ids'],d['amounts'],strict=True):
                q=(int(new==owner)-int(old==owner))*int(v)
                if k==TOKEN:moves[(old if q>0 else new,q)]+=1
                elif q<0:debits[k]-=q
    assert moves==Counter((v['other_account_id'],int(v['amount'])) for v in receipt_rows)
    signed=from_actions(actions(body))
    c=dict(tx=h,rid=rid,collector=owner,stamp=stamp,block=block,w=sum(int(v['amount']) for v in receipt_rows),debits=debits)
    proof,own=verify_effects(c,events,signed)
    for sig in own:sigs[sig['hash']]=sig
    def rows(name):return [d for event in events if event['event']==name for d in event['data']]
    diffs=rows('token_diff');completed={(d['account_id'],d['intent_hash']) for d in rows('intents_executed')}
    inbound=[]; bySource=Counter()
    for t in rows('transfer'):
        amount=int(t['tokens'].get(TOKEN,0))
        if t['receiver_id']!=owner or not amount:continue
        sender=t['account_id'];ih=t['intent_hash'];assert (sender,ih) in completed
        legs=[v['diff'] for v in diffs if v['intent_hash']==ih and v['account_id']==sender]
        two_sided=any(any(int(q)>0 for q in d.values()) and any(int(q)<0 for q in d.values()) for d in legs)
        signature='finalized verifier execution'
        if ih in signed:
            s=signed[ih];msg=json.loads(s['payload']['message']);assert msg['signer_id']==sender and s['payload']['recipient']=='intents.near'
            expected=[(j['receiver_id'],j['tokens']) for j in msg['intents'] if j['intent']=='transfer']
            assert expected==[(j['receiver_id'],j['tokens']) for j in rows('transfer') if j['account_id']==sender and j['intent_hash']==ih]
            assert legs==[j['diff'] for j in msg['intents'] if j['intent']=='token_diff']
            key=payer_key(s);digest=nep413(s).hex()
            sigs[digest]=dict(hash=digest,pk=s['public_key'],sig=s['signature'],collector=key)
            signature='implicit Ed25519 sender' if key==sender else 'verifier-accepted Ed25519 key'
        bySource['swapped' if two_sided else 'unlinked']+=amount
        inbound.append(dict(sender=sender,amountRaw=str(amount),signedIntentsHash=ih,swapped=two_sided,signature=signature))
    return dict(tx=h,receipt=rid,height=block,utc=utc(stamp),receiptBlockHash=out['block_hash'],evidenceSource=src,creditRaw=sum(int(v['amount']) for v in receipt_rows if int(v['amount'])>0),
                debitRaw=-sum(int(v['amount']) for v in receipt_rows if int(v['amount'])<0),signedWNEARAcquisitionRaw=int(proof['signed_exchange_wnear_raw']),
                acquisitionInputsRaw=proof['input_raw_units'],bundledExchangeOutputs=proof['bundled_outputs'],explicitSwapLinkedIncomingWNEARRaw=bySource['swapped'],explicitOtherIncomingWNEARRaw=bySource['unlinked'],
                explicitTransferSources=json.dumps(inbound,separators=(',',':')),mintMinusBurnWNEARRaw=int(proof['mint_minus_burn_wnear_raw']),
                otherSignedWNEARChangeRaw=int(proof['other_signed_wnear_change_raw']),unattributedNetRaw=int(proof['unattributed_net_wnear_raw']),transactionURL='https://nearblocks.io/txns/'+h)

def report(collect,limit,verify):
    owner,withdrawals,rows,opening,closing,trees=run(collect,limit)
    groups=defaultdict(list)
    for r in rows:
        if r['transaction_id'] in trees:groups[r['transaction_id'],r['receipt_id']].append(r)
    sigs={};results=[]
    for (h,rid),group in groups.items():
        tx,src=trees[h];results.append(classify(owner,group,tx,src,sigs))
    verify_sigs(list(sigs.values()))
    results.sort(key=lambda r:(r['height'],r['receipt']))
    for result in results:
        for name in ('creditRaw','debitRaw','signedWNEARAcquisitionRaw','explicitSwapLinkedIncomingWNEARRaw','explicitOtherIncomingWNEARRaw'):
            result[name.removesuffix('Raw')+'Units']=decimal_near(result[name])
    def total(col):return sum(r[col] for r in results)
    netin=sum(int(r['amount']) for r in rows if int(r['amount'])>0)
    totaldebit=-sum(int(r['amount']) for r in rows if int(r['amount'])<0)
    assert total('debitRaw')==totaldebit
    assert totaldebit==sum(int(r['amountYocto']) for r in withdrawals)
    exits={r['transaction_id']:-int(r['amount']) for r in rows if int(r['amount'])<0}
    assert exits=={r['transaction_hash']:int(r['amountYocto']) for r in withdrawals}
    assert all(r['creditRaw']>=r['signedWNEARAcquisitionRaw'] for r in results)
    payouts={r['transaction_hash']:r for r in withdrawals}
    changes=defaultdict(list);acquired=Counter()
    for record in rows:changes[int(record['block_height'])].append(record)
    for record in results:acquired[record['height']]+=record['signedWNEARAcquisitionRaw']
    inventory=int(opening['inventoryRaw']);held=0;delivered=0;checkpoints=[]
    for block,entries in sorted(changes.items()):
        credit=sum(int(v['amount']) for v in entries if int(v['amount'])>0)
        payments=[v for v in entries if int(v['amount'])<0]
        debit=-sum(int(v['amount']) for v in payments)
        assert acquired[block]<=credit
        held+=acquired[block];inventory+=credit-debit
        forced=max(0,held-inventory);held-=forced;delivered+=forced
        assert not forced or payments
        if payments:
            payment,=payments;native=payouts[payment['transaction_id']]
            checkpoint=anchor(owner,dict(hash=native['verifier_block_hash'],height=block),collect)
            assert int(checkpoint['inventoryRaw'])==inventory
            checkpoints.append(dict(**checkpoint,payoutTransaction=payment['transaction_id'],minimumAcquiredWNEARDeliveredRaw=str(forced)))
    assert inventory==int(closing['inventoryRaw']) and held+delivered==sum(acquired.values())
    observed_inputs=Counter()
    for result in results:observed_inputs.update(json.loads(result['acquisitionInputsRaw']))
    meta=input_units()
    with localcontext() as ctx:
        ctx.prec=95
        input_assets=[dict(assetID=k,raw=str(v),symbol=meta[k]['symbol'] if k in meta else None,chain=meta[k]['blockchain'] if k in meta else None,
                           units=format(Decimal(v).scaleb(-meta[k]['decimals']),'f') if k in meta else None) for k,v in sorted(observed_inputs.items())]
    summary=dict(owner=owner,windowStartInclusiveUTC='2026-09-01',windowEndExclusiveUTC='2026-10-31',indexedCreditEvents=sum(int(r['amount'])>0 for r in rows),indexedDebitEvents=len(exits),
                 treasuryRecipient=withdrawals[0]['treasury_recipient'],directArchiveReceiptCount=sum(r['evidenceSource'].startswith('final RPC') for r in results),
                 rawIndexedReceiptCount=sum(r['evidenceSource'].startswith('transaction index') for r in results),
                 opening=opening,closing=closing,grossCreditsWNEAR=decimal_near(netin),grossWithdrawalsNEAR=decimal_near(totaldebit),
                 openingInventoryNetConsumedWNEAR=decimal_near(int(opening['inventoryRaw'])-int(closing['inventoryRaw'])),rankedCreditLimit=limit or 'all',
                 verifiedCreditReceipts=sum(r['creditRaw']>0 for r in results),verifiedGrossWNEARCredit=decimal_near(total('creditRaw')),remainingUnclassifiedWNEARCredits=decimal_near(netin-total('creditRaw')),
                 signedExchangeAcquisitionsWNEAR=decimal_near(total('signedWNEARAcquisitionRaw')),signedAcquisitionInputsRaw=dict(observed_inputs),verifiedUniqueNEP413Signatures=len(sigs),
                 signedInputAssets=input_assets,
                 verifiedAcquisitionReceipts=sum(r['signedWNEARAcquisitionRaw']>0 for r in results),
                 signedExchangeWNEARInsidePayoutTransactions=decimal_near(sum(r['signedWNEARAcquisitionRaw'] for r in results if r['tx'] in exits)),
                 signedExchangeWNEAROutsidePayoutTransactions=decimal_near(sum(r['signedWNEARAcquisitionRaw'] for r in results if r['tx'] not in exits)),
                 exchangeReceiptsWithOtherOutputs=sum(bool(json.loads(r['bundledExchangeOutputs'])) for r in results),
                 explicitSwapLinkedIncomingWNEAR=decimal_near(total('explicitSwapLinkedIncomingWNEARRaw')),explicitOtherIncomingWNEAR=decimal_near(total('explicitOtherIncomingWNEARRaw')),
                 otherSignedWNEARChange=decimal_near(total('otherSignedWNEARChangeRaw')),netMintLessBurnWNEAR=decimal_near(total('mintMinusBurnWNEARRaw')),unattributedNetWNEAR=decimal_near(total('unattributedNetRaw')),
                 minimumNativeTreasuryDeliveryFromIdentifiedExchanges=decimal_near(delivered),maximumIdentifiedWNEARRetained=decimal_near(held),
                 minimumNativeTreasuryDeliveryFromIdentifiedExchangesRaw=str(delivered),
                 closedAtZeroWithdrawals=sum(r['inventoryRaw']=='0' for r in checkpoints),withdrawalStocks=checkpoints,
                 scope='Complete unthresholded verifier wNEAR history, anchored by archival beginning and ending balances. Canonical receipt attribution covers the specified largest incoming transactions and all debits; the count and unexamined amount are explicit. Different stages of this inventory are not additive. An executed payer transfer tied to a swap does not establish an agreed charge, independence or profit.')
    label=f'top-{limit}' if limit else 'all'
    stream=io.StringIO(newline='');w=csv.DictWriter(stream,fieldnames=list(results[0]),lineterminator='\n');w.writeheader();w.writerows(results)
    exhibit=ROOT/'public/data'/f'near-intents-recurring-owner-{label}-credit-transactions-2026-09-01-to-2026-10-30.csv'
    release_file(exhibit,stream.getvalue(),verify)
    assert {r['treasury_recipient'] for r in withdrawals}=={summary['treasuryRecipient']}
    summary['sourceDigests']={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in (exhibit,DEST/(label+'-sources.json'),ROOT/'data/provenance/manifest.json',*sorted(DEST.glob('balance-*.json.gz')))}
    summary_path=DEST/(label+'-summary.json')
    release_file(summary_path,json.dumps(summary,indent=2)+'\n',verify)
    compact={k:v for k,v in summary.items() if k not in ('withdrawalStocks','sourceDigests','signedInputAssets','signedAcquisitionInputsRaw','scope')}
    compact['distinctSignedInputAssets']=len(input_assets)
    compact['sourceDigests']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (summary_path,exhibit,DEST/(label+'-sources.json'))}
    release_file(DEST/(label+'-page-summary.json'),json.dumps(compact,indent=2)+'\n',verify)
    print(json.dumps(summary,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--collect',action='store_true');p.add_argument('--limit',type=int,default=200);p.add_argument('--verify',action='store_true')
    a=p.parse_args();report(a.collect,a.limit,a.verify)
