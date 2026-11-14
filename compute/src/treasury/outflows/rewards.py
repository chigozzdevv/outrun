"""Trace native contract-execution credits from hash-matched historical block state changes."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse,csv
from collections import defaultdict
from decimal import Decimal,getcontext
from pathlib import Path
from compute.src.treasury.outflows.collect import *
from compute.src.treasury.outflows.analyze import amount,utc,write_csv
DATA='https://mainnet.neardata.xyz/v0/block/'
LO='2026-09-01T00:00:00'; HI='2026-10-31T00:00:00'
def bridge(credits):
    rows=[]
    for credit in credits:
        rows.append({'wallet':credit['wallet'],'utc':credit['utc'],'type':'contract_execution_reward','nativeYocto':credit['creditYocto'],'tx':credit['tx'],'receipt':credit['receipt'],'blockHeight':credit['height']})
    for tx,entry in read_history('traces').items():
        tree=rpc('EXPERIMENTAL_tx_status',{'tx_hash':tx,'sender_account_id':entry['signer'],'wait_until':'FINAL'},False)
        outcome={r['id']:r['outcome'] for r in tree['receipts_outcome']}
        if LO<=utc(entry['txTimestampNs'])<HI: assert entry['signer'] not in WALLETS
        for r in tree['receipts']:
            i=r['receipt_id'];loc=entry['receiptBlocks'][i];date=utc(loc['timestampNs'])
            if not LO<=date<HI or 'Failure' in outcome[i]['status']:continue
            sender,recv=r['predecessor_id'],r['receiver_id']
            if (sender in WALLETS)==(recv in WALLETS):continue
            for a in r['receipt'].get('Action',{}).get('actions',[]):
                if not isinstance(a,dict):continue
                if 'Transfer' in a and sender in WALLETS:
                    # External incoming simple transfers are accounted in the independent receipts ledger.
                    val=-int(a['Transfer']['deposit']);typ='native_transfer_out'
                elif 'FunctionCall' in a:
                    fun=a['FunctionCall'];val=int(fun['deposit'])*(1 if recv in WALLETS else -1)
                    typ=('incoming_' if val>0 else 'outgoing_')+fun['method_name']+'_attached_deposit'
                else:continue
                if val:rows.append({'wallet':recv if recv in WALLETS else sender,'utc':date,'type':typ,'nativeYocto':str(val),'tx':tx,'receipt':i,'blockHeight':loc['height']})
    ledger=json.loads((OUT.parent/'treasury-rpc-2026-11-02/summary.json').read_text())
    native=lambda s:int(Decimal(str(s))*Decimal(10**24))
    external=sum(native(w['indexedExternalNativeInflowNEAR']) for w in ledger['windows'])
    delta=sum(native(w['nativeBalanceChangeNEAR']) for w in ledger['windows'])
    add=sum(int(r['nativeYocto']) for r in rows)
    assert external+add==delta,(external,add,delta)
    for row in rows:row['nativeNEAR']=amount(row['nativeYocto'],24)
    rows.sort(key=lambda r:(r['utc'],r['receipt']))
    path=OUT.parents[2]/'public/data/near-intents-treasury-balance-adjustments-2026-09-01-to-2026-10-30.csv'
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)
    by={}
    for row in rows:by[row['type']]=by.get(row['type'],0)+int(row['nativeYocto'])
    return {'scope':'Consolidated three-wallet native balance change from 1 September through 30 October 2026. Zero net difference does not exclude offsetting unmeasured flows. Protocol execution credits are separate from external user fees.','measuredGroupChangeYocto':str(delta),'indexedExternalTransfersYocto':str(external),'adjustmentsYocto':str(add),'unexplainedYocto':'0','adjustmentsByTypeYocto':{k:str(v) for k,v in by.items()},'csv':path.name}
def eligible():
    blocks=defaultdict(dict)
    for tx,e in read_history('traces').items():
        tree=rpc('EXPERIMENTAL_tx_status',{'tx_hash':tx,'sender_account_id':e['signer'],'wait_until':'FINAL'},False)
        for r in tree['receipts']:
            wallet=r['receiver_id']
            if wallet not in WALLETS: continue
            if not any(isinstance(a,dict) and 'FunctionCall' in a for a in r['receipt'].get('Action',{}).get('actions',[])):continue
            loc=e['receiptBlocks'][r['receipt_id']]
            blocks[loc['height']][r['receipt_id']]={'tx':tx,'wallet':wallet,'hash':loc['blockHash'],'ns':loc['timestampNs']}
    return blocks
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--collect',action='store_true');ap.add_argument('--bridge-only',action='store_true');args=ap.parse_args()
    blocks=eligible();matches=[];gaps=[];headers={}
    for i,(height,receipts) in enumerate(sorted(blocks.items())):
        record=next(iter(receipts.values()));date=utc(record['ns'])
        if args.bridge_only and not LO<=date<HI:continue
        data=fetch(DATA+str(height),None,args.collect);header=data['block']['header']
        assert header['height']==height and header['hash']==record['hash'] and int(header['timestamp_nanosec'])==int(record['ns'])
        headers[height]=header['hash'];observed={}
        for shard in data['shards']:
            for edit in shard['state_changes']:
                w=edit['change'].get('account_id')
                if w not in WALLETS or edit['type']!='account_update':continue
                value=int(edit['change']['amount'])
                cause=edit['cause'];rid=cause.get('receipt_hash')
                if cause['type']=='action_receipt_gas_reward':
                    if rid not in receipts or receipts[rid]['wallet']!=w: raise ValueError('Unaccounted reward receipt '+str(rid))
                    if w not in observed:
                        gaps.append({'height':height,'previous':header['prev_hash'],'previousHeight':header['prev_height'],'wallet':w,'rid':rid,'amount':str(value),'tx':receipts[rid]['tx'],'timestamp':date})
                    else:
                        diff=value-observed[w];assert diff>=0
                        matches.append({'wallet':w,'height':height,'utc':date,'receipt':rid,'tx':receipts[rid]['tx'],'creditYocto':str(diff),'beforeYocto':str(observed[w]),'afterYocto':str(value)})
                observed[w]=value
        if i%10==0: print('state checks',i+1,'/',len(blocks),'rewards',len(matches),'prior-state gaps',len(gaps),flush=True)
    unresolved=[]
    for gap in gaps:
        w=gap['wallet'];block_hash=gap['previous'];height=gap['previousHeight']
        # Failed callbacks can credit gas without first rewriting the account in that block.
        # Walk adjoining hash-linked blocks to recover the preceding recorded liquid state.
        for _ in range(4):
            prior=fetch(DATA+str(height),None,args.collect)
            ph=prior['block']['header'];assert ph['hash']==block_hash and ph['height']==height
            vals=[edit['change']['amount'] for sh in prior['shards'] for edit in sh['state_changes'] if edit['type']=='account_update' and edit['change']['account_id']==w]
            if vals:break
            height,block_hash=ph['prev_height'],ph['prev_hash']
        if not vals:
            unresolved.append(gap);continue
        earlier=int(vals[-1]);later=int(gap['amount']);assert later>=earlier
        matches.append({'wallet':w,'height':gap['height'],'utc':gap['timestamp'],'receipt':gap['rid'],'tx':gap['tx'],'creditYocto':str(later-earlier),'beforeYocto':str(earlier),'afterYocto':str(later),'priorRecordedStateBlock':ph['height'],'priorRecordedStateHash':ph['hash']})
    result={'blockCount':len(headers),'mode':'bridge' if args.bridge_only else 'lifetime','credits':matches,'previousStateRequired':unresolved}
    if args.bridge_only and not unresolved:result['reconciliation']=bridge(matches)
    label='bridge' if args.bridge_only else 'lifetime'
    (OUT/f'gas-rewards-{label}.json').write_text(json.dumps(result,indent=2)+'\n')
    print('matched',len(matches),'needs previous state',len(unresolved),'total',(sum(int(r['creditYocto']) for r in matches)/1e24),flush=True)
if __name__=='__main__':main()
