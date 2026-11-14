"""Reproduce proposal execution, treasury outflows and gas tables from captured public sources."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import base64
from collections import Counter,defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal, getcontext
import json
from pathlib import Path
from compute.src.treasury.outflows.collect import OUT,WALLETS,HEIGHT,BLOCK,STAMP,read_history,rpc
from compute.src.treasury.outflows.trace import candidate_sets
getcontext().prec=80
PUBLIC=OUT.parents[2]/'public/data'
USDC='17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1'
BRIDGED='a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48.factory.bridge.near'
CUT='to-block-222728039'
COLS=['occurred_utc','block_height','transaction_hash','receipt_id','original_signer','wallet','counterparty','entry_type','asset','amount_atomic','asset_decimals','amount_units','proposal_id','receipt_status','method','explorer_url']
GASCOL=['root_transaction_utc','transaction_hash','original_signer','delegated_senders','transactions_final_status','receipt_count','failed_receipt_count','root_execution_yocto','receipt_execution_yocto','trace_reported_tokens_burnt_near','gas_burnt','gas_refunds_near','gas_refunded_to','balance_refunds_near','balance_refunded_to','explorer_url']
def utc(ns):
    s,n=divmod(int(ns),10**9)
    return datetime.fromtimestamp(s,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+f'.{n:09d}Z'
def amount(x,d):
    return format(Decimal(int(x)).scaleb(-d),'f') if d is not None else ''
def decode_args(a):
    try: return json.loads(base64.b64decode(a))
    except (ValueError,TypeError,UnicodeDecodeError): return {}
def write_csv(name,fields,rows):
    path=PUBLIC/(name+'-'+CUT+'.csv')
    with path.open('w',newline='') as f:
        wr=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');wr.writeheader();wr.writerows(rows)
    return path.name
def main():
    cli=argparse.ArgumentParser();cli.add_argument('--collect',action='store_true');args=cli.parse_args()
    traces=read_history('traces'); histories=read_history('account-history'); idx=read_history('asset-history')
    core,extra,_=candidate_sets(True);assert set(traces)==set(core+extra)
    closing=rpc('block',{'block_id':BLOCK},False)['header']
    assert int(closing['timestamp_nanosec'])==STAMP and closing['height']==HEIGHT
    movements=[]; gas=[]; actions=[]; all_events=[]; incoming_gas=defaultdict(int); pool_accounts=set()
    proposals=json.loads((OUT.parent/'treasury-rpc-2026-11-02/dao-proposals.json').read_text())['accounts']
    props={(w,x['id']):x for w,dao in proposals.items() for x in dao['proposals']}
    created={}; deposits={}; matched_neg=Counter(); index_neg=Counter(); matched_governance=defaultdict(list); failed_governance=defaultdict(list)
    meta={'NEAR':24,USDC:6,BRIDGED:6,'intents.near/nep141:wrap.near':24}
    staking_flags={}
    for w in WALLETS:
        acc=idx[w]['account']['data']; assert acc['account_id']==w; cr=acc['created']; created[w]={'tx':cr['transaction_hash'],'indexedCreatedUTC':utc(cr['block_timestamp'])}
        staking_flags[w]={'indexedRows':len(idx[w]['staking-txns']['rows']), 'pools':set()}
        for method in ('ft-txns','mt-txns'):
            eventkeys=set()
            for ev in idx[w][method]['rows']:
                assert ev['affected_account_id']==w
                unique=(ev['receipt_id'],ev['event_index'],ev.get('token_id'),ev['delta_amount'],ev['involved_account_id'])
                assert unique not in eventkeys;eventkeys.add(unique)
                asset=ev['contract_account_id']+(('/'+ev['token_id']) if method=='mt-txns' else '')
                metadata=ev.get('base_meta') if method=='mt-txns' else ev.get('meta')
                if metadata and isinstance(metadata.get('decimals'),int):
                    decimals=metadata['decimals'];assert asset not in meta or meta[asset]==decimals;meta[asset]=decimals
                if int(ev['delta_amount'])<0:
                    key=(ev['transaction_hash'],ev['receipt_id'],w,asset,str(-int(ev['delta_amount'])),ev.get('involved_account_id') or '')
                    index_neg[key]+=1
    checked_blocks=set(); payout_check=[]; method_counts=Counter()
    for h,entry in sorted(traces.items()):
        signer=entry['signer']; t=rpc('EXPERIMENTAL_tx_status',{'tx_hash':h,'sender_account_id':signer,'wait_until':'FINAL'},False)
        assert t['final_execution_status']=='FINAL' and t['transaction']['hash']==h and t['transaction']['signer_id']==signer
        by_out={x['id']:x for x in t['receipts_outcome']}; raw={x['receipt_id']:x for x in t['receipts']}
        assert set(raw)<=set(by_out)
        parent={}
        for i,out in by_out.items():
            for cid in out['outcome']['receipt_ids']:
                if cid in raw: assert cid not in parent; parent[cid]=i
        votes={}
        for i,x in raw.items():
            g=x['receipt'].get('Action') or x['receipt'].get('ActionV2')
            if not g:continue
            if x['receiver_id'] not in WALLETS: continue
            for action in g['actions']:
                if not isinstance(action,dict):continue
                fun=action.get('FunctionCall',{})
                if fun.get('method_name')=='act_proposal':
                    body=decode_args(fun['args'])
                    if body.get('action')=='VoteApprove' and isinstance(body.get('id'),int): votes[i]=(x['receiver_id'],body['id'])
        def vote_for(i):
            seen=set()
            while i and i not in votes:
                assert i not in seen;seen.add(i);i=parent.get(i)
            return votes.get(i)
        delegates=[a['Delegate']['delegate_action']['sender_id'] for a in t['transaction']['actions'] if 'Delegate' in a]
        root= t['transaction_outcome']['outcome']; receipt_burn=sum(int(o['outcome']['tokens_burnt']) for o in by_out.values())
        rb=int(root['tokens_burnt']);refunds=defaultdict(int); balance_refunds=defaultdict(int)
        def movement(i,w,to,kind,asset,atomic,status,method=''):
            loc=entry['receiptBlocks'][i]; v=vote_for(i)
            out={'occurred_utc':utc(loc['timestampNs']),'block_height':loc['height'],'transaction_hash':h,'receipt_id':i,'original_signer':signer,'wallet':w,'counterparty':to,'entry_type':kind,'asset':asset,'amount_atomic':str(atomic),'asset_decimals':meta.get(asset,''),'amount_units':amount(atomic,meta.get(asset)),'proposal_id':v[1] if v and v[0]==w else '', 'receipt_status':status,'method':method,'explorer_url':'https://nearblocks.io/txns/'+h}
            movements.append(out)
            if v and v[0]==w and status=='success' and kind in ['native_transfer','nep141_transfer','nep245_transfer']: matched_governance[v].append(out)
            if v and status=='failed': failed_governance[v].append(out)
            return out
        for i,x in raw.items():
            out=by_out[i]['outcome'];loc=entry['receiptBlocks'][i];assert by_out[i]['block_hash']==loc['blockHash']
            ok='SuccessValue' in out['status'] or 'SuccessReceiptId' in out['status']
            assert ok or 'Failure' in out['status']; status='success' if ok else 'failed'
            g=x['receipt'].get('Action') or x['receipt'].get('ActionV2')
            if not g: continue
            sender=x['predecessor_id'];recv=x['receiver_id']
            for act in g['actions']:
                kind=act if isinstance(act,str) else next(iter(act)); details={} if isinstance(act,str) else act[kind]
                if ok and kind=='FunctionCall' and details['method_name']=='add_proposal' and recv in proposals:
                    proposal_id=json.loads(base64.b64decode(out['status']['SuccessValue']))
                    p=props[(recv,proposal_id)]
                    assert p['proposer']==sender and int(p['submission_time'])==int(loc['timestampNs'])
                    assert (recv,proposal_id) not in deposits
                    deposits[(recv,proposal_id)]={'treasury':recv,'id':proposal_id,'proposer':sender,'amountYocto':details['deposit'],'submittedUTC':utc(loc['timestampNs']),'tx':h,'receiptId':i}
                if kind=='CreateAccount' and recv in WALLETS and h==created[recv]['tx']:
                    assert ok;created[recv].update({'verifiedBlock':loc['height'],'verifiedUTC':utc(loc['timestampNs']), 'receiptId':i})
                if sender in WALLETS: method_counts[(sender,kind,details.get('method_name','') if isinstance(details,dict) else '')]+=1
                if kind=='Transfer' and sender=='system' and ok:
                    val=int(details['deposit']); assert int(g['gas_price'])==0
                    if g['signer_id']=='system': balance_refunds[recv]+=val
                    elif g['signer_id']==recv: refunds[recv]+=val
                    else: raise AssertionError('Unclassified system refund')
                    if recv in WALLETS: incoming_gas[('balance' if g['signer_id']=='system' else 'gas',recv)]+=val
                elif kind=='Transfer' and sender in WALLETS and recv!=sender:
                    movement(i,sender,recv,'native_transfer','NEAR',details['deposit'],status)
                elif kind=='FunctionCall' and sender in WALLETS:
                    name=details['method_name']
                    actions.append({'wallet':sender,'receiver':recv,'method':name,'amountYocto':details['deposit'],'status':status,'tx':h,'receiptId':i})
                    if int(details['deposit']) and recv!=sender: movement(i,sender,recv,'attached_deposit','NEAR',details['deposit'],status,name)
                    if name in ['deposit_and_stake','stake','stake_all','unstake','unstake_all','withdraw_all'] and recv!=sender: pool_accounts.add((sender,recv))
                elif kind=='Stake' and (recv in WALLETS or sender in WALLETS):
                    movement(i,recv,recv,'validator_stake_level','NEAR',details['stake'],status)
                elif kind=='DeleteAccount' and recv in WALLETS: raise ValueError('Deleted tracked account requires separate beneficiary reconstruction')
            if not ok: continue
            for l in out['logs']:
                if not l.startswith('EVENT_JSON:'):continue
                ev=json.loads(l[len('EVENT_JSON:'):])
                if ev.get('standard') not in ['nep141','nep245']:continue
                if ev.get('event') not in ['ft_transfer','mt_transfer','ft_burn','mt_burn']:continue
                for d in ev['data']:
                    w=d.get('old_owner_id') or d.get('owner_id');to=d.get('new_owner_id') or ''
                    if w not in WALLETS:continue
                    assets=[recv] if ev['standard']=='nep141' else [recv+'/'+a for a in d['token_ids']]
                    values=[d['amount']] if ev['standard']=='nep141' else d['amounts']
                    assert len(assets)==len(values)
                    for asset,v in zip(assets,values):
                        k=(h,i,w,asset,str(int(v)),to)
                        matched_neg[k]+=1
                        if int(v)==0:continue
                        m=movement(i,w,to,ev['standard']+('_burn' if ev['event'].endswith('burn') else '_transfer'),asset,v,status)
                        all_events.append(m)
        gas.append({'root_transaction_utc':utc(entry['txTimestampNs']),'transaction_hash':h,'original_signer':signer,'delegated_senders':'|'.join(delegates),'transactions_final_status':next(iter(t['status'])),'receipt_count':len(by_out),'failed_receipt_count':sum('Failure' in o['outcome']['status'] for o in by_out.values()),'root_execution_yocto':str(rb),'receipt_execution_yocto':str(receipt_burn),'trace_reported_tokens_burnt_near':amount(rb+receipt_burn,24),'gas_burnt':root['gas_burnt']+sum(o['outcome']['gas_burnt'] for o in by_out.values()),'gas_refunds_near':amount(sum(refunds.values()),24),'gas_refunded_to':'|'.join(sorted(refunds)),'balance_refunds_near':amount(sum(balance_refunds.values()),24),'balance_refunded_to':'|'.join(sorted(balance_refunds)),'explorer_url':'https://nearblocks.io/txns/'+h})
    assert all('verifiedBlock' in c for c in created.values()),created
    assert index_neg==matched_neg,(index_neg-matched_neg,matched_neg-index_neg)
    assert set(deposits)==set(props)
    for row in movements:
        if row['entry_type']!='native_transfer' or row['proposal_id']=='':continue
        deposit=deposits[(row['wallet'],row['proposal_id'])]
        if row['counterparty']==deposit['proposer'] and row['amount_atomic']==deposit['amountYocto'] and row['receipt_status']=='success':
            assert 'matchedReturnTx' not in deposit
            row['entry_type']='matched_proposal_deposit_return'
            deposit['matchedReturnTx']=row['transaction_hash'];deposit['matchedReturnReceipt']=row['receipt_id']
    for (w,id), proposal in props.items():
        if w!=WALLETS[1] or 'Transfer' not in proposal['kind']:continue
        k=proposal['kind']['Transfer'];matching=[m for m in matched_governance[(w,id)] if m['asset']==k['token_id'] and m['counterparty']==k['receiver_id'] and m['amount_atomic']==k['amount']]
        if proposal['status']=='Approved': assert len(matching)==1,(id,matching)
        else: assert len(matching)==0
        if matching:
            m=matching[0]; blockhash=traces[m['transaction_hash']]['receiptBlocks'][m['receipt_id']]['blockHash']
            b=rpc('block',{'block_id':blockhash},args.collect)['header']
            assert b['hash']==blockhash and b['height']==m['block_height'] and utc(b['timestamp_nanosec'])==m['occurred_utc']
            checked_blocks.add(blockhash)
        payout_check.append({'id':id,'statusAtClosingBlock':proposal['status'],'amountRaw':k['amount'],'token':k['token_id'],'to':k['receiver_id'],'successfulTransfer':matching[0] if matching else None})
    for w, pools in staking_flags.items():
        matched={recv for wallet,recv in pool_accounts if wallet==w}
        for row in idx[w]['staking-txns']['rows']:matched.add(row['contract_account_id'])
        pools['pools']=sorted(matched)
        state=rpc('query',{'request_type':'view_account','account_id':w,'block_id':BLOCK},args.collect)
        assert state['block_hash']==BLOCK and state['block_height']==HEIGHT
        pools['nativeLiquidYocto']=state['amount']; pools['nativeLockedYocto']=state['locked'];pools['pinnedPoolBalances']={}
        for pool in matched:
            balances={}
            for method in ['get_account_staked_balance','get_account_unstaked_balance']:
                params=base64.b64encode(json.dumps({'account_id':w}).encode()).decode()
                q=rpc('query',{'request_type':'call_function','block_id':BLOCK,'account_id':pool,'method_name':method,'args_base64':params},args.collect)
                assert q['block_hash']==BLOCK
                balances[method]=json.loads(bytes(q['result']))
            pools['pinnedPoolBalances'][pool]=balances
    movements.sort(key=lambda r:(r['occurred_utc'],r['transaction_hash'],r['receipt_id'],r['entry_type']))
    gas.sort(key=lambda r:r['root_transaction_utc'])
    movement_file=write_csv('near-intents-verified-treasury-outflows',COLS,movements)
    gas_file=write_csv('near-intents-treasury-transaction-gas-and-refunds',GASCOL,gas)
    summary={'blockHeight':HEIGHT,'blockHash':BLOCK,'utc':utc(STAMP),'fastNearAccountHistory':{w:{'transactions':len(x['rows']),'pages':x['pages'],'oldestIndexedTx':x['rows'][-1]['transaction_hash'],'oldestIndexedUTC':utc(x['rows'][-1]['tx_block_timestamp'])} for w,x in histories.items()},'nearBlocksAssetHistory':{w:{kind:{'eventRows':len(st['rows']),'pages':st['pages']} for kind,st in x.items() if kind!='account'} for w,x in idx.items()},'created':created,'rpcVerifiedTransactions':len(traces),'verifiedNegativeTokenEvents':sum(matched_neg.values()),'verifiedFrontendTransferProposals':payout_check,'staking':staking_flags,'treasuryMethods':[{'wallet':w,'action':typ,'method':name,'occurrences':n} for (w,typ,name),n in sorted(method_counts.items())],'movementCSV':movement_file,'executionCSV':gas_file,'feeScope':'Final transaction-outcome and receipt-outcome tokens_burnt; not an operator income statement. The original signer funds the gas envelope, including sponsored transactions. Successful gas refund and balance refund transfers are reported separately; they are not deducted a second time from tokens_burnt.','scope':'FastNear account roles and NearBlocks recognized FT/MT/staking histories paged through terminal response; cash and tokens reported on successful outgoing actions and debit logs through the pinned block. Unknown off-chain entity, unindexed protocols, beneficial owners and downstream custody remain outside the enumerated transaction records.'}
    summary['verifiedProposalDeposits']=list(sorted(deposits.values(),key=lambda d:(d['treasury'],d['id'])))
    grouped=defaultdict(lambda:{'count':0,'atomic':0})
    for row in movements:
        if row['receipt_status']!='success':continue
        grp=grouped[(row['wallet'],row['counterparty'],row['entry_type'],row['asset'])]
        grp['count']+=1;grp['atomic']+=int(row['amount_atomic'])
    summary['verifiedOutgoingByRecipient']=[{'wallet':w,'recipient':recipient,'entryType':typ,'asset':asset,'count':v['count'],'amountAtomic':str(v['atomic']),'amountUnits':amount(v['atomic'],meta.get(asset))} for (w,recipient,typ,asset),v in sorted(grouped.items())]
    fee_groups=defaultdict(lambda:{'signedTransactions':0,'executionYocto':0,'gasRefundYocto':0,'balanceRefundYocto':0})
    for row in gas:
        g=fee_groups[row['original_signer']];g['signedTransactions']+=1
        g['executionYocto']+=int(row['root_execution_yocto'])+int(row['receipt_execution_yocto'])
        assert not row['gas_refunded_to'] or row['gas_refunded_to']==row['original_signer']
        g['gasRefundYocto']+=int(Decimal(row['gas_refunds_near'])*Decimal(10**24))
        g['balanceRefundYocto']+=int(Decimal(row['balance_refunds_near'])*Decimal(10**24))
    summary['reportedExecutionByOriginalSigner']={w:{'transactions':v['signedTransactions'],'executionNEAR':amount(v['executionYocto'],24),'gasRefundNEAR':amount(v['gasRefundYocto'],24)} for w,v in sorted(fee_groups.items())}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ['rpcVerifiedTransactions','verifiedNegativeTokenEvents','staking','movementCSV','executionCSV']},indent=2),flush=True)

if __name__=='__main__': main()
