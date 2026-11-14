"""Capture all single verifier wNEAR credits >= a predeclared size during the full historical study."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse,gzip,json,hashlib
from datetime import datetime,timezone
from compute.src.collectors.history import DATA,IDS,ORIGINAL_START,ORIGINAL_END,WRAP,api
from compute.src.collectors.receipts import fetch_tx,RPC,archived,verify_candidate,outcome_events
from compute.src.collectors.exchanges import verify_sigs
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed

LARGE=DATA/'large-credits'

def load_tier(threshold,fetch=False):
    result=[]
    for owner in IDS:
        cursor=None
        for i in range(1000):
            req=dict(account_id=owner,from_timestamp_ms=ORIGINAL_START//10**6-1000,to_timestamp_ms=ORIGINAL_END//10**6+1000,asset_id=WRAP,direction='receiver',
                     min_amount=str(threshold*10**24),limit=100,desc=False)
            if cursor:req['resume_token']=cursor
            p=LARGE/f'{threshold}-{owner[:8]}-{i:04}.json.gz'
            if not p.is_file():
                if not fetch:raise FileNotFoundError(p)
                headers={};raw=api(req,headers_out=headers)
                data={'request':req,'responseHeaders':headers,'retrievedAt':datetime.now(timezone.utc).isoformat(),'sha256':hashlib.sha256(raw).hexdigest(),'raw':raw.decode()}
                path=p.with_suffix('.part');path.write_bytes(gzip.compress(json.dumps(data,separators=(',',':')).encode(),mtime=0));path.replace(p)
            d=json.loads(gzip.decompress(p.read_bytes()));assert d['request']==req and hashlib.sha256(d['raw'].encode()).hexdigest()==d['sha256']
            b=json.loads(d['raw'])
            for r in b['transfers']:
                assert r['asset_id']==WRAP and r['account_id']==owner and int(r['amount'])>=threshold*10**24
                if ORIGINAL_START<int(r['block_timestamp'])<=ORIGINAL_END:result.append(r)
            nxt=b.get('resume_token')
            if not nxt:break
            assert nxt!=cursor and b['transfers'];cursor=nxt
        else:raise ValueError('Large credit page limit')
    keys=[(r['account_id'],r['block_height'],r['transfer_index']) for r in result]
    assert len(set(keys))==len(keys)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--threshold',type=int,default=1000);p.add_argument('--fetch',action='store_true');p.add_argument('--rpc',action='store_true');args=p.parse_args();LARGE.mkdir(exist_ok=True)
    rows=load_tier(args.threshold,args.fetch); print('threshold',args.threshold,'rows',len(rows),'raw_credit_wnear',sum(int(r['amount']) for r in rows)/1e24,'unique_tx',len({r['transaction_id'] for r in rows}),flush=True)
    if not args.rpc:
        raise SystemExit(0)
    txs={r['transaction_id']:r for r in rows if r['transaction_id']};RPC.mkdir(exist_ok=True)
    if args.rpc:
        with archived() as z:known={n[:-5] for n in z.namelist()}
        for tx,r in txs.items():
            if tx not in known:
                fetch_tx({'tx':tx,'signer':r['signer_id']})
    with archived() as z:
        known=set(z.namelist()); groups=defaultdict(list);verified=[];signatures=[]
        for r in rows:groups[r['transaction_id'],r['receipt_id'],r['account_id']].append(r)
        for (tx,rid,owner),group in groups.items():
            if not tx:continue
            if tx+'.json' in known:raw=z.read(tx+'.json')
            else:
                path=RPC/(tx+'.json.gz')
                if not path.is_file():continue
                raw=gzip.decompress(path.read_bytes())
            x=json.loads(raw)['result']; events=outcome_events(x,rid)
            evidence=Counter();net=0
            for e in events:
                if e['standard']!='nep245':continue
                for d in e['data']:
                    f=d.get('old_owner_id',d.get('owner_id') if e['event']=='mt_burn' else None)
                    t=d.get('new_owner_id',d.get('owner_id') if e['event']=='mt_mint' else None)
                    for k,v in zip(d.get('token_ids',[]),d.get('amounts',[]),strict=True):
                        if k=='nep141:wrap.near':
                            net+=(int(t==owner)-int(f==owner))*int(v)
                            if t==owner:evidence[f,int(v)]+=1
            obs=Counter((r['other_account_id'],int(r['amount'])) for r in group)
            assert all(evidence[k]>=v for k,v in obs.items()),(tx,'indexed credit absent from canonical receipt')
            c={'collector':owner,'tx':tx,'stamp':int(group[0]['block_timestamp']),'rid':rid,'block':int(group[0]['block_height']),'w':net,'debits':{}}
            record,sigs=verify_candidate(c,raw);record['new_to_original_794']=str(tx+'.json' not in known).lower()
            record['qualifying_credits_raw']=str(sum(int(r['amount']) for r in group));verified.append(record);signatures.extend(sigs)
        if signatures:verify_sigs(signatures)
        summary={'thresholdNEAR':args.threshold,'period':'Pinned Aug 23 through Oct 28 cutoff','indexedQualifyingCredits':len(rows),'indexedAmountRaw':str(sum(int(r['amount']) for r in rows)),'uniqueTransactions':len(txs),
                 'qualifyingReceipts':len(groups),'verifiedReceipts':len(verified),'signedExchangeIntents':len({h for r in verified for h in r['signed_exchange_intents'].split('|') if h}),'matchedExchangeWrappedNearRaw':str(sum(int(r['signed_exchange_wnear_raw']) for r in verified)),
                 'newToWithdrawalSampleReceipts':sum(r['new_to_original_794']=='true' for r in verified),'verified':verified}
        (LARGE/f'rpc-preview-{args.threshold}.json').write_text(json.dumps(summary,indent=2)+'\n')
        print({k:v for k,v in summary.items() if k!='verified'})
