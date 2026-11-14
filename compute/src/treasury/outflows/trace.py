"""Resolve indexed outflow candidates against final archival NEAR transaction outcomes."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import json
from compute.src.treasury.outflows.collect import OUT,WALLETS,HEIGHT,STAMP,FAST,fetch,read_history,rpc,write_history


def candidate_sets(with_assets):
    histories=read_history('account-history')
    role=set(); keyed={}
    for w, stream in histories.items():
        entries=stream['rows'];assert stream['reportedCount']==len(entries)
        for r in entries:
            keyed.setdefault(r['transaction_hash'],r['tx_block_height'])
            if r['is_predecessor'] or r['is_any_signer'] or r['is_function_call'] or r['is_explicit_refund_to']:
                role.add(r['transaction_hash'])
    other=set()
    if with_assets:
        assets=read_history('asset-history')
        for w, streams in assets.items():
            other.add(streams['account']['data']['created']['transaction_hash'])
            for kind in ('ft-txns','mt-txns','staking-txns'):
                rows=streams[kind]['rows']
                for r in rows:
                    assert int(r['block']['block_height'])<=HEIGHT
                    if kind=='staking-txns' or int(r['delta_amount'])<0: other.add(r['transaction_hash'])
    return sorted(role),sorted(other-role),keyed


def transactions(collect,with_assets):
    core,extra,heights=candidate_sets(with_assets)
    raw={}
    for group in (core,extra):
        for start in range(0,len(group),20):
            ids=group[start:start+20]
            page=fetch(FAST+'transactions',{'tx_hashes':ids},collect)['transactions']
            found={r['transaction']['hash']:r for r in page}
            assert set(found)==set(ids)
            raw.update(found)
    print('candidates',len(core),'core',len(extra),'other',flush=True)
    records={}
    for i,(h,fast) in enumerate(raw.items()):
        t=fast['transaction']
        assert t['hash']==h
        signer=t['signer_id']; data=rpc('EXPERIMENTAL_tx_status',{'tx_hash':h,'sender_account_id':signer,'wait_until':'FINAL'},collect)
        assert data['transaction']['hash']==h and data['transaction']['signer_id']==signer and data['final_execution_status']=='FINAL'
        assert data['transaction_outcome']['id']==h==fast['execution_outcome']['id']
        assert data['transaction_outcome']['block_hash']==fast['execution_outcome']['block_hash']
        assert data['transaction_outcome']['outcome']['tokens_burnt']==fast['execution_outcome']['outcome']['tokens_burnt']
        if h in heights: assert fast['execution_outcome']['block_height']==heights[h]
        # Match all RPC receipt outcomes to indexed block hashes, with finality checked above.
        refs={r['execution_outcome']['id']:r['execution_outcome'] for r in fast['receipts'] if r.get('execution_outcome')}
        proven={}
        for r in data['receipts_outcome']:
            match=refs[r['id']]
            assert match['block_hash']==r['block_hash'] and match['outcome']['status']==r['outcome']['status'] and match['outcome']['tokens_burnt']==r['outcome']['tokens_burnt']
            assert match['block_height']<=HEIGHT and int(match['block_timestamp'])<=STAMP
            proven[r['id']]={'height':match['block_height'],'timestampNs':str(match['block_timestamp']),'blockHash':match['block_hash']}
        records[h]={'signer':signer,'txHeight':fast['execution_outcome']['block_height'],'txTimestampNs':str(fast['execution_outcome']['block_timestamp']),'receiptBlocks':proven}
        if (i+1)%10==0: print('archival transactions',i+1,'/',len(raw),flush=True)
    return records

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--collect',action='store_true');p.add_argument('--core-only',action='store_true');a=p.parse_args()
    verified=transactions(a.collect,not a.core_only);write_history('core-traces' if a.core_only else 'traces',verified)
    print('complete archival tx',len(verified),flush=True)
