"""Enumerate positive Intents verifier assets and exact balances at independent finalized boundaries."""
from compute.src.paths import ROOT, CODE_ROOT
from pathlib import Path
import argparse, base64, gzip, hashlib, http.client, json, ssl, time


DEST=ROOT/'data/verified/collector-history-2026-11-02'
RPC=DEST/'state'
HASH='https://archival-rpc.mainnet.fastnear.com'
import sys

from compute.src.collectors.history import IDS
END_STUDY=json.loads((ROOT/'data/verified/conversion-review.json').read_text())['snapshot']
START_WINDOW=json.loads((ROOT/'data/verified/matched-supply-windows.json').read_text())['windows'][0]
anchors=[dict(tag='Aug23',height=START_WINDOW['openingBlock'],hash=START_WINDOW['openingBlockHash'],timestampNs=START_WINDOW['openingBlockTimestampNs'])]
anchors.extend(dict(tag=k,height=s['height'],hash=s['hash'],timestampNs=s['timestampNs']) for k,s in zip(['Sep01','Oct01','Oct31'],json.loads((ROOT/'data/verified/treasury-rpc-2026-11-02/summary.json').read_text())['states']))
anchors.append(dict(tag='Oct28',height=END_STUDY['blockHeight'],hash=END_STUDY['blockHash'],timestampNs=END_STUDY['timestampNanosec']))
if (DEST/'first-day-boundary.json').exists():
    anchors.append(json.loads((DEST/'first-day-boundary.json').read_text()))
conn=None


def query(anchor,owner,method,args,fetch):
    global conn
    data=json.dumps(args,sort_keys=True,separators=(',',':')).encode()
    req={'jsonrpc':'2.0','id':'collector-inventory-audit','method':'query','params':{'request_type':'call_function','account_id':'intents.near','method_name':method,'args_base64':base64.b64encode(data).decode(),'block_id':anchor['hash']}}
    p=RPC/f"{anchor['tag']}-{owner[:8]}-{method}-{hashlib.sha256(data).hexdigest()[:14]}.json.gz"
    if not p.exists():
        if not fetch:raise FileNotFoundError(p)
        for attempt in range(6):
            time.sleep(1.0+min(attempt,3))
            try:
                if not conn:conn=http.client.HTTPSConnection('archival-rpc.mainnet.fastnear.com',timeout=25,context=ssl.create_default_context())
                conn.request('POST','/',json.dumps(req),{'Content-Type':'application/json','User-Agent':'IntentThesis/1.0 inventory research'}); response=conn.getresponse();raw=response.read()
                if response.status==429:time.sleep(30)
                if response.status!=200:raise ValueError('RPC HTTP '+str(response.status))
                reply=json.loads(raw)
                if 'result' not in reply:raise ValueError(str(reply.get('error','RPC error'))[:180])
                p.write_bytes(gzip.compress(json.dumps({'request':req,'response':reply},separators=(',',':')).encode(),mtime=0));break
            except (ValueError,http.client.HTTPException,OSError) as e:
                if conn:conn.close();conn=None
                if attempt==5:raise
    packet=json.loads(gzip.decompress(p.read_bytes()));assert req==packet['request']
    result=packet['response']['result'];assert result['block_height']==anchor['height'] and result['block_hash']==anchor['hash']
    return json.loads(bytes(result['result']))


def snapshots(fetch):
    RPC.mkdir(parents=True,exist_ok=True)
    states=[]
    for a in sorted(anchors,key=lambda s:s['height']):
        accounts={}
        for account in IDS:
            assets=[]
            while True:
                page=query(a,account,'mt_tokens_for_owner',dict(account_id=account,from_index=str(len(assets)),limit=100),fetch)
                assets.extend(r['token_id'] for r in page)
                if len(page)<100:break
            assert len(set(assets))==len(assets)
            if 'nep141:wrap.near' not in assets:assets.append('nep141:wrap.near')
            balances={}
            for i in range(0,len(assets),70):
                group=assets[i:i+70];vals=query(a,account,'mt_batch_balance_of',dict(account_id=account,token_ids=group),fetch);assert len(vals)==len(group)
                balances.update(zip(group,vals))
            assert all(isinstance(v,str) and v.isdigit() for v in balances.values());accounts[account]=balances
            print(a['tag'],account[:8],len([v for v in balances.values() if int(v)>0]),'positive IDs',flush=True)
        states.append(dict(a,balances=accounts))
    out=DEST/'boundary-states.json'
    out.write_text(json.dumps(states,indent=2)+'\n')
    return states

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fetch',action='store_true');snapshots(p.parse_args().fetch)
