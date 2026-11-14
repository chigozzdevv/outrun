"""Independently verify signed collector withdrawals and same-intent exchange legs from finalized RPC trees."""
from compute.src.paths import ROOT, CODE_ROOT
import base64
from collections import Counter
import csv
from decimal import Decimal, localcontext
import gzip
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
from zipfile import ZipFile


EVIDENCE=ROOT/'data/verified/withdrawal-rpc-2026-11-02'
PUBLIC=ROOT/'public/data'
WRAP='nep141:wrap.near'
ALPHA='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def b58(data):
    zeros=len(data)-len(data.lstrip(b'\0'))
    n=int.from_bytes(data)
    digits=''
    while n:
        n,i=divmod(n,58)
        digits=ALPHA[i]+digits
    return '1'*zeros+digits


def nep413(s):
    p=s['payload']
    def string(value):
        raw=value.encode()
        return struct.pack('<I',len(raw))+raw
    nonce=base64.b64decode(p['nonce'],validate=True)
    assert len(nonce)==32
    body=struct.pack('<I',2**31+413)+string(p['message'])+nonce+string(p['recipient'])
    body+=(b'\0' if p.get('callbackUrl') is None else b'\1'+string(p['callbackUrl']))
    return hashlib.sha256(body).digest()


def signed_intents(x):
    def inspect(actions):
        for a in actions:
            if 'Delegate' in a:
                yield from inspect(a['Delegate']['delegate_action']['actions'])
            if a.get('FunctionCall',{}).get('method_name')=='execute_intents':
                j=json.loads(base64.b64decode(a['FunctionCall']['args']))
                yield from j['signed']
    candidates=inspect(x['transaction']['actions'])
    seen={}
    for s in candidates:
        if s.get('standard')=='nep413':
            seen[b58(nep413(s))]=s
    for rec in x['receipts']:
        for s in inspect(rec.get('receipt',{}).get('Action',{}).get('actions',[])):
            if s.get('standard')=='nep413':
                seen[b58(nep413(s))]=s
    return seen


def collect(row, raw):
    x=json.loads(raw)['result']; tx=row['transaction_hash']; collector=row['collector_account']
    qty=int(Decimal(row['service_output_near'])*Decimal(10)**24)
    candidates=[]
    for outcome in x['receipts_outcome']:
        o=outcome['outcome']
        if o['executor_id']!='intents.near' or not any(k.startswith('Success') for k in o['status']):
            continue
        events=[json.loads(s[len('EVENT_JSON:'):]) for s in o['logs'] if s.startswith('EVENT_JSON:')]
        for e in events:
            if e['event'] in ('token_diff','native_withdraw','intents_executed','transfer'):
                assert e['standard']=='dip4', (tx,'unexpected verifier event standard')
            if e['event'] in ('mt_transfer','mt_burn'):
                assert e['standard']=='nep245', (tx,'unexpected ledger event standard')
        def event_data(name):
            return [d for e in events if e['event']==name for d in e['data']]
        for w in event_data('native_withdraw'):
            if (w['account_id'], w['receiver_id'],int(w['amount']))!=(collector,row['treasury_recipient'],qty):
                continue
            burns=[d for d in event_data('mt_burn') if d['owner_id']==collector and d.get('memo')=='withdraw' and d['token_ids']==[WRAP] and list(map(int,d['amounts']))==[qty]]
            assert len(burns)==1, (tx,'wrapped backing withdrawal')
            assert any(d['intent_hash']==w['intent_hash'] and d['account_id']==collector for d in event_data('intents_executed')), (tx,'matched intent execution')
            candidates.append((w,events))
    assert len(candidates)==1,(tx,len(candidates))
    w,events=candidates[0]
    signed=signed_intents(x).get(w['intent_hash'])
    assert signed and signed['payload']['recipient']=='intents.near', (tx,'signed instruction')
    j=json.loads(signed['payload']['message'])
    assert j['signer_id']==collector and any((i.get('intent'),i.get('receiver_id'),i.get('amount'))==('native_withdraw',row['treasury_recipient'],str(qty)) for i in j['intents']), (tx,'signed withdrawal')
    signature={'hash':nep413(signed).hex(),'pk':signed['public_key'],'sig':signed['signature'],'collector':collector}
    diffs=[d for e in events if e['event']=='token_diff' for d in e['data'] if d['account_id']==collector and d['intent_hash']==w['intent_hash']]
    signed_diff=[i['diff'] for i in j['intents'] if i.get('intent')=='token_diff']
    assert [d['diff'] for d in diffs]==signed_diff,(tx,'signed and executed differences')
    matching=[d for d in diffs if int(d['diff'].get(WRAP,0))>0 and any(int(v)<0 and k!=WRAP for k,v in d['diff'].items())]
    if not matching:
        return signature,None
    credits=sum(int(d['diff'][WRAP]) for d in matching)
    assert len(matching)==len(diffs) and all(len(d['diff'])==2 for d in matching),(tx,'complex collector exchange requires individual review')
    spent=Counter(); reported_fees=Counter()
    for d in matching:
        spent.update({k:-int(v) for k,v in d['diff'].items() if int(v)<0})
        reported_fees.update({k:int(v) for k,v in d.get('fees_collected',{}).items()})
    movements=[d for e in events if e['event']=='mt_transfer' for d in e['data']]
    received=Counter();debited=Counter()
    for d in movements:
        for coin,v in zip(d['token_ids'],d['amounts'],strict=True):
            if d['new_owner_id']==collector:received[coin]+=int(v)
            if d['old_owner_id']==collector:debited[coin]+=int(v)
    # Other signed intents can transfer an independently logged quote improvement to this collector.
    wrap_allocations=sum(
        (int(d.get('receiver_id')==collector)-int(d.get('account_id')==collector))*int(d['tokens'].get(WRAP,0))
        for e in events if e['event']=='transfer' for d in e['data']
    )
    assert received[WRAP]-debited[WRAP]==credits+wrap_allocations,(tx,credits,wrap_allocations,received[WRAP],debited[WRAP])
    assert all(debited[k]-received[k]==v for k,v in spent.items()),(tx,'net input amount mismatch')
    return signature,dict(transaction_hash=tx,withdrawal_utc=row['withdrawal_utc'],collector_account=collector,
        intent_hash=w['intent_hash'],input_raw_units=json.dumps(dict(spent),sort_keys=True,separators=(',',':')),
        exchange_event_fees_collected_raw_units=json.dumps(dict(reported_fees),sort_keys=True,separators=(',',':')),
        wrapped_near_received=format(Decimal(credits)/Decimal(10)**24,'f'),
        other_net_wrapped_near_transfers=format(Decimal(wrap_allocations)/Decimal(10)**24,'f'),native_near_withdrawn=row['service_output_near'],
        transaction_url='https://nearblocks.io/txns/'+tx)


def verify_sigs(rows):
    subprocess.run(['node', str(CODE_ROOT / 'compute/src/verify/signatures.mjs')], input=json.dumps(rows), text=True, check=True)


def input_units():
    metadata_file='research/institutional-2026-10-28/revenue/raw/oneclick-supported-tokens.json'
    manifest=json.loads((ROOT/'data/provenance/manifest.json').read_text())
    digest=next(i['sha256'] for i in manifest['files'] if i['path']==metadata_file)
    with ZipFile(ROOT/'data/provenance/source-data.zip') as archive:
        data=archive.read(metadata_file)
    assert hashlib.sha256(data).hexdigest()==digest
    return {r['assetId']:r for r in json.loads(data)}


def analyze(rows, load, save):
    sigs=[]; legs=[]
    for r in rows:
        data=load(r['transaction_hash'])
        if not data:continue
        s,leg=collect(r,data);sigs.append(s)
        if leg:legs.append(leg)
    verify_sigs(sigs)
    by_asset=Counter(); fees=Counter()
    for leg in legs:
        by_asset.update(json.loads(leg['input_raw_units']))
        fees.update(json.loads(leg['exchange_event_fees_collected_raw_units']))
    meta=input_units()
    def quantity(asset,raw):
        return format(Decimal(raw).scaleb(-meta[asset]['decimals']), 'f') if asset in meta else None
    for leg in legs:
        inputs=json.loads(leg['input_raw_units'])
        display=[{'asset':k,'symbol':meta[k]['symbol'] if k in meta else None,'amount':quantity(k,v)} for k,v in inputs.items()]
        leg['input_asset_and_quantity']=json.dumps(display,separators=(',',':'))
    summary={'signedWithdrawals':len(sigs),'uniqueSignedIntents':len({s['hash'] for s in sigs}),'executedSameIntentConversions':len(legs),'wrappedNEARReceivedInMatchedConversionLegs':str(sum(Decimal(r['wrapped_near_received']) for r in legs)),'nativeNEARWithdrawnInThoseTransactions':str(sum(Decimal(r['native_near_withdrawn']) for r in legs)),'inputAssets':[{'assetId':k,'symbol':meta[k]['symbol'] if k in meta else None,'rawUnits':str(v),'units':quantity(k,v)} for k,v in sorted(by_asset.items())]}
    summary['otherNetWrappedNearTransfersOnMatchedReceipts']=str(sum(Decimal(r['other_net_wrapped_near_transfers']) for r in legs))
    summary['exchangeEventFeesCollected']=[{'assetId':k,'symbol':meta[k]['symbol'] if k in meta else None,'rawUnits':str(v),'units':quantity(k,v)} for k,v in sorted(fees.items())]
    sample=next((r for r in legs if r['transaction_hash']=='4Fwg4notuLyvjkVDBaJSUs9AAW1PZmAEkBLDnSzK4zGY'),None)
    if sample:
        asset,raw=next(iter(json.loads(sample['input_raw_units']).items()))
        assert meta[asset]['symbol']=='USDC' and meta[asset]['blockchain']=='arb'
        summary['stablecoinExample']={
            'transactionHash':sample['transaction_hash'], 'date':sample['withdrawal_utc'][:10],
            'fromToken':meta[asset]['symbol'], 'fromChain':'Arbitrum', 'fromAssetId':asset, 'units':quantity(asset,raw),
            'principalWrappedNEAR':sample['wrapped_near_received'],
            'additionalRelayWrappedNEAR':sample['other_net_wrapped_near_transfers'],
            'nativeWithdrawalNEAR':sample['native_near_withdrawn'], 'publicURL':sample['transaction_url'],
        }
    summary['scope']='Only collector-signed exchanges in the very same verified intent as a delivered native withdrawal. Later withdrawals can use earlier inventory. Exchange inputs establish token units spent in these events, not payer identity or net cash earnings.'
    if save:
        (EVIDENCE/'conversion-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        fields=list(legs[0]) if legs else ['transaction_hash']
        with (EVIDENCE/'collector-conversions.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields,lineterminator='\n'); writer.writeheader();writer.writerows(legs)
        if sigs:
            (PUBLIC/'near-intents-collector-conversions-2026-08-23-to-2026-10-28.csv').write_bytes((EVIDENCE/'collector-conversions.csv').read_bytes())
    return summary


if __name__=='__main__':
    with localcontext() as c:
        c.prec=80
        if '--partial' in sys.argv:
            import runpy
            ctx=runpy.run_path(str(CODE_ROOT / 'compute/src/withdrawals/reconcile.py'))
            rows=ctx['records']()
            work=ctx['WORK']
            def load(tx):
                p=work/(tx+'.gz')
                return gzip.decompress(p.read_bytes()) if p.exists() else None
            print(json.dumps(analyze(rows,load,False)))
        else:
            rows=list(csv.DictReader((EVIDENCE/'withdrawal-rpc-matches.csv').open()))
            with ZipFile(EVIDENCE/'archival-responses.zip') as raw:
                print(json.dumps(analyze(rows,lambda tx:raw.read(tx+'.json'),True)))
