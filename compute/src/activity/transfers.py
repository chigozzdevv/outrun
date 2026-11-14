"""Verify same-instruction transfers to documented collection accounts in the charged-account cohort."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import csv
import importlib.util
import io
import json
import subprocess
from collections import Counter
from decimal import Decimal, getcontext
from pathlib import Path
from compute.src.collectors.exchanges import signed_intents, nep413, ALPHA


spec=importlib.util.spec_from_file_location('payer',CODE_ROOT / 'compute/src/activity/accounts.py')
payer=importlib.util.module_from_spec(spec);spec.loader.exec_module(payer)
getcontext().prec=80

def add(counter,key,value):
    if int(value)>0: counter[key]+=int(value)

def unit_map(values):
    return json.dumps({k:str(v) for k,v in sorted(values.items())},separators=(',',':'))

def authenticated_implicit(payload,account):
    if not payload:return False
    pub=payload['public_key']
    if not pub.startswith('ed25519:') or len(account)!=64:return False
    digits=pub.split(':',1)[1];n=0
    for c in digits:n=n*58+ALPHA.index(c)
    decoded=b'\x00'*(len(digits)-len(digits.lstrip('1')))+n.to_bytes((n.bit_length()+7)//8,'big')
    return decoded.hex()==account

def derive():
    sources=payer.Sources()
    primary=ROOT/'public/data/near-intents-paid-account-cohort-2026-11-02.csv'
    rows=[]; sigs={}; eligible=0
    for r in csv.DictReader(primary.open()):
        if r['baselineStatus']!='verified-fee':continue
        eligible+=1;tx=r['baseline_tx'];x=sources.tx[tx];signed=signed_intents(x)
        o=next(o['outcome'] for o in x['receipts_outcome'] if o['id']==r['baseline_receiptId'])
        assert o['executor_id']=='intents.near' and any(t.startswith('Success') for t in o['status'])
        events=[json.loads(l[11:]) for l in o['logs'] if l.startswith('EVENT_JSON:')]
        for ih in json.loads(r['baseline_intentHashes']):
            a=r['account']
            executed=[d for e in events if e['standard']=='dip4' and e['event']=='transfer' for d in e['data'] if d.get('intent_hash')==ih and d.get('account_id')==a]
            targets=[d for d in executed if d['receiver_id'] in payer.AGG]
            if not targets:continue
            ledger_out,ledger_in=Counter(),Counter()
            for event in events:
                if event['standard']!='nep245':continue
                for move in event['data']:
                    name=event['event']
                    debit=(name=='mt_transfer' and move['old_owner_id']==a) or (name=='mt_burn' and move['owner_id']==a)
                    credit=(name=='mt_transfer' and move['new_owner_id']==a) or (name=='mt_mint' and move['owner_id']==a)
                    if debit or credit:
                        for token,units in zip(move['token_ids'],move['amounts'],strict=True):
                            if debit:add(ledger_out,token,units)
                            if credit:add(ledger_in,token,units)
            sig=signed.get(ih)
            verified=authenticated_implicit(sig,a)
            ins=[];neg,pos,withdraw,other,out=Counter(),Counter(),Counter(),Counter(),Counter()
            known=True;elements=[]
            if sig:
                msg=json.loads(sig['payload']['message']); assert msg['signer_id']==a and sig['payload']['recipient']=='intents.near'
                ins=msg['intents'];elements=[v for v in ins if v['intent']=='transfer']
                assert [(v['receiver_id'],v['tokens'],v.get('memo')) for v in elements]==[(v['receiver_id'],v['tokens'],v.get('memo')) for v in executed]
                observed_diff=[d['diff'] for e in events if (e['standard'],e['event'])==('dip4','token_diff') for d in e['data'] if d['account_id']==a and d['intent_hash']==ih]
                assert [v['diff'] for v in ins if v['intent']=='token_diff']==observed_diff
                if verified:sigs[ih]=dict(hash=nep413(sig).hex(),pk=sig['public_key'],sig=sig['signature'],collector=a)
                for v in ins:
                    kind=v['intent']
                    if kind=='token_diff':
                        for t,amt in v['diff'].items():
                            if int(amt)<0:add(neg,t,-int(amt))
                            else:add(pos,t,amt)
                    elif kind=='transfer':
                        for t,amt in v['tokens'].items():add(out if v['receiver_id'] in payer.AGG else other,t,amt)
                    elif kind=='ft_withdraw':add(withdraw,'nep141:'+v['token'],v['amount'])
                    elif kind=='mt_withdraw':
                        for t,amt in zip(v['token_ids'],v['amounts'],strict=True):add(withdraw,'nep245:'+v['token']+':'+t,amt)
                    elif kind in ('storage_deposit','native_withdraw'):add(withdraw,'nep141:wrap.near',v['amount'])
                    else:known=False
                    # Optional NEAR storage registration is an additional same-asset debit.
                    if v.get('storage_deposit') is not None and kind in ('ft_withdraw','mt_withdraw'):
                        add(withdraw,'nep141:wrap.near',v['storage_deposit'])
            for d in targets:
                for t,qty in d['tokens'].items():
                    assert int(qty)>0
                    denom=neg[t]+out[t]+other[t]
                    qualifies=bool(verified and known and neg[t] and not pos[t] and not withdraw[t] and denom and ledger_out[t]==denom and ledger_in[t]==0)
                    rows.append(dict(account=a,transaction_hash=tx,receipt_id=r['baseline_receiptId'],intent_hash=ih,receiver=d['receiver_id'],token_id=t,transfer_atomic=qty,memo=d.get('memo') or '',referrals=r['baseline_referrals'],nep413_implicit_signature_verified=verified,all_input_trade_debits_atomic=unit_map(neg) if sig else '',all_trade_credits_atomic=unit_map(pos) if sig else '',all_known_aggregator_transfers_atomic=unit_map(out) if sig else '',all_other_transfers_atomic=unit_map(other) if sig else '',all_withdrawal_and_storage_debits_atomic=unit_map(withdraw) if sig else '',same_asset_receipt_ledger_debits_atomic=str(ledger_out[t]),same_asset_receipt_ledger_credits_atomic=str(ledger_in[t]),ratio_basis='transfer amount / all signed debits of the same exclusively input asset, reconciled to receipt ledger debits' if qualifies else '',same_asset_denominator_atomic=str(denom) if qualifies else '',observed_transfer_share_bps=format(Decimal(qty)*10000/denom,'.6f') if qualifies else ''))
    if sigs:subprocess.run(['node',str(CODE_ROOT / 'compute/src/verify/signatures.mjs')],input=json.dumps(list(sigs.values())),text=True,check=True)
    assert rows
    st=io.StringIO(newline='');wr=csv.DictWriter(st,fieldnames=list(rows[0]),lineterminator='\n');wr.writeheader();wr.writerows(rows)
    ratios=[Decimal(r['observed_transfer_share_bps']) for r in rows if r['observed_transfer_share_bps']]
    summary=dict(baselineWitnesses=eligible,accountsWithTransferToKnownAggregator=len({r['account'] for r in rows}),sameSignedIntentTransferAssetRows=len(rows),distinctTokenIds=len({r['token_id'] for r in rows}),independentlyVerifiedImplicitSignatures=len(sigs),sameInputAssetRatios=len(ratios),minObservedBps=str(min(ratios)) if ratios else None,maxObservedBps=str(max(ratios)) if ratios else None,countsByObservedBps=dict(sorted(Counter(str(v) for v in ratios).items(),key=lambda pair:Decimal(pair[0]))),nonemptyMemos=sum(bool(r['memo']) for r in rows),scope='One qualifying fee witness per baseline signer, not all its trades or a protocol take-rate estimate. Transfers require the same executed account and intent hash as the charge. Ratios require an independently verified NEP-413 implicit signature, exhaustively understood signed debits, a spent input asset with no positive trade leg or withdrawal of the same asset, and all other same-asset transfer debits in the denominator. The entire actual ledger debit for the asset must equal the signed denominator, with no credit of that asset to the signer in the receipt. Blank ratios are unavailable, not zero. Transfer purpose and partner cash entitlements remain unassigned.')
    return {ROOT/'public/data/near-intents-same-signer-aggregation-transfers-2026-11-02.csv':st.getvalue().encode(),payer.DATA/'aggregation-transfer-summary.json':(json.dumps(summary,indent=2)+'\n').encode()}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build',action='store_true');a=p.parse_args()
    for path,data in derive().items():
        if a.build:path.write_bytes(data)
        else:assert path.read_bytes()==data,f'{path.name} differs'
