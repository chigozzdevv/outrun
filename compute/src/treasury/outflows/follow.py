"""Trace the material July recipient's next hop using two independent receipt indexes."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse,base64,json
from compute.src.treasury.outflows.analyze import utc
from compute.src.treasury.outflows.collect import *
W='508aa18569e5a59d63b0f4c445e02e7b5fc53b86337328e756b32745ebac50e7'
IN_TX='ABTtANZpzNnxMHpHVSSuPkNZ6EvQhBtGjVmco9aJFRx4'
def flatten(node):
    yield node
    for child in (node.get('receipts') or []):yield from flatten(child)
def main():
    p=argparse.ArgumentParser();p.add_argument('--collect',action='store_true');args=p.parse_args()
    h=read_history('traces')[IN_TX];rid='D8WbahrZuJSFKwW5oFFCs4gWZ4svtNV5g39W2TJfHSvp';start=h['receiptBlocks'][rid]
    root=rpc('EXPERIMENTAL_tx_status',{'tx_hash':IN_TX,'sender_account_id':h['signer'],'wait_until':'FINAL'},False)
    receipt=next(r for r in root['receipts'] if r['receipt_id']==rid)
    assert receipt['receiver_id']==W and receipt['receipt']['Action']['actions']==[{'Transfer':{'deposit':str(12000*10**24)}}]
    req={'account_id':W,'from_tx_block_height':start['height'],'to_tx_block_height':HEIGHT,'desc':False,'limit':200}
    rows=[];pages=0; cursors=set()
    while True:
        page=fetch(FAST+'account',req,args.collect);rows.extend(page['account_txs']);pages+=1
        if pages==1: expected=page['txs_count']
        token=page.get('resume_token')
        if not token:break
        assert token not in cursors;cursors.add(token);req['resume_token']=token
    assert expected==len(rows)==len({r['transaction_hash'] for r in rows})
    after=[]
    for role in rows:
        tx=role['transaction_hash'];assert role['account_id']==W and start['height']<=role['tx_block_height']<=HEIGHT
        ft=fetch(FAST+'transactions',{'tx_hashes':[tx]},args.collect)['transactions'][0]
        tree=fetch(NB+'txns/'+tx+'/receipts',None,args.collect)['data']
        indexed={r['receipt_id']:r for r in flatten(tree)}
        assert ft['transaction']['hash']==tx and ft['execution_outcome']['block_height']==role['tx_block_height']
        actions=[]
        for pair in ft['receipts']:
            rec=pair['receipt'];f=pair['execution_outcome'];i=f['id'];b=indexed.get(i)
            if rec['predecessor_id']=='system' and b is None:continue # The explorer elides protocol refunds in its receipt tree.
            assert b and b['predecessor_account_id']==rec['predecessor_id'] and b['receiver_account_id']==rec['receiver_id']
            assert b['block']['block_hash']==f['block_hash'] and int(b['block']['block_timestamp'])==int(f['block_timestamp'])
            success='Failure' not in f['outcome']['status']; assert b['outcome']['status']==success
            raw=rec['receipt'].get('Action',{}).get('actions',[]); comparison=b['actions']; assert len(raw)==len(comparison)
            if rec['predecessor_id']=='system':continue
            for a, v in zip(raw,comparison):
                k=next(iter(a));d=a[k];assert k in ['FunctionCall','Transfer']
                assert int(d['deposit'])==int(v['args']['deposit'])
                assert (k=='Transfer' and v['action']=='TRANSFER') or (k=='FunctionCall' and v['action']=='FUNCTION_CALL' and d['method_name']==v['method'])
                actions.append({'from':rec['predecessor_id'],'to':rec['receiver_id'],'receiptId':i,'status':'success' if success else 'failed','utc':utc(f['block_timestamp']),'timestampNs':str(f['block_timestamp']),'height':f['block_height'],'blockHash':f['block_hash'],'action':k,'depositYocto':d['deposit'],'method':d.get('method_name'),'args':json.loads(base64.b64decode(d['args'])) if d.get('args') else None})
        after.append({'transactionHash':tx,'signer':ft['transaction']['signer_id'],'actions':actions})
    result={'asOfBlock':HEIGHT,'asOfUTC':utc(STAMP),'wallet':W,'incomingVerifiedTx':IN_TX,'incomingReceipt':rid,'incomingYocto':str(12000*10**24),'incomingOccurredUTC':utc(start['timestampNs']),'incomingTimestampNs':start['timestampNs'],'eligibleRootTransactions':len(rows),'terminalPages':pages,'followOn':after,'scope':'Full subsequent indexed account activity whose root transaction was at or after the incoming receipt block. Incoming payment checked by archival RPC; subsequent receipt actions, timestamps, amounts and success crosschecked independently between FastNear and NearBlocks. A transfer establishes the next recipient; it does not identify a sale or ultimate beneficial owner.'}
    (OUT/'one-hop-12000.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(after,indent=2))
if __name__=='__main__':main()
