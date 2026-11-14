#!/usr/bin/env python3
"""Capture or replay treasury permissions and access keys at one finalized block."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import base64
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
from compute.src.collectors.receipts import rpc_pace
import http.client
import ssl
import time


OUT=ROOT/'data/verified/treasury-control-2026-11-02'
RPC='archival-rpc.mainnet.fastnear.com'
HEIGHT=222728039
BLOCK='CPx6Jmn2THRCGYsqAGXU62YTkPEQnM9wgGmrdQ3vNs8G'
WALLETS=['buybacks.multisignature.near','fefundsadmin.sputnik-dao.near','1csfundsadmin.sputnik-dao.near']
PUBLIC=ROOT/'public/data/near-intents-treasury-controls-2026-11-02.csv'

def read(params, collect):
    request=dict(jsonrpc='2.0',id='public-treasury-permissions',method='query',params={**params,'block_id':BLOCK})
    encoded=json.dumps(request,sort_keys=True,separators=(',',':')).encode()
    path=OUT/'sources'/(hashlib.sha256(encoded).hexdigest()+'.json.gz')
    if not path.exists():
        if not collect:raise FileNotFoundError(path)
        for attempt in range(5):
            rpc_pace()
            cx=http.client.HTTPSConnection(RPC,timeout=30,context=ssl.create_default_context())
            try:
                cx.request('POST','/',encoded,{'Content-Type':'application/json','User-Agent':'IntentThesis public read-only treasury control study'})
                res=cx.getresponse();raw=res.read()
                if res.status==429:
                    wait=res.getheader('Retry-After','30');rpc_pace(int(wait) if wait.isdigit() else 30)
                if res.status!=200:raise ValueError('RPC HTTP '+str(res.status))
                body=json.loads(raw)
                if 'error' in body:raise ValueError(body['error'])
                record=dict(url='https://'+RPC,request=request,retrievedAt=datetime.now(timezone.utc).isoformat(),responseSha256=hashlib.sha256(raw).hexdigest(),responseText=raw.decode())
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(gzip.compress(json.dumps(record,separators=(',',':')).encode(),mtime=0));break
            except (ValueError,OSError,http.client.HTTPException):
                if attempt==4:raise
                time.sleep(attempt+1)
            finally:cx.close()
    record=json.loads(gzip.decompress(path.read_bytes()));raw=record['responseText'].encode()
    assert record['request']==request and hashlib.sha256(raw).hexdigest()==record['responseSha256']
    result=json.loads(raw)['result'];assert result['block_hash']==BLOCK and result['block_height']==HEIGHT
    return result

def main(collect,build):
    states={};rows=[]
    for wallet in WALLETS:
        account=read(dict(request_type='view_account',account_id=wallet),collect)
        keys=read(dict(request_type='view_access_key_list',account_id=wallet),collect)['keys']
        state=dict(codeHash=account['code_hash'],accessKeys=keys)
        for k in keys:
            permission=k['access_key']['permission']
            detail=permission.get('FunctionCall',{}) if isinstance(permission,dict) else {}
            rows.append(dict(wallet=wallet,control_type='Access key',member=k['public_key'],permission='FunctionCall' if detail else permission,receiver_id=detail.get('receiver_id',''),method_names='|'.join(detail.get('method_names',[])),threshold='',block_height=HEIGHT,block_hash=BLOCK))
        method='get_num_confirmations' if wallet==WALLETS[0] else 'get_policy'
        result=read(dict(request_type='call_function',account_id=wallet,method_name=method,args_base64=base64.b64encode(b'{}').decode()),collect)
        value=json.loads(bytes(result['result']))
        if method=='get_num_confirmations':
            assert isinstance(value,int) and value>0
            state['requiredConfirmations']=value
            rows.append(dict(wallet=wallet,control_type='Contract confirmation threshold',member='',permission='',receiver_id='',method_names='',threshold=value,block_height=HEIGHT,block_hash=BLOCK))
        else:
            state['policy']=value
            for role in value['roles']:
                members=role['kind'].get('Group',[]) if isinstance(role['kind'],dict) else [str(role['kind'])]
                for member in members:
                    rows.append(dict(wallet=wallet,control_type='Policy role: '+role['name'],member=member,permission='|'.join(role['permissions']),receiver_id='',method_names='',threshold=json.dumps(role['vote_policy'],sort_keys=True,separators=(',',':')),block_height=HEIGHT,block_hash=BLOCK))
        states[wallet]=state
    summary=dict(blockHeight=HEIGHT,blockHash=BLOCK,wallets=states,scope='Direct archival contract views and access-key lists at one pinned block. They establish the returned permissions, not independent controller identities, immutable policy, reviewed deployed code, or legal rights of token holders.')
    buf=io.StringIO(newline='');writer=csv.DictWriter(buf,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)
    for path,value in {OUT/'summary.json':json.dumps(summary,indent=2)+'\n',PUBLIC:buf.getvalue()}.items():
        if build:path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
        else:assert path.read_text()==value,path
    print(json.dumps(summary,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--collect',action='store_true');p.add_argument('--build',action='store_true');a=p.parse_args();main(a.collect,a.build)
