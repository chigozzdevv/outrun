"""Read complete finalized block/shard data and verify selected collector receipts and signed exchanges."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse,collections,base64,csv,fcntl,gzip,hashlib,http.client,json,os,ssl,subprocess,threading,time,uuid
from datetime import datetime,timezone
from tempfile import NamedTemporaryFile
from decimal import Decimal,localcontext
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from compute.src.collectors.history import ROOT,DATA,IDS,WRAP,VERIFIER,utc
from compute.src.collectors.receipts import rows_available,candidates,verify_effects,RPC,SAMPLE_END
from compute.src.collectors.large import load_tier
from compute.src.collectors.exchanges import nep413,b58,verify_sigs

BLOCKS=DATA/'finalized-blocks';MEMBERS=set(IDS)
lock=threading.Lock();next_request=0.0
PACE_FILE=Path('/private/tmp/near-collector-finalized-block-rate')

def block_pace(delay=None):
    # The naked host redirects to an archive host. An interval above four seconds
    # respects the published 30/min ceiling even when both HTTP legs count.
    ticket=uuid.uuid4().hex
    priority=os.environ.get('NEARDATA_FIXED_COHORT_PRIORITY')=='1'
    pending_path=PACE_FILE.with_suffix('.queue')
    PACE_FILE.touch(exist_ok=True)
    while True:
        with PACE_FILE.open('r+') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            value=f.read();next_time=float(value) if value else 0;now=time.time()
            if delay is not None:
                f.seek(0);f.truncate();f.write(str(max(next_time,now+delay)));f.flush();return
            state=json.loads(pending_path.read_text()) if pending_path.exists() else {'pending':[],'priorityRun':0}
            q=[r for r in state['pending'] if r['heartbeat']>now-25]
            me=next((r for r in q if r['ticket']==ticket),None)
            if me is None:q.append(dict(ticket=ticket,heartbeat=now,priority=priority))
            else:me['heartbeat']=now
            high=[r for r in q if r['priority']];regular=[r for r in q if not r['priority']]
            winner=high[0] if high and (not regular or state['priorityRun']<2) else (regular or high)[0]
            granted=winner['ticket']==ticket and now>=next_time
            if granted:
                q.remove(winner);state['priorityRun']=state['priorityRun']+1 if priority else 0
                f.seek(0);f.truncate();f.write(str(now+4.15));f.flush()
            state['pending']=q
            with NamedTemporaryFile(mode='w',dir=pending_path.parent,delete=False) as pending:
                json.dump(state,pending);tmp=Path(pending.name)
            tmp.replace(pending_path)
            if granted:return
        time.sleep(min(1,max(.15,next_time-time.time())))


def fetch_height(h):
    global next_request
    file=BLOCKS/(str(h)+'.json.gz')
    if file.is_file():return True
    url=f'https://mainnet.neardata.xyz/v0/block/{h}'
    for attempt in range(4):
        block_pace()
        try:
            result=subprocess.run(['curl','-sSL','--compressed','--connect-timeout','10','--max-time','28','-w','\n%{http_code}\t%{url_effective}',url],capture_output=True)
            raw,meta=result.stdout.rsplit(b'\n',1);code,location=meta.split(b'\t')
            if result.returncode or code!=b'200':
                diagnostic=dict(time=datetime.now(timezone.utc).isoformat(),height=h,attempt=attempt+1,curl=result.returncode,http=code.decode(),url=location.decode(),stderr=result.stderr.decode(errors='replace')[:320],errorBody=raw[:180].decode(errors='replace') if code!=b'200' else '')
                with (DATA/'finalized-block-errors.jsonl').open('a') as log:log.write(json.dumps(diagnostic)+'\n')
                if attempt==3:print('block request failed',json.dumps(diagnostic),flush=True)
            if code==b'429':
                block_pace(30)
            if result.returncode or code!=b'200':raise ValueError(code)
            x=json.loads(raw)
            if x is not None:
                assert x['block']['header']['height']==h and isinstance(x['shards'],list)
            with NamedTemporaryFile(dir=BLOCKS,delete=False,suffix='.part') as handle:
                handle.write(gzip.compress(raw,mtime=0));temp=Path(handle.name)
            temp.replace(file)
            return True
        except (ValueError,subprocess.CalledProcessError,OSError):
            time.sleep(attempt+1)
    return False

def all_receipts_already_archived(targets):
    """A full successful finalized receipt in the existing source cache needs no second full block."""
    if not targets:return False
    archive_path=ROOT/'data/verified/withdrawal-rpc-2026-11-02/archival-responses.zip'
    from zipfile import ZipFile
    with ZipFile(archive_path) as archive:
        original=set(archive.namelist())
        for rid,tx in targets:
            if not tx:return False
            file=RPC/(tx+'.json.gz')
            if file.exists():raw=gzip.decompress(file.read_bytes())
            elif tx+'.json' in original:raw=archive.read(tx+'.json')
            else:return False
            x=json.loads(raw)['result'];assert x['transaction']['hash']==tx and x['final_execution_status']=='FINAL'
            outcome=next((v for v in x['receipts_outcome'] if v['id']==rid),None)
            if not outcome:return False
            assert outcome['outcome']['executor_id']=='intents.near' and any(k.startswith('Success') for k in outcome['outcome']['status'])
    return True

def fetch_or_reuse(h,targets):
    return all_receipts_already_archived(targets) or fetch_height(h)


def from_actions(actions):
    signed={}
    for a in actions:
        if 'Delegate' in a:signed.update(from_actions(a['Delegate']['delegate_action']['actions']))
        fc=a.get('FunctionCall',{})
        if fc.get('method_name')!='execute_intents':continue
        params=json.loads(base64.b64decode(fc['args']))
        for s in params['signed']:
            if s.get('standard')=='nep413':signed[b58(nep413(s))]=s
    return signed


def effects(entry):
    receipt=entry['receipt']; exe=entry['execution_outcome']; output=exe['outcome']
    assert receipt['receipt_id']==exe['id'] and receipt['receiver_id']==output['executor_id']
    if output['executor_id']!='intents.near' or not any(k.startswith('Success') for k in output['status']):return None
    events=[json.loads(log[11:]) for log in output['logs'] if log.startswith('EVENT_JSON:')]
    amounts=collections.defaultdict(collections.Counter); all_moves=[]
    for ix,event in enumerate(events):
        kind=event['event']
        if kind not in ('mt_mint','mt_burn','mt_transfer'):continue
        assert event['standard']=='nep245'
        for di,row in enumerate(event['data']):
            a=row.get('old_owner_id',row.get('owner_id') if kind=='mt_burn' else None)
            b=row.get('new_owner_id',row.get('owner_id') if kind=='mt_mint' else None)
            for owner in MEMBERS & {a,b}:
                for k,v in zip(row['token_ids'],row['amounts'],strict=True):
                    raw=(int(owner==b)-int(owner==a))*int(v)
                    amounts[owner][k]+=raw
                    all_moves.append(dict(owner=owner,asset=k,change=raw,origin=a,target=b,kind=kind,event=di,memo=row.get('memo','')))
    return receipt['receipt_id'],entry['tx_hash'],events,from_actions(receipt['receipt'].get('Action',{}).get('actions',[])),amounts,all_moves


def block_proofs(index_rows):
    by_key=collections.defaultdict(list)
    for r in index_rows:by_key[r['account_id'],r['receipt_id']].append(r)
    proofs={}; signatures={}; data_moves=[]; presence=set(); observed={}
    for p in sorted(BLOCKS.glob('*.json.gz')):
        x=json.loads(gzip.decompress(p.read_bytes()))
        if x is None:continue
        h=x['block']['header']; assert int(p.name.split('.')[0])==h['height']
        for shard in x['shards']:
            for entry in shard['receipt_execution_outcomes']:
                assert entry['execution_outcome']['block_hash']==h['hash']
                state=effects(entry)
                if not state:continue
                rid,tx,events,signed,amounts,moves=state
                if not amounts:continue
                for owner,net in amounts.items():
                    key=(owner,rid); obs=by_key.get(key,[])
                    if not obs:continue
                    assert all(r['transaction_id'] in (None,tx) for r in obs)
                    assert all(int(r['block_height'])==h['height'] and int(r['block_timestamp'])==int(h['timestamp_nanosec']) for r in obs)
                    data=[m for m in moves if m['owner']==owner]
                    observed[key]=(tx,h, data)
                    canon=collections.Counter((m['asset'],m['origin'] if m['change']>0 else m['target'],m['change']) for m in data)
                    indexed=collections.Counter((r['asset_id'][len(VERIFIER):],r['other_account_id'],int(r['amount'])) for r in obs)
                    # A query for one direction can return only part of the receipt.
                    assert all(canon[k]>=n for k,n in indexed.items()),(tx,'index row absent from canonical source',indexed-canon)
                    presence.add(key)
                    if not any(m['change']>0 and m['asset']=='nep141:wrap.near' for m in data):continue
                    debit=collections.Counter()
                    for m in data:
                        if m['asset']!='nep141:wrap.near' and m['change']<0:debit[m['asset']]-=m['change']
                    c={'collector':owner,'tx':tx,'stamp':int(h['timestamp_nanosec']),'rid':rid,'block':h['height'],'w':net['nep141:wrap.near'],'debits':debit}
                    v,sigs=verify_effects(c,events,signed);v['canonical_block_hash']=h['hash'];v['proof_source']='NEAR Data finalized block';proofs[key]=v
                    signatures.update({sig['hash']:sig for sig in sigs})
    if signatures:verify_sigs(list(signatures.values()))
    return proofs,presence,observed


def targets(tier, earliest):
    if tier:return load_tier(tier)
    raw=rows_available();possible=candidates(raw)
    return [dict(account_id=c['collector'],receipt_id=c['rid'],transaction_id=c['tx'],block_height=str(c['block']),block_timestamp=str(c['stamp']),amount=str(c['w']),asset_id=WRAP) for c in possible if not earliest or c['stamp']<SAMPLE_END]


def pull(tier,firstday,batch):
    BLOCKS.mkdir(exist_ok=True)
    rows=targets(tier,firstday); best={};required=collections.defaultdict(set)
    for x in rows:
        h=int(x['block_height'])
        if h not in best:best[h]=0
        best[h]+=int(x['amount'])
        required[h].add((x['receipt_id'],x['transaction_id']))
    missing=[h for h in sorted(best,key=lambda h:-best[h]) if not (BLOCKS/(str(h)+'.json.gz')).is_file()][:batch]
    print('eligible heights',len(best),'fetch batch',len(missing),flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending={pool.submit(fetch_or_reuse,h,required[h]):h for h in missing}
        for i,f in enumerate(as_completed(pending),1):
            if not f.result():print('unavailable',pending[f],flush=True)
            if i%25==0:print('received',i,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--tier',type=int);p.add_argument('--firstday',action='store_true');p.add_argument('--fetch',action='store_true');p.add_argument('--batch',type=int,default=100);opt=p.parse_args()
    if opt.fetch:pull(opt.tier,opt.firstday,opt.batch)
    else:
        index=rows_available()+(load_tier(opt.tier) if opt.tier else [])
        distinct={}
        for r in index:distinct[r['account_id'],r['block_height'],r['transfer_index'],r['amount']]=r
        proofs,pres,obs=block_proofs(list(distinct.values()));print('canonical receipts',len(pres),'received wNEAR',len(proofs),'exchange intent receipts',sum(int(v['signed_exchange_wnear_raw'])>0 for v in proofs.values()))
