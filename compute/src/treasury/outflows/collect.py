"""Capture public account/asset histories through a fixed finalized block. Default: offline replay."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor
import threading
import time

OUT = ROOT / 'data/verified/treasury-outflows-2026-11-02'
WALLETS = ('buybacks.multisignature.near','fefundsadmin.sputnik-dao.near','1csfundsadmin.sputnik-dao.near')
HEIGHT = 222728039
BLOCK = 'CPx6Jmn2THRCGYsqAGXU62YTkPEQnM9wgGmrdQ3vNs8G'
STAMP = 1793627903911607825
FAST = 'https://tx.main.fastnear.com/v0/'
NB = 'https://api.nearblocks.io/v3/'
RPC = 'https://archival-rpc.mainnet.fastnear.com'
PACERS = {}

def fetch(url, body, collect):
    wire = json.dumps(body, separators=(',', ':'), sort_keys=True).encode() if body is not None else None
    ident = sha256(url.encode() + b'\n' + (wire or b'')).hexdigest()
    path = OUT/'sources'/(ident+'.json.gz')
    if not path.exists():
        if not collect: raise FileNotFoundError(path)
        pace = 7 if url.startswith(NB) else 1.5 if url.startswith(RPC) else 0.6
        service = NB if url.startswith(NB) else FAST if url.startswith(FAST) else RPC
        lock, last = PACERS.setdefault(service, (threading.Lock(), [0]))
        for attempt in range(7):
            try:
                with lock:
                    time.sleep(max(0, pace-(time.monotonic()-last[0])))
                    last[0] = time.monotonic()
                req = Request(url,data=wire, headers={'Accept':'application/json','Content-Type':'application/json','User-Agent':'NEAR-Intents-public-research/1.0'})
                with urlopen(req, timeout=35) as res: raw = res.read().decode()
                decoded = json.loads(raw)
                if isinstance(decoded,dict) and 'error' in decoded: raise ValueError(decoded['error'])
                path.parent.mkdir(exist_ok=True)
                record = {'url':url,'request':body,'fetchedAt':datetime.now(timezone.utc).isoformat(),'responseSha256':sha256(raw.encode()).hexdigest(),'responseText':raw}
                tmp = path.with_suffix('.tmp')
                tmp.write_bytes(gzip.compress(json.dumps(record,separators=(',',':')).encode(),mtime=0)); tmp.replace(path)
                break
            except HTTPError as e:
                if e.code not in [429,500,502,503,504] or attempt==6: raise
                retry = e.headers.get('Retry-After','')
                time.sleep(max(float(retry) if retry.isdigit() else 0, 6*(attempt+1)))
            except (URLError, TimeoutError):
                if attempt==6: raise
                time.sleep(2**attempt)
    record = json.loads(gzip.decompress(path.read_bytes()))
    raw = record['responseText']
    assert record['url']==url and record['request']==body and sha256(raw.encode()).hexdigest()==record['responseSha256']
    return json.loads(raw)

def account_history(w, collect):
    rows, cursors = [], set()
    body={'account_id':w,'to_tx_block_height':HEIGHT,'limit':200,'desc':True}
    count=None
    while True:
        page=fetch(FAST+'account', body, collect); part=page['account_txs']
        if count is None: count=page.get('txs_count')
        for row in part:
            assert row['account_id']==w and row['tx_block_height']<=HEIGHT
            assert not rows or row['tx_block_height']<=rows[-1]['tx_block_height']
            rows.append(row)
        cursor=page.get('resume_token')
        if cursor is None: break
        assert part and cursor not in cursors
        cursors.add(cursor); body={**body,'resume_token':cursor}
    assert len(rows)==len({r['transaction_hash'] for r in rows})
    return {'wallet':w,'pages':len(cursors)+1,'reportedCount':count,'rows':rows}

def near_history(w, kind, collect):
    path=NB+'accounts/'+w
    if kind=='account': return fetch(path,None,collect)
    path += '/'+kind
    cursor=None; cursors=set(); rows=[]
    while True:
        q={'before_ts':str(STAMP+1),'limit':100}
        if cursor: q['next']=cursor
        page=fetch(path+'?'+urlencode(q),None,collect)
        part=page['data']; rows.extend(part)
        cursor=(page.get('meta') or {}).get('next_page')
        if not cursor: break
        assert part and cursor not in cursors
        cursors.add(cursor)
    return {'pages':len(cursors)+1,'rows':rows}

def write_history(kind,rows):
    f=OUT/(kind+'.json.gz'); f.write_bytes(gzip.compress(json.dumps(rows,separators=(',',':')).encode(),mtime=0))

def read_history(kind):
    return json.loads(gzip.decompress((OUT/(kind+'.json.gz')).read_bytes()))

def fast_all(collect):
    out={}
    for w in WALLETS:
        out[w]=account_history(w,collect)
        print('FastNear',w,len(out[w]['rows']),out[w]['reportedCount'],out[w]['pages'],flush=True)
    write_history('account-history',out)

def near_all(collect):
    out={}
    for w in WALLETS:
        out[w]={}
        for kind in ('account','ft-txns','mt-txns','staking-txns'):
            v=near_history(w,kind,collect);out[w][kind]=v
            print('NearBlocks',w,kind,len(v.get('rows',[])),flush=True)
    write_history('asset-history',out)

def rpc(method,params,collect):
    return fetch(RPC,{'jsonrpc':'2.0','id':'thesis-readonly','method':method,'params':params}, collect)['result']

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--collect',action='store_true');args=ap.parse_args()
    header=rpc('block',{'block_id':BLOCK},args.collect)['header']
    assert header['height']==HEIGHT and header['hash']==BLOCK and int(header['timestamp_nanosec'])==STAMP
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=[pool.submit(f,args.collect) for f in [fast_all,near_all]]
        for result in results: result.result()

if __name__=='__main__': main()
