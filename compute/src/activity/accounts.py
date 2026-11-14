"""Reproduce the ten-minute fee-account baseline, eligible follow-up windows and transaction-linked evidence.
Run with --build to emit artifacts and without it to verify every byte. All calculations use saved source responses.
"""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import csv
import gzip
import hashlib
import io
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


DATA = ROOT/'data/verified/paid-activity-2026-11-02'
PUBLIC = ROOT/'public/data'
COLLECTOR = '7066024d3f20f94de601c003163367873cca78507eeca4df66d9be645f197f05'
MT = 'nep245:intents.near:'
UNIT = 10**9
def ns(date):
    return int(datetime.fromisoformat(date.replace('Z','+00:00')).timestamp())*UNIT
def utc(value):
    seconds,nanos=divmod(int(value),UNIT)
    return datetime.fromtimestamp(seconds,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+f'.{nanos:09}Z'
BASE = (ns('2026-08-01T12:00:00Z'),ns('2026-08-01T12:10:00Z'))
WINDOWS = {'days1to30':(ns('2026-08-02T00:00:00Z'),ns('2026-09-01T00:00:00Z')), 'days61to90':(ns('2026-10-01T00:00:00Z'),ns('2026-10-31T00:00:00Z'))}
AGG = {r['collector_account'] for r in csv.DictReader((PUBLIC/'withdrawal-matches-2026-10-28.csv').open())}

def unpack(path):
    return json.loads(gzip.decompress(path.read_bytes()))

def source_manifest():
    descriptions={'index':'NearBlocks paginated NEP-245 per-account index, with exact GET URL and raw response','transfer':'FastNear account transfer index, with exact POST parameters','tx':'Final archival NEAR RPC transaction and all receipt outcomes','block':'NEAR Data historical block with receipt execution outcomes','configuration':'Archival contract fee/collector view at the first observed baseline block'}
    entries=[]
    for label in descriptions:
        suffix='json' if label=='configuration' else 'json.gz'
        for path in sorted(DATA.glob(f'{label}-*.{suffix}')):
            entries.append(dict(file=path.name,kind=label,bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    access=DATA/'explorer-access.json';probe=json.loads(access.read_text())
    assert probe['request']['authentication']=='none' and probe['response']['status']==400
    assert hashlib.sha256(probe['response']['raw'].encode()).hexdigest()==probe['response']['sha256'] and json.loads(probe['response']['raw'])['error']['code']=='MISSING_TOKEN'
    descriptions['access']='Direct response to the unauthenticated official Explorer transaction request'
    entries.append(dict(file=access.name,kind='access',bytes=access.stat().st_size,sha256=hashlib.sha256(access.read_bytes()).hexdigest()))
    return (json.dumps(dict(sources=descriptions,files=entries),indent=2)+'\n').encode()

class Sources:
    def __init__(self):
        self.index={};self.fast={};self.tx={};self.rpc_count=0;self.block_time={};self.block_transactions={}
        for p in DATA.glob('index-*.json.gz'):
            record=unpack(p);assert hashlib.sha256(record['raw'].encode()).hexdigest()==record['rawSha256']
            assert p.name=='index-'+hashlib.sha256(record['url'].encode()).hexdigest()[:20]+'.json.gz'
            assert json.loads(record['raw'])==record['body']
            url=urlsplit(record['url']);a=url.path.split('/')[-2];assert url.netloc=='api.nearblocks.io'
            args={k:v[0] for k,v in parse_qs(url.query).items()}
            assert args['contract']=='intents.near';key=(a,args.get('involved',''),int(args['before_ts']),int(args['limit']),args.get('next',''));assert key not in self.index
            self.index[key]=record['body']
        for p in DATA.glob('transfer-*.json.gz'):
            record=unpack(p);assert record['endpoint']=='https://transfers.main.fastnear.com/v0/transfers'
            q=record['request'];assert q['direction']=='sender' and q['desc'] is False and q['limit']==100
            assert p.name=='transfer-'+hashlib.sha256(json.dumps(q,sort_keys=True).encode()).hexdigest()[:20]+'.json.gz'
            key=(q['account_id'],q['from_timestamp_ms'],q['to_timestamp_ms'],q.get('resume_token',''));assert key not in self.fast
            self.fast[key]=record['body']
        for p in DATA.glob('tx-*.json.gz'):
            r=unpack(p);request=r['request'];params=request['params'];x=r['response']['result'];tx=x['transaction']['hash']
            assert r['endpoint'] in ('https://archival-rpc.mainnet.fastnear.com','https://archival-rpc.mainnet.near.org') and request['method']=='EXPERIMENTAL_tx_status' and params['wait_until']=='FINAL' and params['tx_hash']==tx and x['final_execution_status']=='FINAL' and p.name==f'tx-{tx}.json.gz'
            self.tx[tx]=x
        self.rpc_count=len(self.tx)
        self.stream_count=0
        for p in DATA.glob('block-*.json.gz'):
            blob=unpack(p);j=blob['body'];header=j['block']['header'];height=int(header['height'])
            assert p.name==f'block-{height}.json.gz' and blob['source']==f'https://mainnet.neardata.xyz/v0/block/{height}'
            self.block_time[header['hash']]=int(header['timestamp_nanosec'])
            self.block_transactions[header['hash']]={t['transaction']['hash'] for sh in j['shards'] for t in (sh.get('chunk') or {}).get('transactions',[])}
            for sh in j['shards']:
                for entry in sh['receipt_execution_outcomes']:
                    ro=entry['execution_outcome'];receipt=entry['receipt'];tx=entry.get('tx_hash')
                    if not tx or ro['outcome']['executor_id']!='intents.near':continue
                    assert ro['block_hash']==header['hash'] and ro['id']==receipt['receipt_id'] and receipt['receiver_id']=='intents.near'
                    if tx in self.tx and 'final_execution_status' in self.tx[tx]:
                        old=next((v for v in self.tx[tx]['receipts_outcome'] if v['id']==ro['id']),None)
                        assert old and old['block_hash']==ro['block_hash'] and old['outcome']['logs']==ro['outcome']['logs']
                        continue
                    if tx not in self.tx:
                        self.tx[tx]={'transaction':{'hash':tx,'signer_id':receipt['receipt'].get('Action',{}).get('signer_id',''),'actions':[]},'receipts_outcome':[],'receipts':[],'blockStream':True}
                        self.stream_count+=1
                    if not any(o['id']==ro['id'] for o in self.tx[tx]['receipts_outcome']):
                        self.tx[tx]['receipts_outcome'].append(ro); self.tx[tx]['receipts'].append(receipt)

    def near_pages(self,account,other,before,limit):
        seen=set();cursor='';last=None;data=[]
        while (account,other,before,limit,cursor) in self.index:
            assert cursor not in seen;seen.add(cursor)
            j=self.index[(account,other,before,limit,cursor)];rows=j['data']
            for r in rows:
                t=int(r['block_timestamp']);assert t<before and (last is None or t<=last)
                assert r['affected_account_id']==account and r['contract_account_id']=='intents.near'
                if other:assert r['involved_account_id']==other
                last=t
            data.extend(rows);nxt=j.get('meta',{}).get('next_page')
            if not nxt:return data,True
            cursor=nxt
        return data,False

    def witness(self,row,account):
        tx=row['transaction_hash'];x=self.tx.get(tx)
        if not x:return None,False
        ro=next((r for r in x['receipts_outcome'] if r['id']==row['receipt_id']),None)
        if ro is None and x.get('blockStream'): return None,False
        assert ro is not None
        o=ro['outcome'];assert o['executor_id']=='intents.near' and any(k.startswith('Success') for k in o['status'])
        logs=[json.loads(s[11:]) for s in o['logs'] if s.startswith('EVENT_JSON:')]
        def data(std,e):
            return [d for ev in logs if (ev['standard'],ev['event'])==(std,e) for d in ev['data']]
        executed={(d['account_id'],d['intent_hash']) for d in data('dip4','intents_executed')}
        coin=row['token_id'];legs=[d for d in data('dip4','token_diff') if d['account_id']==account and (account,d['intent_hash']) in executed and int(d.get('fees_collected',{}).get(coin,0))>0 and int(d['diff'].get(coin,0))<0]
        amount=abs(int(row['delta_amount']))
        direct=[int(v) for d in data('nep245','mt_transfer') if d['old_owner_id']==account and d['new_owner_id']==COLLECTOR for t,v in zip(d['token_ids'],d['amounts'],strict=True) if t==coin]
        if not legs or amount not in direct:return None,True
        hashes={d['intent_hash'] for d in legs}
        transfers=[d for d in data('dip4','transfer') if d['account_id']==account and d['intent_hash'] in hashes]
        aggregate=[dict(receiver=t['receiver_id'],token=tok,units=u,intentHash=t['intent_hash']) for t in transfers if t['receiver_id'] in AGG for tok,u in t['tokens'].items() if int(u)>0]
        refs=sorted({d['referral'] for d in legs if d.get('referral')})
        tag='configured aggregation account' if account in AGG else '1Click-referred intent' if any(t.startswith('1click-') for t in refs) else 'counterparty in a 1Click-referred execution' if any(d['account_id']!=account and str(d.get('referral','')).startswith('1click-') for d in data('dip4','token_diff')) else 'other charged signer'
        block=row.get('block',{}).get('block_hash')
        relation='index did not supply hash' if block is None else 'receipt execution' if block==ro['block_hash'] else 'original transaction inclusion'
        if relation=='original transaction inclusion':
            # Some index records join the original transaction block to a later event.
            # Confirm BOTH headers, actual transaction inclusion, receipt ID and time.
            assert tx in self.block_transactions.get(block,set()) and ro['block_hash'] in self.block_time
        if ro['block_hash'] in self.block_time:
            assert self.block_time[ro['block_hash']]==int(row['block_timestamp']), 'Index timestamp differs from receipt block'
        return dict(account=account,accountRoleInWitness=tag,proofSource='finalized RPC' if 'final_execution_status' in x else 'NEAR Data receipt execution in block',tx=tx,receiptId=ro['id'],utc=utc(row['block_timestamp']),rpcBlockHash=ro['block_hash'],indexedBlockHash=block,indexedBlockRelation=relation,token=coin,feeAtomic=str(sum(int(d['fees_collected'][coin]) for d in legs)),collectorCreditAtomic=str(amount),intentHashes=sorted(hashes),referrals=refs,aggregatorTransfers=aggregate,relayer=x['transaction']['signer_id']),True

    def near_follow(self,account,w,start,end):
        # Separate cutoffs in the pilot, combined histories in the full cohort and bounded fallback pages.
        pending=False;covered=False
        for before,limit in ((end,12),(WINDOWS['days61to90'][1],25)):
            data,exhausted=self.near_pages(account,COLLECTOR,before,limit)
            if not data:continue
            # A negative result needs evidence the account index reached its known baseline fee or a later record.
            baseline_ns=ns(w['utc'][:19]+'Z')+int(w['utc'][20:29])
            last=int(data[-1]['block_timestamp']); first=int(data[0]['block_timestamp'])
            if first<baseline_ns:continue
            unresolved=False
            for row in data:
                t=int(row['block_timestamp'])
                if start<=t<end and row['cause']=='TRANSFER' and int(row['delta_amount'])<0:
                    proof,available=self.witness(row,account)
                    if proof:return 'verified-repeat',proof
                    unresolved|=not available
            pending|=unresolved
            covered|=exhausted or last<start
        return ('pending-candidate' if pending else 'no-qualifying-indexed-event' if covered else 'unknown'),None

    def fast_follow(self,account,w,start,end):
        low,high=BASE[0]//10**6,WINDOWS['days61to90'][1]//10**6
        key=(account,low,high);cursor='';seen=set();base_seen=False;exhausted=False;last=None;unresolved=False
        while (*key,cursor) in self.fast:
            assert cursor not in seen;seen.add(cursor)
            j=self.fast[(*key,cursor)]
            for r in j['transfers']:
                t=int(r['block_timestamp']);assert r['account_id']==account and low*10**6<=t<=(high+1)*10**6
                assert last is None or last<=t;last=t
                if r.get('other_account_id')!=COLLECTOR or not r['asset_id'].startswith(MT) or int(r['amount'])>=0: continue
                coin=r['asset_id'][len(MT):];amount=-int(r['amount'])
                if (r['receipt_id'],coin,amount)==(w['receiptId'],w['token'],int(w['collectorCreditAtomic'])):base_seen=True
                if start<=t<end:
                    row=dict(transaction_hash=r.get('transaction_id'),receipt_id=r['receipt_id'],block_timestamp=t,token_id=coin,delta_amount=amount)
                    proof,available=self.witness(row,account)
                    if proof:return 'verified-repeat',proof
                    unresolved|=not available
            nxt=j.get('resume_token')
            if not nxt:exhausted=True;break
            cursor=nxt
        if unresolved:return 'pending-candidate',None
        if base_seen and (exhausted or (last is not None and last>=end)):return 'no-qualifying-indexed-event',None
        return 'unknown',None

    def all_verifier_outflow_count(self,account,w,start,end):
        # This account-wide sender query has no collector filter. Require a verified
        # baseline anchor and enough cursor coverage before reporting even a zero.
        key=(account,BASE[0]//10**6,WINDOWS['days61to90'][1]//10**6)
        cursor='';covered=False;anchored=False;outflows=0
        while (*key,cursor) in self.fast:
            page=self.fast[(*key,cursor)];rows=page['transfers']
            for movement in rows:
                timestamp=int(movement['block_timestamp'])
                if (movement['receipt_id'],movement['asset_id'],movement.get('other_account_id'),-int(movement['amount']))==(w['receiptId'],MT+w['token'],COLLECTOR,int(w['collectorCreditAtomic'])):anchored=True
                if start<=timestamp<end and movement['asset_id'].startswith(MT) and int(movement['amount'])<0:outflows+=1
            cursor=page.get('resume_token')
            if not cursor or (rows and int(rows[-1]['block_timestamp'])>=end):
                covered=True;break
        return outflows if anchored and covered else ''

def derive(s):
    rows,exhausted=s.near_pages(COLLECTOR,'',BASE[1],100)
    assert rows and (exhausted or int(rows[-1]['block_timestamp'])<BASE[0])
    rows=[r for r in rows if BASE[0]<=int(r['block_timestamp'])<BASE[1]]
    assert len({(r['block_timestamp'],r['shard_id'],r['event_index']) for r in rows})==len(rows)
    first=rows[-1]['block'];cfg={}
    for method in ('fee','fee_collector'):
        raw=json.loads((DATA/f"configuration-{first['block_height']}-{method}.json").read_text())
        params=raw['request']['params'];reply=raw['response']['result']
        assert params==dict(request_type='call_function',account_id='intents.near',block_id=first['block_hash'],method_name=method,args_base64='e30=')
        assert reply['block_hash']==first['block_hash'] and reply['block_height']==int(first['block_height'])
        cfg[method]=json.loads(bytes(reply['result']))
    assert cfg['fee_collector']==COLLECTOR and cfg['fee']==1
    by=defaultdict(list)
    for r in rows:
        if r['cause']=='TRANSFER' and int(r['delta_amount'])>0 and r.get('involved_account_id'):by[r['involved_account_id']].append(r)
    accounts=sorted(by, key=lambda a:hashlib.sha256(('intent-thesis-20260801:'+a).encode()).digest())
    result=[]
    def fields(w,prefix):
        return {prefix+k:(v if isinstance(v,str) else json.dumps(v,sort_keys=True,separators=(',',':'))) for k,v in w.items() if k!='account'}
    for rank,account in enumerate(accounts,1):
        base=None;untested=False
        for r in by[account]:
            base,available=s.witness(r,account)
            if base:break
            untested|=not available
        record=dict(rank=rank,account=account,baselineStatus='verified-fee' if base else 'unknown' if untested else 'no-matching-fee-in-candidates')
        if base:record.update(fields(base,'baseline_'))
        else:
            for k in WINDOWS:record[k]='not-baseline-verified'
            result.append(record);continue
        for k,(start,end) in WINDOWS.items():
            # Prefer any check that establishes a fee, otherwise an exhausted, scoped scan.
            observations=[s.near_follow(account,base,start,end),s.fast_follow(account,base,start,end)]
            positive=next((w for status,w in observations if status=='verified-repeat'),None)
            states={status for status,_ in observations}
            record[k]='verified-repeat' if positive else 'no-qualifying-indexed-event' if 'no-qualifying-indexed-event' in states and 'pending-candidate' not in states else 'unknown'
            record[k+'_allVerifierIndexedOutflows']=s.all_verifier_outflow_count(account,base,start,end)
            if positive:record.update(fields(positive,k+'_'))
        result.append(record)
    def counts(items):
        return {k:{key:c.get(key,0) for key in ['verified-repeat','no-qualifying-indexed-event','unknown']} for k in WINDOWS for c in [Counter(r[k] for r in items if r['baselineStatus']=='verified-fee')]}
    proved=[r for r in result if r['baselineStatus']=='verified-fee']
    collector_signed=[r for r in proved if json.loads(r['baseline_aggregatorTransfers'])]
    referrals=Counter(t for r in proved for t in json.loads(r['baseline_referrals']))
    summary=dict(windows=dict(baselineStartUtc=utc(BASE[0]),baselineEndExclusiveUtc=utc(BASE[1]),days1to30StartUtc=utc(WINDOWS['days1to30'][0]),days1to30EndExclusiveUtc=utc(WINDOWS['days1to30'][1]),days61to90StartUtc=utc(WINDOWS['days61to90'][0]),days61to90EndExclusiveUtc=utc(WINDOWS['days61to90'][1])),
        baselineIndexRows=len(rows),baselinePositiveCollectorCreditRows=sum(len(r) for r in by.values()),candidateAccounts=len(result),baselineVerifiedAccounts=len(proved),indeterminateBaseline=sum(r['baselineStatus']=='unknown' for r in result),candidatesWithoutMatchingFee=sum(r['baselineStatus']=='no-matching-fee-in-candidates' for r in result),baselineConfiguration=dict(blockHeight=int(first['block_height']),blockHash=first['block_hash'],feePips=cfg['fee'],feeCollector=cfg['fee_collector']),
        cohortOutcomes=counts(result),pilotSize=24,pilotOutcomes=counts(result[:24]),outcomesByWitnessRole={role:dict(baseline=len(group),windows=counts(group)) for role in sorted({r['baseline_accountRoleInWitness'] for r in proved}) for group in [[row for row in proved if row['baseline_accountRoleInWitness']==role]]},
        verifiedInBothFollowupWindows=sum(all(r[k]=='verified-repeat' for k in WINDOWS) for r in proved),noQualifyingIndexEventInEitherWindow=sum(all(r[k]=='no-qualifying-indexed-event' for k in WINDOWS) for r in proved),
        sourceRPCTransactions=s.rpc_count,primaryBaselineRPCConfirmations=sum(r.get('baseline_proofSource')=='finalized RPC' for r in proved),primaryBaselineBlockConfirmations=sum(r.get('baseline_proofSource')=='NEAR Data receipt execution in block' for r in proved),
        baselineWithReferral=sum(bool(json.loads(r['baseline_referrals'])) for r in proved),baselineReferralCounts=dict(referrals),baselineSignersWithSameIntentTransferToKnownAggregator=len(collector_signed),
        noObservedVerifierOutflowOfAnyKind={k:sum(r[k+'_allVerifierIndexedOutflows']==0 for r in proved) for k in WINDOWS},
        scope='The full candidate population is defined by positive transfers returned by the NearBlocks account index in a predeclared ten-minute interval. Membership needs a successful finalized verifier token_diff with a positive fee, matching account and intent in intents_executed, and an actual same-asset credit to the configured protocol collector in the same receipt. Recurrence is a distinct qualifying fee in each stated calendar window. A negative observation is scoped to paginated public index coverage; unresolved histories stay unknown. This is a baseline of active signers, not first-acquisition cohorts, end customer identity, profit or continuous usage. Solver, collection, transaction-specific and other intermediary accounts may participate.')
    fields_list=['rank','account','baselineStatus']
    witness_fields=['accountRoleInWitness','proofSource','tx','receiptId','utc','rpcBlockHash','indexedBlockHash','indexedBlockRelation','token','feeAtomic','collectorCreditAtomic','intentHashes','referrals','aggregatorTransfers','relayer']
    fields_list += ['baseline_'+k for k in witness_fields]
    for k in WINDOWS: fields_list += [k,k+'_allVerifierIndexedOutflows']+[k+'_'+f for f in witness_fields]
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=fields_list,lineterminator='\n');writer.writeheader();writer.writerows(result)
    return {PUBLIC/'near-intents-paid-account-cohort-2026-11-02.csv':stream.getvalue().encode(),DATA/'summary.json':(json.dumps(summary,indent=2)+'\n').encode()}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--build',action='store_true');args=parser.parse_args()
    generated=derive(Sources())
    generated[DATA/'source-manifest.json']=source_manifest()
    for p,b in generated.items():
        if args.build:p.write_bytes(b)
        else:assert p.read_bytes()==b,f'Published evidence differs: {p.name}'
