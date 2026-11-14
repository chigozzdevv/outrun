"""Bind finalized receipt outcome hashes to canonical block height and UTC nanoseconds."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import ssl
import http.client
import time
from datetime import datetime,timezone
from compute.src.collectors.funding import rows
from compute.src.collectors.history import ROOT,DATA
from compute.src.collectors.receipts import rpc_pace
from compute.src.collectors.finality import BLOCKS

HEADERS=DATA/'receipt-block-headers'
HOST='archival-rpc.mainnet.fastnear.com'

def expected(scope):
    entries={}
    def add(h,block,stamp):
        rec=(int(block),int(stamp));assert h not in entries or entries[h]==rec;entries[h]=rec
    if scope in ('stable','all'):
        _,idx=rows();timestamps={r['receipt_id']:r['block_timestamp'] for r in idx}
        for filename in ['stable-funding-canonical.csv','stable-disposition-canonical.csv']:
            for r in csv.DictReader((DATA/filename).open()):add(r['blockHash'],r['blockHeight'],timestamps[r['receiptId']])
    if scope in ('acquisition','all'):
        for r in csv.DictReader((ROOT/'public/data/near-intents-collector-acquisition-receipts-2026-08-23-to-2026-10-28.csv').open()):
            utc=r['time_utc'];dates,ns=utc[:-1].split('.');stamp=int(datetime.fromisoformat(dates).replace(tzinfo=timezone.utc).timestamp())*10**9+int(ns)
            add(r['block_hash'],r['block_height'],stamp)
    return entries

def check(block_hash,height,ns,collect):
    path=HEADERS/(block_hash+'.json.gz');finalized=BLOCKS/(str(height)+'.json.gz')
    if finalized.is_file():header=json.loads(gzip.decompress(finalized.read_bytes()))['block']['header'];source='NEAR Data'
    elif path.is_file():
        doc=json.loads(gzip.decompress(path.read_bytes()));assert doc['endpoint']=='https://'+HOST and doc['request']['params']=={'block_id':block_hash}
        assert hashlib.sha256(doc['body'].encode()).hexdigest()==doc['sha256']
        header=json.loads(doc['body'])['result']['header'];source='archival RPC'
    elif not collect:return None
    else:
        req={'jsonrpc':'2.0','id':'collector-receipt-header','method':'block','params':{'block_id':block_hash}}
        for attempt in range(5):
            rpc_pace();cx=http.client.HTTPSConnection(HOST,timeout=22,context=ssl.create_default_context())
            try:
                cx.request('POST','/',json.dumps(req),{'Content-Type':'application/json','User-Agent':'IntentThesis public collector reconciliation'})
                resp=cx.getresponse();raw=resp.read()
                if resp.status==429:
                    h=resp.getheader('Retry-After','0');rpc_pace(max(30,int(h)) if h.isdigit() else 30)
                if resp.status!=200:raise ValueError('HTTP '+str(resp.status))
                header=json.loads(raw)['result']['header'];assert (header['hash'],int(header['height']),int(header['timestamp_nanosec']))==(block_hash,height,ns)
                doc=dict(endpoint='https://'+HOST,request=req,fetchedAt=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(raw).hexdigest(),body=raw.decode())
                tmp=path.with_suffix('.part');tmp.write_bytes(gzip.compress(json.dumps(doc,separators=(',',':')).encode(),mtime=0));tmp.replace(path);source='archival RPC';break
            except (ValueError,KeyError,http.client.HTTPException,OSError) as err:
                if attempt==4:print('archival header unavailable',block_hash,type(err).__name__,str(err)[:100],flush=True);return None
                time.sleep(attempt+1)
            finally:cx.close()
    assert (header['hash'],int(header['height']),int(header['timestamp_nanosec']))==(block_hash,height,ns),(block_hash,height,ns)
    return source

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--scope',choices=['stable','acquisition','all'],default='stable');p.add_argument('--fetch',action='store_true');a=p.parse_args();HEADERS.mkdir(exist_ok=True)
    expected_headers=expected(a.scope);matches={};missing=[]
    for i,(h,(height,ns)) in enumerate(sorted(expected_headers.items(),key=lambda v:v[1]),1):
        source=check(h,height,ns,a.fetch)
        if source:matches[h]=source
        else:missing.append(h)
        if i%25==0:print('headers',i,'of',len(expected_headers),'verified',len(matches),flush=True)
    summary=dict(scope=a.scope,uniqueOutcomeBlocks=len(expected_headers),canonicalHeightAndTimeVerified=len(matches),missingOrUnavailable=len(missing),missingBlockHashes=missing,source='Finalized receipt outcome block hashes compared to primary block headers. Both absolute height and exact timestamp nanoseconds must match the public transfer index.')
    (DATA/(a.scope+'-header-check.json')).write_text(json.dumps(summary,indent=2)+'\n');print({k:v for k,v in summary.items() if k!='missingBlockHashes'})
