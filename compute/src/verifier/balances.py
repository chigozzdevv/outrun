"""Reconstruct and verify the dated verifier inventory from saved, block-bound public RPC calls."""
from compute.src.paths import ROOT, CODE_ROOT
import base64
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import sys
import json
from pathlib import Path


D = ROOT / 'data/verified/internal-balances-2026-11-02'
HEIGHT = 222728039
HASH = 'CPx6Jmn2THRCGYsqAGXU62YTkPEQnM9wgGmrdQ3vNs8G'
contracts = ['buybacks.multisignature.near', 'fefundsadmin.sputnik-dao.near', '1csfundsadmin.sputnik-dao.near']
ledger = ROOT / 'public/data/withdrawal-matches-2026-10-28.csv'
with ledger.open() as f:
    aggregators = sorted({r['collector_account'] for r in csv.DictReader(f)})
assert len(aggregators) == 3
records = {}
inputs = sorted(D.glob(f'{HEIGHT}-*.json')) + sorted(p for p in D.glob('*-view_account-*.json') if not p.name.startswith(str(HEIGHT))) + [D/'pull-request-374.json.gz', D/'deployment-source.json']
manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
assert manifest == json.loads((D/'rpc-inventory-manifest.json').read_text()), 'Saved RPC or deployment evidence changed'
for p in D.glob(f'{HEIGHT}-*.json'):
    obj = json.loads(p.read_text()); req = obj['request']; result=obj['response']['result']
    digest = hashlib.sha256(json.dumps(req,sort_keys=True).encode()).hexdigest()[:18]
    assert p.stem.endswith('-'+digest) and req['params']['block_id'] == HASH and req['jsonrpc'] == '2.0'
    assert obj['response']['id']==req['id'] and obj['response']['jsonrpc']=='2.0'
    if req['method']=='block':
        block=result['header'];assert block['hash']==HASH and block['height']==HEIGHT
        records[('block','','')] = block
        continue
    params = req['params']
    assert req['method']=='query' and result['block_height']==HEIGHT and result['block_hash']==HASH
    method = params.get('method_name',params['request_type'])
    args = json.loads(base64.b64decode(params['args_base64'])) if 'args_base64' in params else {}
    if method=='ft_metadata': assert params['account_id']=='wrap.near'
    else: assert params['account_id']=='intents.near'
    key=(method,args.get('account_id',''),json.dumps(args,sort_keys=True))
    assert key not in records
    records[key]=json.loads(bytes(result['result'])) if 'result' in result else result

fee=records['fee','','{}']; collector=records['fee_collector','','{}']
metadata=records['contract_source_metadata','','{}']
block=records['block','','']; code=records['view_account','','{}']['code_hash']
assert records['ft_metadata','','{}']['decimals']==24
owners = [collector,*contracts,*aggregators]
assert collector not in contracts+aggregators
states={}; out=[]
for account in owners:
    pages=sorted((int(json.loads(args)['from_index']), json.loads(args)['limit'], values) for (method,owner,args),values in records.items() if method=='mt_tokens_for_owner' and owner==account)
    listed=[]
    for i,(start,limit,page) in enumerate(pages):
        assert start==len(listed) and ((len(page)==limit) if i<len(pages)-1 else len(page)<limit)
        listed.extend(t['token_id'] for t in page)
    assert pages and len(set(listed))==len(listed)
    balances={}
    for (method,owner,args),values in records.items():
        if method!='mt_batch_balance_of' or owner!=account: continue
        ids=json.loads(args)['token_ids']
        assert len(ids)==len(values) and not balances.keys()&set(ids) and all(v.isdigit() for v in values)
        balances.update(zip(ids,values))
    assert set(listed)==set(balances)
    states[account]=dict(listedTokens=len(listed),positiveTokens=sum(int(v)>0 for v in balances.values()),balancesAtomic=dict(sorted(balances.items())))
    role='verifier protocol fee collector' if account==collector else 'tracked treasury' if account in contracts else 'configured aggregation account'
    for t,n in sorted(balances.items()):
        out.append(dict(block_height=HEIGHT,block_hash=HASH,ledger_contract='intents.near',account_id=account,role=role,token_id=t,balance_atomic=n))
    if not listed:
        out.append(dict(block_height=HEIGHT,block_hash=HASH,ledger_contract='intents.near',account_id=account,role=role,token_id='',balance_atomic=''))
summary=dict(status='complete',states=[dict(height=HEIGHT,hash=HASH,timestampNs=block['timestamp_nanosec'],feePips=fee,feeCollector=collector,verifierCodeHash=code,sourceMetadata=metadata,accounts=states)])
stream=io.StringIO(newline='')
writer=csv.DictWriter(stream,fieldnames=list(out[0]),lineterminator='\n');writer.writeheader();writer.writerows(out)
seconds,nanos=divmod(int(block['timestamp_nanosec']),10**9)
utc=datetime.fromtimestamp(seconds,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+f'.{nanos:09}Z'
page=dict(blockHeight=HEIGHT,blockHash=HASH,timeUTC=utc,feePips=fee,protocolCollector=collector,
          positiveTokenCounts={account:states[account]['positiveTokens'] for account in owners},
          protocolCollectorWrappedNearAtomic=states[collector]['balancesAtomic']['nep141:wrap.near'],
          wrappedNearDecimals=records['ft_metadata','','{}']['decimals'],
          sourceMetadataVersion=metadata['version'],sourceMetadataLink=metadata['link'],deployedCodeHash=code)
derived={D/'page-summary.json':json.dumps(page,indent=2)+'\n',D/'latest-summary.json':json.dumps(summary,indent=2)+'\n', ROOT/'public/data/near-intents-verifier-fee-and-treasury-balances-2026-11-02.csv':stream.getvalue()}
for path,value in derived.items():
    if '--build' in sys.argv: path.write_text(value)
    else: assert path.read_bytes()==value.encode(), f'Published output differs: {path.name}'

deploy=json.loads((D/'deployment-source.json').read_text());pr=gzip.decompress((D/'pull-request-374.json.gz').read_bytes());j=json.loads(pr)
assert hashlib.sha256(pr).hexdigest()==deploy['apiResponseSha256'] and j['merge_commit_sha']==deploy['mergeCommit']==metadata['build_info']['source_code_snapshot'].split('=')[-1]
assert deploy['metadata']==metadata and deploy['blockHash']==HASH and deploy['codeHashesAtBoundaryBlocks'][str(HEIGHT)]==code
for height,expected in deploy['codeHashesAtBoundaryBlocks'].items():
    files=list(D.glob(f'{height}-view_account-*.json'));assert len(files)==1
    o=json.loads(files[0].read_text());q=o['request'];r=o['response']['result']
    assert q['params']['request_type']=='view_account' and q['params']['account_id']=='intents.near'
    assert q['params']['block_id']==r['block_hash'] and r['block_height']==int(height) and r['code_hash']==expected
    assert files[0].stem.endswith(hashlib.sha256(json.dumps(q,sort_keys=True).encode()).hexdigest()[:18])
print(json.dumps(dict(block=HEIGHT,feePips=fee,accounts=len(owners),positiveTokenBalances=sum(o['positiveTokens'] for o in states.values()),feeCollectorPositiveIds=states[collector]['positiveTokens'],sourceCommit=deploy['mergeCommit'])))
