"""Account-level verifier transfer history. Fetch complete cursor chains; verify amounts with pinned RPC state."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import csv
import fcntl
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import gzip
import http.client
import json
from pathlib import Path
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


DATA=ROOT/'data/verified/collector-history-2026-11-02'
PAGES=DATA/'pages'
WRAP='nep245:intents.near:nep141:wrap.near'
VERIFIER='nep245:intents.near:'
IDS=sorted({r['collector_account'] for r in csv.DictReader((ROOT/'public/data/withdrawal-matches-2026-10-28.csv').open())})
ORIGINAL_START=1787443199885903781
ORIGINAL_END=1793172407281150058
START='2026-08-23'
END='2026-10-28T07:26:47.281150'
TIMEBASE=10**24
HOST='transfers.main.fastnear.com'
lock=threading.Lock()
next_request=0.0
connection=threading.local()
PACE=Path('/private/tmp/intent-collector-nep245-rate.json')


def pace(backoff=None):
    PACE.touch(exist_ok=True)
    while True:
        with PACE.open('r+') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            text=f.read();state=json.loads(text) if text else {'next':0,'gap':1.0}
            now=time.time()
            if backoff is not None:
                state['next']=max(state['next'],now+max(30,backoff))
                state['gap']=min(8,max(1.2,state['gap']*1.3)) if now>state.get('last429',0)+3 else state['gap']
                state['last429']=now
            elif now>=state['next']:
                state['next']=now+state['gap']
            else:
                wait=state['next']-now
                fcntl.flock(f,fcntl.LOCK_UN);time.sleep(min(wait,3));continue
            f.seek(0);f.truncate();f.write(json.dumps(state));f.flush();return


def stamp(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()*1000)


def utc(ns):
    sec,frac=divmod(int(ns),10**9)
    return datetime.fromtimestamp(sec,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+f'.{frac:09d}Z'


def queries():
    # Split into dates to localize resumption. Adjacent requests overlap in real time,
    # and the data derivation uses (opening timestamp, closing timestamp] with integer ns.
    edges=[stamp(START)]
    while edges[-1]+7*86400000<ORIGINAL_END//10**6:
        edges.append(edges[-1]+7*86400000)
    edges.append(ORIGINAL_END//10**6+1)
    for col in IDS:
        for core in ('credits1','debits'):
            for ix,(left,right) in enumerate(zip(edges,edges[1:])):
                yield (f'{col[:8]}-{core}-{ix:02}',dict(account_id=col,from_timestamp_ms=left-1000,to_timestamp_ms=right+1000,desc=False,limit=100,
                            **({'asset_id':WRAP,'direction':'receiver','min_amount':str(TIMEBASE)} if core=='credits1' else {'direction':'sender'})))


def day_queries():
    day_end=stamp('2026-08-24')
    for col in IDS:
        yield (f'{col[:8]}-dayall-00',dict(account_id=col,from_timestamp_ms=ORIGINAL_START//10**6-1000,to_timestamp_ms=day_end+1000,desc=False,limit=100))


def stable_queries():
    owner=next(o for o in IDS if o.startswith('143f2bb4'))
    token='nep141:eth-0xdac17f958d2ee523a2206206994597c13d831ec7.omft.near'
    yield f'{owner[:8]}-ethusdt-00',dict(account_id=owner,from_timestamp_ms=ORIGINAL_START//10**6-1000,to_timestamp_ms=stamp('2026-08-24')+1000,asset_id=VERIFIER+token,desc=False,limit=100)


def api(req, *, headers_out=None):
    url='/v0/transfers'
    for attempt in range(9):
        pace()
        try:
            if not getattr(connection,'client',None):
                connection.client=http.client.HTTPSConnection(HOST,timeout=24,context=ssl.create_default_context())
            connection.client.request('POST',url,json.dumps(req),{'Content-Type':'application/json','User-Agent':'NEAR-intents-research/1'})
            resp=connection.client.getresponse(); raw=resp.read()
            if resp.status==429:
                delay=resp.getheader('Retry-After','0')
                pace(int(delay) if delay.isdigit() else 30)
                audit=PAGES.parent/'rate-limit.jsonl'
                audit.parent.mkdir(parents=True,exist_ok=True)
                with audit.open('a') as f:f.write(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'responseHeaders':dict(resp.getheaders()),'responseBody':raw.decode(errors='replace')[:450]})+'\n')
            if resp.status!=200: raise RuntimeError(f'HTTP {resp.status}')
            x=json.loads(raw)
            assert isinstance(x.get('transfers'),list) and len(x['transfers'])<=100
            if headers_out is not None: headers_out.update(dict(resp.getheaders()))
            return raw
        except (OSError,http.client.HTTPException,RuntimeError) as ex:
            if getattr(connection,'client',None):connection.client.close();connection.client=None
            if attempt==8:raise
            time.sleep(min(10,attempt*2+1))


def restore(path):
    payload=json.loads(gzip.decompress(path.read_bytes()))
    body=payload['body']
    canonical=json.dumps(body,sort_keys=True,separators=(',',':')).encode()
    assert sha256(canonical).hexdigest()==payload['semanticSHA256']
    return payload['request'],body


def fetch_query(q):
    key,params=q; cursor=None; seen=set()
    for ix in range(1500):
        req=params if cursor is None else dict(params,resume_token=cursor)
        f=PAGES/f'{key}-{ix:04}.json.gz'
        if f.exists():
            stored,body=restore(f)
            assert stored==req,f
        else:
            raw=api(req);body=json.loads(raw)
            canonical=json.dumps(body,sort_keys=True,separators=(',',':')).encode()
            packet={'endpoint':'https://'+HOST+'/v0/transfers','retrievedAt':datetime.now(timezone.utc).isoformat(),
                    'request':req,'sha256':sha256(raw).hexdigest(),'semanticSHA256':sha256(canonical).hexdigest(),'body':body}
            temp=f.with_suffix('.part');temp.write_bytes(gzip.compress(json.dumps(packet,separators=(',',':')).encode(),compresslevel=5,mtime=0));temp.replace(f)
        nxt=body.get('resume_token')
        if not nxt:return ix+1
        assert nxt not in seen and body['transfers'],(key,ix)
        seen.add(nxt);cursor=nxt
    raise ValueError('Exceeded explicit page safety bound '+key)


def enumerate_pages():
    from compute.src.collectors.inventory import complete_chains
    rows,missing=complete_chains()
    if missing:raise ValueError('Unfinished transfer cursor chains: '+json.dumps(missing))
    return sorted(rows,key=lambda r:(int(r['block_height']),int(r['transfer_index']),r['account_id']))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['fetch','fetch-day','fetch-week','fetch-tail','fetch-stable','enumerate']);args=parser.parse_args();PAGES.mkdir(parents=True,exist_ok=True)
    if args.mode.startswith('fetch'):
        qs=list(queries() if args.mode=='fetch' else day_queries())
        if args.mode=='fetch-week':
            qs=[(f'{col[:8]}-wrap-00',dict(account_id=col,from_timestamp_ms=stamp('2026-08-23')-1000,to_timestamp_ms=stamp('2026-08-30')+1000,desc=False,limit=100,asset_id=WRAP)) for col in IDS]
        if args.mode=='fetch-tail':
            qs=[(f'{col[:8]}-tail-00',dict(account_id=col,from_timestamp_ms=stamp('2026-10-28')-1000,to_timestamp_ms=ORIGINAL_END//10**6+1000,desc=False,limit=100,asset_id=WRAP)) for col in IDS]
        if args.mode=='fetch-stable':qs=list(stable_queries())
        # Collect each account/series across a common recent slice early, then backfill.
        qs=sorted(qs,key=lambda q:(int(q[0].split('-')[-1]),q[0].split('-')[1],q[0]))
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs={pool.submit(fetch_query,q):q[0] for q in qs}
            failed=[]
            for job in as_completed(jobs):
                try:print(jobs[job],job.result(),'pages',flush=True)
                except (OSError,http.client.HTTPException,RuntimeError) as error:
                    failed.append(jobs[job]);print('incomplete query',jobs[job],type(error).__name__,str(error)[:120],flush=True)
            if failed:raise RuntimeError('Cursor queries need resumption: '+', '.join(failed))
    else:
        rows=enumerate_pages()
        print('unique movements',len(rows),'wNEAR movements',sum(r['asset_id']==WRAP for r in rows),'by collector',Counter(r['account_id'][:8] for r in rows))


if __name__=='__main__': main()
