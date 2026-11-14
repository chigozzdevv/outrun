"""Test the shared wNEAR sender found in the six largest treasury withdrawal paths."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
from collections import Counter,defaultdict
import csv
import hashlib
import io
import json
from datetime import datetime,timezone
from compute.src.collectors.history import api,WRAP
from compute.src.treasury.inflows import OUT,PUBLIC,ROOT,decimal_near,ledger,indexed_trees,release_file,packet
from compute.src.treasury.largest import capture

SRC=OUT/'shared-source-eligible'
PUBLIC_MATCHES=ROOT/'public/data/near-intents-common-source-transfer-matches-2026-09-01-to-2026-10-30.csv'
TIMING=OUT/'shared-source-timing.json'
START=int(datetime(2026,9,1,tzinfo=timezone.utc).timestamp()*1000)
END=int(datetime(2026,10,31,tzinfo=timezone.utc).timestamp()*1000)

def funding_account():
    sample=json.loads((OUT/'largest-withdrawals-summary.json').read_text())
    senders={t['authorizer'] for x in sample['owners'] for r in x['creditReceipts'] for t in r['explicitIncomingTransfers']}
    assert len(senders)==1
    return senders.pop()

def history(owner,collect):
    floor=min(int(r['amountYocto']) for r in ledger() if r['predecessor']=='intents.near')
    # Smaller debits cannot equal any verifier-origin native payout in this ledger.
    base=dict(account_id=owner,asset_id=WRAP,direction='sender',min_amount=str(floor),from_timestamp_ms=START-1000,to_timestamp_ms=END+1000,desc=False,limit=100)
    cursor=None;seen=set();rows=[]
    for ix in range(250):
        req=base if cursor is None else dict(base,resume_token=cursor)
        tx=capture(SRC/f'{ix:04}.json.gz','https://transfers.main.fastnear.com/v0/transfers',req,collect,api)
        rows.extend(tx['transfers'])
        cursor=tx.get('resume_token')
        print('Shared source outgoing page',ix+1,'rows',len(rows),'complete',not cursor,flush=True)
        if not cursor:break
        assert cursor not in seen and tx['transfers'];seen.add(cursor)
    else:raise ValueError('Source incomplete')
    assert len(rows)==len({(r['block_height'],r['transfer_index']) for r in rows})
    assert all(r['account_id']==owner and r['asset_id']==WRAP and -int(r['amount'])>=floor for r in rows)
    scoped=[v for v in rows if START*10**6<=int(v['block_timestamp'])<END*10**6]
    # These initial unfiltered pages are an independent check on the exact
    # candidate threshold in the period where both complete segments overlap.
    prefix=OUT/'shared-source';uncensored=[];cursor=None
    plain={k:v for k,v in base.items() if k!='min_amount'}
    for path in sorted(prefix.glob('[0-9]*.json.gz')):
        req=plain if cursor is None else dict(plain,resume_token=cursor)
        page=capture(path,'https://transfers.main.fastnear.com/v0/transfers',req,False,api)
        uncensored.extend(page['transfers']);cursor=page.get('resume_token')
    assert uncensored
    key=lambda v:(int(v['block_height']),int(v['transfer_index']))
    end=max(map(key,uncensored))
    inPrefix={key(v) for v in uncensored if START*10**6<=int(v['block_timestamp'])<END*10**6 and -int(v['amount'])>=floor}
    assert inPrefix=={key(v) for v in scoped if key(v)<=end}
    return scoped,floor,dict(unfilteredPrefixRows=len(uncensored),thresholdEligiblePrefixRows=len(inPrefix),lastBlockHeight=end[0])

def run(collect,verify=False):
    SRC.mkdir(exist_ok=True);owner=funding_account();movements,floor,prefix=history(owner,collect)
    originals=list(csv.DictReader(PUBLIC.open()))
    withdrawals={r['transaction_hash']:r for r in originals if r['withdrawal_owner']}
    six=json.loads((OUT/'largest-withdrawals-summary.json').read_text())['owners']
    proved={r['nativeWithdrawalTransaction']:r['creditReceipts'][0]['transaction'] for r in six}
    byowner=defaultdict(list)
    for m in movements: byowner[m['other_account_id']].append(m)
    # Compare the funding block to the initiating verifier debit, not the later
    # native delivery. A transfer between the two could not fund that withdrawal.
    need={h:r for h,r in withdrawals.items() if any(-int(v['amount'])==int(r['amountYocto']) for v in byowner[r['withdrawal_owner']])}
    def pinned_index():
        selected=json.loads(TIMING.read_text())
        assert selected.keys()==need.keys()
        for path in sorted({v for v in selected.values() if not v.startswith(str(SRC.relative_to(ROOT))+'/header-')}):
            _,txs=packet(ROOT/path)
            for tx in txs:
                if selected.get(tx['transaction']['hash'])==path:yield ROOT/path,tx
    debit_times={};sources={}
    for path,tx in (pinned_index() if verify else indexed_trees(set(need))):
        h=tx['transaction']['hash'];r=need[h]
        d=next(e['execution_outcome'] for e in tx['receipts'] if e['execution_outcome'] and e['execution_outcome']['id']==r['verifier_receipt_id'])
        assert d['block_hash']==r['verifier_block_hash']
        assert h not in debit_times or debit_times[h]==int(d['block_timestamp'])
        sources.setdefault(h,str(path.relative_to(ROOT)))
        debit_times[h]=int(d['block_timestamp'])
    if need.keys()-debit_times.keys():
        from compute.src.treasury.intermediary import RPC_HOST,request_rpc
        for h in sorted(need.keys()-debit_times.keys()):
            block=need[h]['verifier_block_hash']
            req=dict(jsonrpc='2.0',id='treasury-source-sequence',method='block',params=dict(block_id=block))
            path=SRC/('header-'+block+'.json.gz')
            reply=capture(path,'https://'+RPC_HOST,req,collect,request_rpc)
            header=reply['result']['header']
            assert header['hash']==block
            sources[h]=str(path.relative_to(ROOT))
            debit_times[h]=int(header['timestamp_nanosec'])
    release_file(TIMING,json.dumps(sources,sort_keys=True,indent=2)+'\n',verify)
    candidates=[];use=Counter()
    for r in originals:
        if r['transaction_hash'] not in debit_times:continue
        amount=int(r['amountYocto'])
        root_time=int(datetime.fromisoformat(r['receipt_utc'][:-1]).replace(tzinfo=timezone.utc).timestamp())*10**9+int(r['receipt_utc'].split('.')[1][:-1])
        possible=[t for t in byowner[r['withdrawal_owner']] if -int(t['amount'])==amount and int(t['block_timestamp'])<debit_times[r['transaction_hash']]]
        if len(possible)!=1:continue
        transfer=possible[0];key=(transfer['receipt_id'],transfer['transfer_index'])
        use[key]+=1
        candidates.append((key,dict(withdrawingOwner=r['withdrawal_owner'],sharedSource=owner,fundingTransaction=transfer['transaction_id'],fundingReceipt=transfer['receipt_id'],
                            withdrawalTransaction=r['transaction_hash'],nativeReceipt=r['native_receipt_id'],nativeReceiptUTC=r['receipt_utc'],payoutNEAR=r['native_near'],amountYocto=r['amountYocto'],treasury=r['treasury_recipient'],ownerInOriginalThree=r['owner_in_original_three'],
                            gapToVerifierDebitNs=str(debit_times[r['transaction_hash']]-int(transfer['block_timestamp'])),gapToNativeReceiptNs=str(root_time-int(transfer['block_timestamp'])),
                            completeIntermediateHistoryAndSignedFunding=str(proved.get(r['transaction_hash'])==transfer['transaction_id']).lower(),
                            fundingTransactionURL='https://nearblocks.io/txns/'+transfer['transaction_id'],withdrawalTransactionURL=r['source_transaction_url'])))
    matches=[r for key,r in candidates if use[key]==1]
    assert all(any(r['withdrawalTransaction']==tx and r['completeIntermediateHistoryAndSignedFunding']=='true' for r in matches) for tx in proved)
    splits=defaultdict(list)
    for row in matches:
        splits[(row['nativeReceiptUTC'][:7],row['ownerInOriginalThree'])].append(row)
    gaps=sorted(int(r['gapToVerifierDebitNs']) for r in matches)
    stats=dict(sharedSource=owner,minimumCandidateWNEAR=decimal_near(floor),completeEligibleOutgoingMovements=len(movements),distinctEligibleSourceOutgoingRecipients=len({r['other_account_id'] for r in movements if r['other_account_id']}),
               totalEligibleSourceOutgoingWNEAR=decimal_near(sum(-int(r['amount']) for r in movements)),exactEarlierAmountOwnerMatches=len(matches),
               matchedNativePayoutNEAR=decimal_near(sum(int(r['amountYocto']) for r in matches)),distinctMatchedWithdrawalOwners=len({r['withdrawingOwner'] for r in matches}),
               exactAmountOwnerCandidateTransactionsMissingVerifierTime=len(need.keys()-debit_times.keys()),
               transferThresholdCrossCheck=prefix,
               matchedMonthAndOwnerScope=[dict(month=key[0],ownerScope='original_three' if key[1]=='true' else 'additional',receipts=len(group),nativeNEAR=decimal_near(sum(int(r['amountYocto']) for r in group))) for key,group in sorted(splits.items())],
               matchesWithinOneMinuteOfFunding=sum(v<=60*10**9 for v in gaps),matchesWithinOneHourOfFunding=sum(v<=3600*10**9 for v in gaps),
               scope='The public index shows an earlier wNEAR transfer from the shared ledger source to the same initiating owner for the exact amount of each matched payout. These pairs do not establish each intermediate account\'s whole history, independence, or the economic source of the shared sender\'s stock. The six detailed cases have separately checked complete intermediate histories and authorized transfer messages.')
    f=io.StringIO(newline='');w=csv.DictWriter(f,fieldnames=list(matches[0]));w.writeheader();w.writerows(matches)
    release_file(PUBLIC_MATCHES,f.getvalue(),verify)
    stats['sourceDigests']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [PUBLIC,PUBLIC_MATCHES]}
    release_file(OUT/'shared-source-summary.json',json.dumps(stats,indent=2)+'\n',verify)
    print(json.dumps(stats,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--collect',action='store_true');p.add_argument('--verify',action='store_true')
    a=p.parse_args();run(a.collect,a.verify)
