"""Reconcile explicit cross-chain payout instructions in the fee-account baseline.

An identical destination can belong to a customer, integrator, exchange, bridge or test.
These records demonstrate repeated instructions to an address. They do not identify its owner.
"""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import csv
import hashlib
import io
import json
import runpy
import subprocess
from collections import defaultdict
from pathlib import Path
from compute.src.collectors.exchanges import signed_intents, nep413


PAYER = runpy.run_path(str(CODE_ROOT / 'compute/src/activity/transfers.py'))

def destination(action):
    memo = action.get('memo', '')
    if action['token'].endswith('.omft.near') and action['receiver_id']==action['token'] and memo.startswith('WITHDRAW_TO:'):
        target=memo.removeprefix('WITHDRAW_TO:')
        return ('omft memo',action['token'],target) if target else None
    if action['receiver_id']=='omni.bridge.near' and action.get('msg'):
        target=json.loads(action['msg']).get('recipient','')
        chain,sep,addr=target.partition(':')
        return ('omni bridge message',chain,addr) if sep and chain and addr else None
    return None

def derive():
    data=PAYER['payer'].DATA
    contract=PAYER['payer'].Sources()
    cohort=ROOT/'public/data/near-intents-paid-account-cohort-2026-11-02.csv'
    rows=[];signatures={};routed=defaultdict(set)
    for account in csv.DictReader(cohort.open()):
        if account['baselineStatus']!='verified-fee':continue
        a=account['account'];tx=contract.tx[account['baseline_tx']]
        out=next(o['outcome'] for o in tx['receipts_outcome'] if o['id']==account['baseline_receiptId'])
        events=[json.loads(l.removeprefix('EVENT_JSON:')) for l in out['logs'] if l.startswith('EVENT_JSON:')]
        signed=signed_intents(tx)
        for h in json.loads(account['baseline_intentHashes']):
            sig=signed.get(h)
            if not PAYER['authenticated_implicit'](sig,a):continue
            assert sig['payload']['recipient']=='intents.near'
            msg=json.loads(sig['payload']['message'])
            assert msg['signer_id']==a
            for act in msg['intents']:
                if act['intent']!='ft_withdraw':continue
                key=destination(act)
                if not key:continue
                matches=[w for e in events if (e['standard'],e['event'])==('dip4','ft_withdraw') for w in e['data'] if w['account_id']==a and w['intent_hash']==h and all(w.get(k)==v for k,v in act.items() if k!='intent')]
                assert matches and int(act['amount'])>0
                burns=[d for e in events if (e['standard'],e['event'])==('nep245','mt_burn') for d in e['data'] if d['memo']=='withdraw' and d['owner_id']==a for coin,v in zip(d['token_ids'],d['amounts'],strict=True) if coin=='nep141:'+act['token'] and int(v)==int(act['amount'])]
                if not burns:continue
                signatures[h]=dict(hash=nep413(sig).hex(),pk=sig['public_key'],sig=sig['signature'],collector=a)
                digest=hashlib.sha256(json.dumps(key,separators=(',',':')).encode()).hexdigest()
                routed[digest].add(a)
                rows.append(dict(account=a,utc=account['baseline_utc'],transaction_hash=account['baseline_tx'],receipt_id=account['baseline_receiptId'],intent_hash=h,referrals=account['baseline_referrals'],destination_format=key[0],destination_scope=key[1],destination_sha256=digest,withdrawal_token=act['token'],withdrawal_atomic=act['amount']))
    assert rows
    subprocess.run(['node',str(CODE_ROOT / 'compute/src/verify/signatures.mjs')],input=json.dumps(list(signatures.values())),text=True,check=True)
    for r in rows:r['distinct_baseline_signers_at_destination']=len(routed[r['destination_sha256']])
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)
    shared={d:ac for d,ac in routed.items() if len(ac)>1}
    summary=dict(baselinePaidSignersWithExplicitDestination=len({r['account'] for r in rows}),signedWithdrawalInstructions=len(rows),distinctDestinations=len(routed),destinationsNamedByMultiplePaidSignerAccounts=len(shared),distinctSignersAtSharedDestinations=len(set().union(*shared.values())),scope='One qualifying August 1 baseline witness per signer. Only independently verified implicit NEP-413 signatures, matching executed FT withdrawal events and same-amount internal withdrawal burns qualify. OMFT destinations require exact token contract and identical memo address. Omni Bridge destinations require an exact chain-prefixed recipient. Digest is SHA-256 of the JSON tuple [format, scope, exact-case address]. An instruction is not proof of payout completion on its destination chain or control of that address by a customer.')
    return {ROOT/'public/data/near-intents-paid-signer-destinations-2026-11-02.csv':stream.getvalue().encode(),data/'destination-summary.json':(json.dumps(summary,indent=2)+'\n').encode()}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--build',action='store_true');args=parser.parse_args()
    for path,body in derive().items():
        if args.build:path.write_bytes(body)
        else:assert path.read_bytes()==body,f'Published evidence differs: {path.name}'
