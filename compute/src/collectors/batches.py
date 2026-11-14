"""Capture the Explorer API's raw receipt bodies in documented batches of 20, with separate source provenance."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import csv
import fcntl
import gzip
import hashlib
import http.client
import json
from pathlib import Path
import ssl
import time
from datetime import datetime,timezone
from compute.src.collectors.history import DATA,ORIGINAL_START,ORIGINAL_END,WRAP
from compute.src.collectors.opening import verify as first_day
from compute.src.collectors.receipts import rows_available
from compute.src.collectors.large import load_tier
from compute.src.collectors.inventory import complete_chains

HOST='tx.main.fastnear.com'
BATCH=DATA/'tx-batches'
PACE=Path('/private/tmp/intent-fastnear-explorer-quota')
client=None

def api_pace(backoff=None):
    PACE.touch(exist_ok=True)
    while True:
        with PACE.open('r+') as f:
            fcntl.flock(f,fcntl.LOCK_EX);text=f.read();state=json.loads(text) if text else {'next':0,'gap':3.0}
            now=time.time()
            if backoff is not None:
                state['next']=max(state['next'],now+max(30,backoff));state['gap']=min(12,max(3,state['gap']*1.3))
            elif now>=state['next']:state['next']=now+state['gap']
            else:
                fcntl.flock(f,fcntl.LOCK_UN);time.sleep(min(1,state['next']-now));continue
            f.seek(0);f.truncate();f.write(json.dumps(state));f.flush();return

def request(endpoint,req):
    global client
    for attempt in range(7):
        api_pace()
        try:
            if client is None:client=http.client.HTTPSConnection(HOST,timeout=30,context=ssl.create_default_context())
            client.request('POST',endpoint,json.dumps(req),{'Content-Type':'application/json','User-Agent':'IntentThesis/1.0 public historical receipt audit'})
            response=client.getresponse();raw=response.read()
            if response.status==429:
                retry=response.getheader('Retry-After','0');api_pace(int(retry) if retry.isdigit() else 30)
                with (DATA/'explorer-rate-limits.jsonl').open('a') as f:f.write(json.dumps(dict(time=datetime.now(timezone.utc).isoformat(),status=response.status,headers=dict(response.getheaders()),body=raw.decode(errors='replace')[:350]))+'\n')
            if response.status!=200:raise ValueError('HTTP '+str(response.status)+' '+raw[:150].decode(errors='replace'))
            return raw,dict(response.getheaders())
        except (OSError,http.client.HTTPException,ValueError) as ex:
            if client:client.close();client=None
            if attempt==6:raise
            time.sleep(min(8,attempt+1))

def packet(path):
    archive=json.loads(gzip.decompress(path.read_bytes()))
    assert archive['url']=='https://'+HOST+'/v0/transactions' and hashlib.sha256(archive['raw'].encode()).hexdigest()==archive['sha256']
    requested=archive['request']['tx_hashes'];assert 0<len(requested)<=20 and len(set(requested))==len(requested)
    body=json.loads(archive['raw']);actual=[x['transaction']['hash'] for x in body['transactions']]
    assert len(actual)==len(set(actual)) and set(actual)<=set(requested)
    return requested,body['transactions']

def capture(req_hashes):
    assert 0<len(req_hashes)<=20 and len(set(req_hashes))==len(req_hashes)
    params={'tx_hashes':req_hashes};name=hashlib.sha256(json.dumps(params,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    p=BATCH/(name+'.json.gz')
    if not p.is_file():
        raw,headers=request('/v0/transactions',params)
        archive=dict(url='https://'+HOST+'/v0/transactions',request=params,retrievedAt=datetime.now(timezone.utc).isoformat(),responseHeaders=headers,sha256=hashlib.sha256(raw).hexdigest(),raw=raw.decode())
        tmp=p.with_suffix('.part');tmp.write_bytes(gzip.compress(json.dumps(archive,separators=(',',':')).encode(),mtime=0));tmp.replace(p)
    req,tx=packet(p);assert req==req_hashes
    return {x['transaction']['hash'] for x in tx}

def supplied_targets(population,extra=None):
    if population=='priority-2000':
        path=DATA/'priority-2000-at-2026-11-02T165822Z.csv'
        rows=list(csv.DictReader(path.open()));assert len(rows)==2000 and len({r['transaction_hash'] for r in rows})==2000
        return [r['transaction_hash'] for r in sorted(rows,key=lambda r:int(r['priority_rank']))]
    if population=='credits':
        raw,missing=complete_chains()
        candidates=[r for r in raw if r['asset_id']==WRAP and int(r['amount'])>=10**24]
        by={}
        for r in candidates:
            if r['transaction_id']:by[r['transaction_id']]=by.get(r['transaction_id'],0)+int(r['amount'])
        # The 794 archive's original complete receipt trees already independently classify all its wNEAR credits.
        from compute.src.collectors.receipts import archived
        with archived() as archive:old={s[:-5] for s in archive.namelist()}
        return sorted(set(by)-old,key=lambda tx:(-by[tx],tx))
    if population=='high':raw=load_tier(100)+first_day()[1]
    else:raw=load_tier(100)+first_day()[1]+rows_available()
    rank={}
    for r in raw:
        tx=r['transaction_id']
        if not tx:continue
        priority=abs(int(r['amount'])) if r['asset_id']==WRAP else 0
        rank[tx]=max(rank.get(tx,0),priority)
    if extra:
        for line in Path(extra).read_text().splitlines():
            tx=line.strip()
            if tx:rank.setdefault(tx,0)
    return sorted(rank,key=lambda tx:(-rank[tx],tx))

def existing():
    captured=set()
    for p in BATCH.glob('*.json.gz'):
        req,txs=packet(p);captured.update(x['transaction']['hash'] for x in txs)
    return captured

def collect(population,extra,batch_limit):
    BATCH.mkdir(exist_ok=True); targets=supplied_targets(population,extra); done=existing()
    remaining=[tx for tx in targets if tx not in done]
    print('known transaction hashes',len(targets),'available raw trees',len(set(targets)&done),'to request',len(remaining),flush=True)
    for i in range(0,len(remaining),20):
        if batch_limit is not None and i//20>=batch_limit:break
        batch=remaining[i:i+20];got=capture(batch)
        if len(got)!=len(batch):print('unreturned indexed transaction hashes',sorted(set(batch)-got),flush=True)
        if i%200==0:print('batch transaction hashes checked',i+len(batch),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--population',choices=['high','known','credits','priority-2000'],default='high');p.add_argument('--extra-hashes');p.add_argument('--max-batches',type=int);args=p.parse_args();collect(args.population,args.extra_hashes,args.max_batches)
