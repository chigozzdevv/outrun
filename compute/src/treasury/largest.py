"""Recover the earlier wNEAR movements for the six largest individual non-original-owner withdrawals."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import base64
from collections import Counter, defaultdict
import csv
from datetime import datetime,timezone
import gzip
import hashlib
import io
import json
from compute.src.collectors.history import api,WRAP
from compute.src.collectors.finality import from_actions
from compute.src.collectors.exchanges import verify_sigs,nep413,b58
from compute.src.collectors.funding import payer_key
from compute.src.treasury.inflows import PUBLIC, OUT,ROOT, decimal_near, request, actions,direct_witness,release_file

SAMPLE=OUT/'large-withdrawal-origins'
TOKEN='nep141:wrap.near'
PUBLIC_SIX=ROOT/'public/data/near-intents-six-largest-additional-treasury-withdrawals-2026-09-01-to-2026-10-30.csv'

def capture(path,url,query,collect,read):
    if not path.is_file():
        if not collect:raise FileNotFoundError(path)
        raw=read(query);doc=dict(url=url,request=query,retrievedAt=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(raw).hexdigest(),raw=raw.decode())
        tmp=path.with_suffix('.part');tmp.write_bytes(gzip.compress(json.dumps(doc,separators=(',',':')).encode(),mtime=0));tmp.replace(path)
    doc=json.loads(gzip.decompress(path.read_bytes()));raw=doc['raw'].encode()
    assert doc['url']==url and doc['request']==query and hashlib.sha256(raw).hexdigest()==doc['sha256']
    return json.loads(raw)

def histories(rows,collect):
    output=[]
    for sample in rows:
        owner=sample['withdrawal_owner']
        ns=int(datetime.fromisoformat(sample['receipt_utc'][:-1]).replace(tzinfo=timezone.utc).timestamp())*10**9+int(sample['receipt_utc'].split('.')[1][:-1])
        qry=dict(account_id=owner,asset_id=WRAP,to_timestamp_ms=ns//10**6+1,desc=False,limit=100)
        cursor=None;seen=set();transfers=[]
        for page in range(75):
            req=qry if cursor is None else dict(qry,resume_token=cursor)
            result=capture(SAMPLE/(owner+'-'+str(page)+'.json.gz'),'https://transfers.main.fastnear.com/v0/transfers',req,collect,api)
            transfers.extend(result['transfers'])
            cursor=result.get('resume_token')
            if not cursor: break
            assert cursor not in seen and result['transfers']
            seen.add(cursor)
        else:raise AssertionError('Lower the scope or finish the cursor before claiming complete history')
        selected=[r for r in transfers if int(r['block_timestamp'])<=ns]
        assert all(r['account_id']==owner and r['asset_id']==WRAP for r in selected)
        ids={(r['block_height'],r['transfer_index']) for r in selected}
        assert len(ids)==len(selected)
        payout=[r for r in selected if r['transaction_id']==sample['transaction_hash'] and -int(r['amount'])==int(sample['amountYocto']) and r['other_account_id'] is None]
        assert len(payout)==1
        selected=[r for r in selected if int(r['block_height'])<=int(payout[0]['block_height'])]
        net=0
        for r in sorted(selected,key=lambda v:(int(v['block_height']),int(v['transfer_index']))):
            net+=int(r['amount'])
            assert net>=0
        output.append((sample,selected,net))
        print('Non-original withdrawal owner',owner[:10],'history rows',len(selected),'complete',flush=True)
    return output

def credit_facts(owner,records,trees,sigs):
    groups=defaultdict(list)
    for r in records:
        if int(r['amount'])>0:groups[r['transaction_id'],r['receipt_id']].append(r)
    result=[]
    for (h,rid),rows in groups.items():
        tx,src=trees[h];entry=next(e for e in tx['receipts'] if e['execution_outcome'] and e['execution_outcome']['id']==rid)
        out=entry['execution_outcome'];r=entry['receipt'];assert r and r['receipt_id']==rid and r['receiver_id']==out['outcome']['executor_id']=='intents.near'
        assert any(k.startswith('Success') for k in out['outcome']['status'])
        if 'block_timestamp' in out:assert all(int(n['block_timestamp'])==int(out['block_timestamp']) and int(n['block_height'])==int(out['block_height']) for n in rows)
        events=defaultdict(list)
        for log in out['outcome']['logs']:
            if not log.startswith('EVENT_JSON:'):continue
            e=json.loads(log[11:]); events[e['event']].extend(e['data'])
            if e['event'] in ('mt_transfer','mt_mint','mt_burn'): assert e['standard']=='nep245'
            if e['event'] in ('transfer','token_diff','intents_executed'): assert e['standard']=='dip4'
        matched=Counter()
        for kind,prop in (('mt_transfer','new_owner_id'),('mt_mint','owner_id')):
            for e in events[kind]:
                if e[prop]!=owner:continue
                for k,v in zip(e['token_ids'],e['amounts'],strict=True):
                    if k==TOKEN:matched[e.get('old_owner_id'),int(v)]+=1
        assert matched==Counter((v['other_account_id'],int(v['amount'])) for v in rows)
        diff=[d for d in events['token_diff'] if d['account_id']==owner]
        signed=from_actions(actions(r))
        own=0;details=[]
        for d in diff:
            if int(d['diff'].get(TOKEN,0))<=0 or not any(int(v)<0 for v in d['diff'].values()): continue
            ih=d['intent_hash']; s=signed[ih];msg=json.loads(s['payload']['message'])
            assert s['payload']['recipient']=='intents.near' and msg['signer_id']==owner
            assert [v['diff'] for v in msg['intents'] if v['intent']=='token_diff']==[z['diff'] for z in diff if z['intent_hash']==ih]
            assert any(z['account_id']==owner and z['intent_hash']==ih for z in events['intents_executed'])
            key=payer_key(s);digest=nep413(s)
            sigs[ih]=dict(hash=digest.hex(),pk=s['public_key'],sig=s['signature'],collector=key)
            own+=int(d['diff'][TOKEN])
            details.append(d['diff'])
        total=sum(int(v['amount']) for v in rows);mint=sum(int(v['amount']) for v in rows if v['other_account_id'] is None);explicit=[]
        for d in events['transfer']:
            q=int(d['tokens'].get(TOKEN,0))
            if d['receiver_id']==owner and q:
                assert any(z['account_id']==d['account_id'] and z['intent_hash']==d['intent_hash'] for z in events['intents_executed'])
                ih=d['intent_hash'];s=signed[ih];msg=json.loads(s['payload']['message']);digest=nep413(s)
                assert b58(digest)==ih and msg['signer_id']==d['account_id'] and s['payload']['recipient']=='intents.near'
                assert [v['tokens'] for v in msg['intents'] if v['intent']=='transfer' and v['receiver_id']==owner]==[z['tokens'] for z in events['transfer'] if z['account_id']==d['account_id'] and z['receiver_id']==owner and z['intent_hash']==ih]
                key=payer_key(s);assert key==d['account_id']
                sigs[ih]=dict(hash=digest.hex(),pk=s['public_key'],sig=s['signature'],collector=key)
                explicit.append(dict(authorizer=d['account_id'],intent=ih,amountRaw=str(q),signature='NEP-413 Ed25519 implicit owner',sameIntentPositiveWNEARExchange=any(z['account_id']==key and z['intent_hash']==ih and int(z['diff'].get(TOKEN,0))>0 for z in events['token_diff'])))
        result.append(dict(transaction=h,receipt=rid,source=src,wholeGrossWNEARRaw=str(total),ownerSignedExchangeWNEARRaw=str(own),
                          ownerSignedInputOutput=details,ledgerMintWNEARRaw=str(mint),explicitIncomingTransfers=explicit,receiptActions=[a['FunctionCall']['method_name'] for a in actions(r) if 'FunctionCall' in a]))
    return result

def stock(owner,height,collect):
    from compute.src.treasury.intermediary import request_rpc,RPC_HOST
    args=base64.b64encode(json.dumps(dict(account_id=owner,token_id=TOKEN),separators=(',',':')).encode()).decode()
    q=dict(jsonrpc='2.0',id='treasury-rank-origin-inventory',method='query',params=dict(request_type='call_function',account_id='intents.near',method_name='mt_balance_of',args_base64=args,block_id=height))
    reply=capture(SAMPLE/(owner+'-stock-'+str(height)+'.json.gz'),'https://'+RPC_HOST,q,collect,request_rpc)['result']
    assert reply['block_height']==height
    return dict(height=height,hash=reply['block_hash'],wNEARRaw=json.loads(bytes(reply['result'])))

def counterasset_check(tx,credit,owner,collect):
    source,=credit['explicitIncomingTransfers'];sender=source['authorizer'];rid=credit['receipt'];h=tx['transaction']['hash']
    def executed(actions):
        fc,=actions
        assert list(fc)==['FunctionCall'] and fc['FunctionCall']['method_name']=='execute_intents'
        assert int(fc['FunctionCall']['deposit'])==0
        docs=json.loads(base64.b64decode(fc['FunctionCall']['args']))
        msg,=docs['signed']
        assert msg['standard']=='nep413'
        payload=json.loads(msg['payload']['message'])
        leg,=payload['intents']
        assert payload['signer_id']==sender and leg['intent']=='transfer' and leg['receiver_id']==owner and leg['tokens']=={TOKEN:source['amountRaw']}
    executed(tx['transaction']['actions'])
    allEvents=[];systemRefund=0
    for ent in tx['receipts']:
        if not ent['execution_outcome']:continue
        r=ent['receipt'];out=ent['execution_outcome']
        es=[json.loads(l[11:]) for l in out['outcome']['logs'] if l.startswith('EVENT_JSON:')]
        if out['id']==rid:
            executed(actions(r));allEvents=es;block=int(out['block_height'])
        else:
            assert es==[],(h,'another receipt contains token events')
            if actions(r):
                assert r['predecessor_id']=='system' and r['receiver_id']==tx['transaction']['signer_id']
                assert all(list(a)==['Transfer'] for a in actions(r))
                systemRefund+=sum(int(a['Transfer']['deposit']) for a in actions(r))
    assert [(e['standard'],e['event']) for e in allEvents]==[('dip4','transfer'),('dip4','intents_executed'),('nep245','mt_transfer')]
    physical,=allEvents[-1]['data'];assert physical==dict(old_owner_id=sender,new_owner_id=owner,token_ids=[TOKEN],amounts=[source['amountRaw']])
    assert len(allEvents[0]['data'])==len(allEvents[1]['data'])==1
    before=stock(sender,block-1,collect);after=stock(sender,block,collect)
    return dict(upstreamAccount=sender,submittedMessages=1,signedInstructions=1,matchedVerifierMTTransfers=1,allTokenAssets=[TOKEN],otherTokenAssetsMoved=[],
                mtMints=0,mtBurns=0,tokenDifferences=0,otherReceiptTokenEvents=0,otherNativeActions='protocol refund to original signer',refundedYocto=str(systemRefund),
                sourceOpeningBlock=before,sourceTransferBlock=after,sourceBlockChangeRaw=str(int(after['wNEARRaw'])-int(before['wNEARRaw'])),signedTransferWNEARRaw=source['amountRaw'])

def derive(collect,verify=False):
    SAMPLE.mkdir(exist_ok=True)
    ordered=sorted([r for r in csv.DictReader(PUBLIC.open()) if r['owner_in_original_three']=='false'],key=lambda r:(-int(r['amountYocto']),r['transaction_hash']))
    assert len({r['withdrawal_owner'] for r in ordered[:6]})==6
    traces=histories(ordered[:6],collect)
    target={r['transaction_id'] for _,vs,_ in traces for r in vs if int(r['amount'])>0}
    trees={}
    missing=sorted(target)
    for start in range(0,len(missing),20):
        req=dict(tx_hashes=missing[start:start+20])
        tag=hashlib.sha256(json.dumps(req,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        path=SAMPLE/(tag+'.json.gz')
        found=capture(path,'https://tx.main.fastnear.com/v0/transactions',req,collect,lambda q:request('/v0/transactions',q)[0])['transactions']
        assert {x['transaction']['hash'] for x in found}==set(req['tx_hashes'])
        for x in found:
            primary=direct_witness(x,collect)
            trees[x['transaction']['hash']]=(x,'transaction index / '+str(path.relative_to(ROOT))+' matched to final RPC / '+str(primary.relative_to(ROOT)))
    sigs={};exhibits=[]
    for sample,records,closing in traces:
        credits=credit_facts(sample['withdrawal_owner'],records,trees,sigs)
        c,=credits;c['wholeTransactionCounterassetCheck']=counterasset_check(trees[c['transaction']][0],c,sample['withdrawal_owner'],collect)
        opening=stock(sample['withdrawal_owner'],int(records[0]['block_height'])-1,collect)
        final=stock(sample['withdrawal_owner'],int(records[-1]['block_height']),collect)
        assert int(opening['wNEARRaw'])==0 and int(final['wNEARRaw'])==closing
        exhibits.append(dict(owner=sample['withdrawal_owner'],nativeWithdrawalTransaction=sample['transaction_hash'],nativeReceipt=sample['native_receipt_id'],
                             nativePayoutNEAR=sample['native_near'],treasury=sample['treasury_recipient'],wNEARHistoryEntries=len(records),endingWNEAR=decimal_near(closing),openingStock=opening,withdrawalBlockStock=final,creditReceipts=credits))
    verify_sigs(list(sigs.values()))
    report=dict(selection='Largest six individual native withdrawals authorized by owners outside the three original service collectors, with complete indexed wNEAR history up to each withdrawal block. This selection is intentionally value-ranked and not a representative sample.',signedUpstreamTransfersOrExchangeIntents=len(sigs),
                sourceTransactionsWithNoSecondAssetOrReciprocalPayment=sum(not c['wholeTransactionCounterassetCheck']['otherTokenAssetsMoved'] for case in exhibits for c in case['creditReceipts']),
                totalNativePayoutNEAR=decimal_near(sum(int(r['amountYocto']) for r in ordered[:6])),owners=exhibits)
    release_file(OUT/'largest-withdrawals-summary.json',json.dumps(report,indent=2)+'\n',verify)
    public=[]
    for row in exhibits:
        c,=row['creditReceipts'];src,=c['explicitIncomingTransfers'];h=c['transaction'];t=row['nativeWithdrawalTransaction']
        public.append(dict(source_authorizing_account=src['authorizer'],withdrawing_account=row['owner'],treasury=row['treasury'],native_near=row['nativePayoutNEAR'],funding_intent_hash=src['intent'],
                           opening_block=row['openingStock']['height'],opening_wnear=decimal_near(int(row['openingStock']['wNEARRaw'])),closing_block=row['withdrawalBlockStock']['height'],
                           closing_wnear=decimal_near(int(row['withdrawalBlockStock']['wNEARRaw'])),indexed_wnear_movements=row['wNEARHistoryEntries'],
                           source_wnear_before_transfer_block=decimal_near(int(c['wholeTransactionCounterassetCheck']['sourceOpeningBlock']['wNEARRaw'])),
                           source_wnear_at_transfer_block=decimal_near(int(c['wholeTransactionCounterassetCheck']['sourceTransferBlock']['wNEARRaw'])),
                           source_transaction_counterasset='none in source transaction',
                           funding_transaction=h,funding_receipt=c['receipt'],withdrawal_transaction=t,native_receipt=row['nativeReceipt'],
                           source_funding_url='https://nearblocks.io/txns/'+h,native_withdrawal_url='https://nearblocks.io/txns/'+t))
    f=io.StringIO(newline='');w=csv.DictWriter(f,fieldnames=list(public[0]));w.writeheader();w.writerows(public)
    release_file(PUBLIC_SIX,f.getvalue(),verify)
    for v in exhibits:
        print(v['owner'][:10],v['nativePayoutNEAR'],len(v['creditReceipts']),'credit receipts; signed wNEAR', decimal_near(sum(int(q['ownerSignedExchangeWNEARRaw']) for q in v['creditReceipts'])))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--collect',action='store_true');p.add_argument('--verify',action='store_true')
    a=p.parse_args();derive(a.collect,a.verify)
