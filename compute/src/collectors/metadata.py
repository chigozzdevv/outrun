"""Recover the verifier inventory's uncatalogued NEP-141 token units at the pinned historical block."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse,base64,csv,gzip,hashlib,http.client,json,ssl,time
from pathlib import Path
from compute.src.collectors.exchanges import input_units
from compute.src.collectors.receipts import rpc_pace
from compute.src.collectors.history import DATA

SOURCE=DATA/'token-metadata'
SUMMARY=DATA/'token-metadata-supplement.json'
HOST='archival-rpc.mainnet.fastnear.com'

def one(token,block,fetch):
    assert token.startswith('nep141:')
    account=token[len('nep141:'):]
    req={'jsonrpc':'2.0','id':'historical-ft-metadata','method':'query','params':{'request_type':'call_function','account_id':account,'method_name':'ft_metadata','args_base64':base64.b64encode(b'{}').decode(),'block_id':block['hash']}}
    path=SOURCE/(hashlib.sha256(token.encode()).hexdigest()+'.json.gz')
    if not path.exists():
        if not fetch:raise FileNotFoundError(path)
        for attempt in range(5):
            rpc_pace();c=http.client.HTTPSConnection(HOST,timeout=20,context=ssl.create_default_context())
            try:
                c.request('POST','/',json.dumps(req),{'Content-Type':'application/json','User-Agent':'IntentThesis historical verifier inventory'});resp=c.getresponse();raw=resp.read()
                if resp.status==429:rpc_pace(30)
                if resp.status!=200:raise OSError('HTTP '+str(resp.status))
                body=json.loads(raw);record=dict(endpoint='https://'+HOST,request=req,sha256=hashlib.sha256(raw).hexdigest(),raw=raw.decode());assert 'result' in body or 'error' in body
                tmp=path.with_suffix('.part');tmp.write_bytes(gzip.compress(json.dumps(record,separators=(',',':')).encode(),mtime=0));tmp.replace(path);break
            except (OSError,http.client.HTTPException,ValueError):
                if attempt==4:raise
                time.sleep(attempt+1)
            finally:c.close()
    saved=json.loads(gzip.decompress(path.read_bytes()));assert saved['request']==req and saved['endpoint']=='https://'+HOST and saved['sha256']==hashlib.sha256(saved['raw'].encode()).hexdigest()
    x=json.loads(saved['raw'])
    if 'error' in x:return dict(token=token,unavailable=x['error'].get('cause',{}).get('name','RPC query error'))
    r=x['result'];assert r['block_height']==block['height'] and r['block_hash']==block['hash']
    if 'error' in r:return dict(token=token,unavailable=str(r['error'])[:180])
    m=json.loads(bytes(r['result']));assert isinstance(m.get('symbol'),str) and isinstance(m.get('name'),str) and isinstance(m.get('decimals'),int) and 0<=m['decimals']<=255
    return dict(token=token,name=m['name'],symbol=m['symbol'],decimals=m['decimals'],metadataSpec=m.get('spec'),reference=m.get('reference'))

def derive(fetch):
    inventory=list(csv.DictReader((DATA/'full-period-all-asset-inventory.csv').open()));base=input_units();need=sorted({r['token'] for r in inventory if r['token'] not in base})
    block=next(s for s in json.loads((DATA/'boundary-states.json').read_text()) if s['tag']=='Oct28')
    SOURCE.mkdir(exist_ok=True);result=[one(asset,block,fetch) for asset in need]
    return dict(blockHeight=block['height'],blockHash=block['hash'],inventoryAssetIDs=len({r['token'] for r in inventory}),assetsAbsentFromReferenceCatalog=len(need),additionalOnchainUnitsVerified=sum('decimals' in r for r in result),assets=result,
                scope='These token contracts were absent from the preserved 1Click supported-token catalogue. Each successful record is the NEP-148 metadata returned by that token’s contract at the exact closing block. Symbols and decimal precision describe onchain units, not audited issuance, asset identity across chains or dollar values.')

def verified_units():
    if not SUMMARY.is_file():return {}
    saved=json.loads(SUMMARY.read_text());block=next(s for s in json.loads((DATA/'boundary-states.json').read_text()) if s['tag']=='Oct28')
    assert (saved['blockHeight'],saved['blockHash'])==(block['height'],block['hash'])
    result={}
    for r in saved['assets']:
        assert one(r['token'],block,False)==r
        if 'decimals' in r:result[r['token']]=r
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fetch',action='store_true');p.add_argument('--verify',action='store_true');a=p.parse_args();s=derive(a.fetch);text=json.dumps(s,indent=2)+'\n'
    if a.verify:assert SUMMARY.read_text()==text
    else:SUMMARY.write_text(text)
    print(s['assetsAbsentFromReferenceCatalog'],s['additionalOnchainUnitsVerified'])
