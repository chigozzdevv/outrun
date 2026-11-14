"""Trace the DAO's verifier wNEAR transfer and subsequent native payment through the same intermediate owner."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import base64
import csv
from datetime import datetime,timezone
import gzip
import hashlib
import http.client
import io
import json
import ssl
import time
from compute.src.collectors.receipts import rpc_pace
from compute.src.collectors.history import api, WRAP, utc
from compute.src.treasury.inflows import OUT,ROOT,ledger,decimal_near,source_iterator,actions,release_file
from compute.src.collectors.finality import from_actions
from compute.src.collectors.exchanges import verify_sigs,nep413

OWNER='c0addac935f49baee1909a6fb4f08b57352a6d8c8ce7da27c59545fe45c5838c'
DAO='1csfundsadmin.sputnik-dao.near'
FIRST='DbQwfZFHV3BmRN5DhSFjageys9bXKVbvMd5U3dYzdVjs'
SECOND='3ihccLWsPJnMEgF1o37Vb9vWhwqW4h4j3iA7qX5oQGKG'
SRC=OUT/'intermediary-sources'
RPC_HOST='archival-rpc.mainnet.fastnear.com'
PUBLIC=ROOT/'public/data/near-intents-treasury-inventory-rerouting-2026-09-09.csv'

def capture(url,query,allow,read,tag):
    path=SRC/(tag+'.json.gz')
    if not path.exists():
        if not allow: raise FileNotFoundError(path)
        raw=read(query)
        doc=dict(url=url,request=query,retrievedAt=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(raw).hexdigest(),raw=raw.decode())
        tmp=path.with_suffix('.part');tmp.write_bytes(gzip.compress(json.dumps(doc,separators=(',',':')).encode(),mtime=0));tmp.replace(path)
    doc=json.loads(gzip.decompress(path.read_bytes()));raw=doc['raw'].encode()
    assert doc['url']==url and doc['request']==query and hashlib.sha256(raw).hexdigest()==doc['sha256']
    return json.loads(raw)

def request_rpc(query):
    for attempt in range(5):
        rpc_pace()
        cx=http.client.HTTPSConnection(RPC_HOST,timeout=25,context=ssl.create_default_context())
        try:
            cx.request('POST','/',json.dumps(query),{'Content-Type':'application/json','User-Agent':'IntentThesis public read-only treasury reconciliation'})
            response=cx.getresponse();raw=response.read()
            if response.status==429:
                wait=response.getheader('Retry-After','30');rpc_pace(int(wait) if wait.isdigit() else 30)
            if response.status!=200 or 'result' not in json.loads(raw):raise ValueError(raw[:180])
            return raw
        except (ValueError,OSError,http.client.HTTPException):
            if attempt==4: raise
            time.sleep(attempt+1)
        finally:cx.close()

def run(collect,verify=False):
    SRC.mkdir(parents=True,exist_ok=True)
    with (ROOT/'public/data/near-intents-verified-treasury-outflows-to-block-222728039.csv').open() as f:
        a=next(row for row in csv.DictReader(f) if row['transaction_hash']==FIRST and row['counterparty']==OWNER)
    t=next(row for row in ledger() if row['transactionHash']==SECOND)
    ns=int(t['timestampNs'])
    q=dict(account_id=OWNER,asset_id=WRAP,from_timestamp_ms=ns//10**6-20_000,to_timestamp_ms=ns//10**6+1000,limit=100,desc=False)
    transfers=capture('https://transfers.main.fastnear.com/v0/transfers',q,collect,api,'transfers')
    assert not transfers.get('resume_token')
    moves=transfers['transfers']
    assert len(moves)==3 and [v['transaction_id'] for v in moves]==[FIRST,SECOND,SECOND]
    assert all(v['account_id']==OWNER and v['asset_id']==WRAP for v in moves)
    assert moves[0]['other_account_id']==DAO and int(moves[0]['amount'])>0
    assert moves[1]['other_account_id']=='5880ad2b362620fadf759cbceb1cd5737ce8c6ed7fb8e9942881e6731f9247dd'
    assert moves[2]['other_account_id'] is None and -int(moves[2]['amount'])==int(t['amountYocto'])
    assert int(moves[0]['amount'])==int(a['amount_atomic'])
    trees={h:(tx,source) for h,tx,source in source_iterator({FIRST,SECOND})}
    assert all(src.startswith('final RPC') for _,src in trees.values())
    def proof(h,ix):
        tx,src=trees[h]
        entry=next(e for e in tx['receipts'] if e['execution_outcome'] and e['execution_outcome']['id']==moves[ix]['receipt_id'])
        out=entry['execution_outcome']['outcome'];assert out['executor_id']=='intents.near' and any(k.startswith('Success') for k in out['status'])
        return entry,[json.loads(l[11:]) for l in out['logs'] if l.startswith('EVENT_JSON:')]
    first,ev=proof(FIRST,0)
    assert any(e['event']=='mt_transfer' and e['standard']=='nep245' and any(d['old_owner_id']==DAO and d['new_owner_id']==OWNER and d['token_ids']==['nep141:wrap.near'] and d['amounts']==[moves[0]['amount']] for d in e['data']) for e in ev)
    second,ev=proof(SECOND,1)
    transfer,=[d for e in ev if e['event']=='transfer' and e['standard']=='dip4' for d in e['data'] if d['account_id']==OWNER]
    withdraw,=[d for e in ev if e['event']=='native_withdraw' and e['standard']=='dip4' for d in e['data'] if d['account_id']==OWNER]
    assert transfer['intent_hash']==withdraw['intent_hash'] and transfer['receiver_id']==moves[1]['other_account_id']
    assert withdraw['amount']==t['amountYocto'] and withdraw['receiver_id']==t['receiver']
    assert transfer['tokens']=={'nep141:wrap.near':str(-int(moves[1]['amount']))}
    signed=from_actions(actions(second['receipt']))[withdraw['intent_hash']];msg=json.loads(signed['payload']['message'])
    assert signed['payload']['recipient']=='intents.near' and msg['signer_id']==OWNER
    assert any(i['intent']=='transfer' and i['receiver_id']==transfer['receiver_id'] and i['tokens']==transfer['tokens'] for i in msg['intents'])
    assert any(i['intent']=='native_withdraw' and i['receiver_id']==withdraw['receiver_id'] and i['amount']==withdraw['amount'] for i in msg['intents'])
    verify_sigs([dict(hash=nep413(signed).hex(),pk=signed['public_key'],sig=signed['signature'],collector=OWNER)])
    fs=int(moves[0]['block_height']);ss=int(moves[1]['block_height'])
    balances=[]
    for height in [fs-1,fs,ss]:
        params=dict(request_type='call_function',account_id='intents.near',method_name='mt_balance_of',args_base64=base64.b64encode(json.dumps(dict(account_id=OWNER,token_id='nep141:wrap.near'),separators=(',',':')).encode()).decode(),block_id=height)
        q=dict(jsonrpc='2.0',id='treasury-funding-account',method='query',params=params)
        reply=capture('https://'+RPC_HOST,q,collect,request_rpc,'balance-'+str(height))['result']
        assert reply['block_height']==height
        balances.append(dict(height=height,hash=reply['block_hash'],verifierWNEARRaw=json.loads(bytes(reply['result']))))
    opening,after,closing=(int(v['verifierWNEARRaw']) for v in balances)
    assert after-opening==int(moves[0]['amount']) and after-closing==-sum(int(m['amount']) for m in moves[1:])
    lower=max(0,int(t['amountYocto'])-opening)
    summary=dict(transferringTreasury=DAO,intermediateOwner=OWNER,nativeTreasuryRecipient=t['receiver'],fundingTransaction=FIRST,nativePaymentTransaction=SECOND,
                 fundingUTC=a['occurred_utc'],nativeReceiptUtcNs=t['timestampNs'],wNEARFromTreasury=decimal_near(int(moves[0]['amount'])),nativeNEARDelivered=decimal_near(int(t['amountYocto'])),
                 minimumNativeNEARFromTrackedTreasuryInventory=decimal_near(lower),otherWNEARSentToOriginalCollector=decimal_near(-int(moves[1]['amount'])),balances=balances,
                 withdrawalAndCollectorTransferIntent=withdraw['intent_hash'],archivalTransactionSources=[trees[h][1] for h in (FIRST,SECOND)],
                 interpretation='The complete bidirectional transfer window identifies the DAO credit and subsequent withdrawal debit. The entire opening intermediate stock is allowed as alternate funding. This is a re-routing of already-owned group inventory to the stated extent despite an intents.near native receipt predecessor.')
    release_file(OUT/'intermediary-summary.json',json.dumps(summary,indent=2)+'\n',verify)
    rows=[]
    for i,state in enumerate(balances):
        kind=['Opening verifier balance','Balance after DAO transfer','Balance after native withdrawal'][i]
        rows.append(dict(record=kind,block_height=state['height'],utc='',from_account='',to_account=OWNER,amount_wnear=decimal_near(int(state['verifierWNEARRaw'])),
                         amount_native_near='',receipt_id='',transaction_hash='',source_url='https://nearblocks.io/blocks/'+state['hash']))
    for i,m in enumerate(moves):
        debit=int(m['amount'])
        rows.append(dict(record=['Verifier transfer from treasury','Verifier transfer to fee collector','Wrapped withdrawal with confirmed native treasury delivery'][i],
                         block_height=m['block_height'],utc=utc(m['block_timestamp']),from_account=DAO if debit>0 else OWNER,
                         to_account=OWNER if debit>0 else t['receiver'] if i==2 else m['other_account_id'],
                         amount_wnear=decimal_near(abs(debit)),amount_native_near=decimal_near(int(t['amountYocto'])) if i==2 else '',
                         receipt_id=m['receipt_id'],transaction_hash=m['transaction_id'],source_url='https://nearblocks.io/txns/'+m['transaction_id']))
    f=io.StringIO(newline='');w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    release_file(PUBLIC,f.getvalue(),verify)
    print(json.dumps(summary,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--collect',action='store_true');parser.add_argument('--verify',action='store_true')
    args=parser.parse_args();run(args.collect,args.verify)
