"""Prioritize and verify candidate exchanges in separate receipts, with a fully enumerated first-day slice."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import base64
from collections import Counter,defaultdict
import csv
import fcntl
from decimal import Decimal,localcontext
import gzip
import http.client
import json
from pathlib import Path
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor,as_completed
from zipfile import ZipFile
from compute.src.collectors.history import ROOT,DATA,PAGES,IDS,VERIFIER,WRAP,ORIGINAL_START,ORIGINAL_END,restore,utc
from compute.src.collectors.exchanges import signed_intents,verify_sigs,input_units,nep413,ALPHA

RPC=DATA/'transaction-rpc'
SAMPLE_END=1787529600000000000
CUTS=[ORIGINAL_START,1788220799923365214,1790812799562730410,ORIGINAL_END]
con=threading.local()
lock=threading.Lock()
next_req=0.
RPC_PACE=Path('/private/tmp/near-collector-receipt-rpc-rate')

def rpc_pace(delay=None):
    RPC_PACE.touch(exist_ok=True)
    while True:
        with RPC_PACE.open('r+') as f:
            fcntl.flock(f,fcntl.LOCK_EX);raw=f.read();due=float(raw) if raw else 0;now=time.time()
            if delay is not None:due=max(due,now+delay)
            elif due<=now:due=now+1.30
            else:
                fcntl.flock(f,fcntl.LOCK_UN);time.sleep(min(1,due-now));continue
            f.seek(0);f.truncate();f.write(str(due));f.flush();return


def rows_available(paths=None):
    seen={}
    for p in sorted(PAGES.glob('*.json.gz')) if paths is None else paths:
        req,body=restore(p)
        for row in body['transfers']:
            if not ORIGINAL_START<int(row['block_timestamp'])<=ORIGINAL_END:continue
            if not row['asset_id'].startswith(VERIFIER):continue
            assert req['account_id']==row['account_id'] and row['receipt_account_id']=='intents.near'
            key=(row['account_id'],row['block_height'],row['transfer_index'],row['amount'])
            if key in seen:
                old=seen[key]
                assert all(old[k]==row[k] for k in ('receipt_id','asset_id','amount','other_account_id','transfer_type','transaction_id')),key
            seen[key]=row
    return list(seen.values())


def candidates(rows):
    groups=defaultdict(list)
    for r in rows:groups[(r['account_id'],r['receipt_id'])].append(r)
    out=[]
    for (owner,rid),events in groups.items():
        w=sum(int(r['amount']) for r in events if r['asset_id']==WRAP)
        debits=Counter()
        for r in events:
            v=int(r['amount'])
            if v<0 and r['asset_id']!=WRAP:debits[r['asset_id'][len(VERIFIER):]]-=v
        if w<=0 or not debits:continue
        txs={r['transaction_id'] for r in events}; assert len(txs)==1
        out.append(dict(collector=owner,rid=rid,tx=next(iter(txs)),w=w,debits=debits,stamp=int(events[0]['block_timestamp']),block=int(events[0]['block_height']),signer=events[0]['signer_id']))
    return out


def fetch_tx(c):
    global next_req
    tx=c['tx']; p=RPC/(tx+'.json.gz')
    if p.is_file():return True
    req={'jsonrpc':'2.0','id':'internal-collector-exchange','method':'EXPERIMENTAL_tx_status','params':{'tx_hash':tx,'sender_account_id':c['signer'],'wait_until':'FINAL'}}
    for attempt in range(5):
        rpc_pace()
        try:
            if not getattr(con,'client',None):con.client=http.client.HTTPSConnection('archival-rpc.mainnet.fastnear.com',timeout=23,context=ssl.create_default_context())
            con.client.request('POST','/',json.dumps(req),{'Content-Type':'application/json','User-Agent':'IntentThesis/1.0 public verifier research'})
            resp=con.client.getresponse();raw=resp.read()
            if resp.status==429:
                header=resp.getheader('Retry-After','0');rpc_pace(max(30,int(header)) if header.isdigit() else 30)
            if resp.status!=200:raise ValueError('HTTP '+str(resp.status))
            x=json.loads(raw).get('result',{});assert x['transaction']['hash']==tx and x['final_execution_status']=='FINAL'
            p.write_bytes(gzip.compress(raw,mtime=0));return True
        except (OSError,http.client.HTTPException,ValueError,KeyError):
            if getattr(con,'client',None):con.client.close();con.client=None
            time.sleep(attempt+1)
    return False


def archived():
    return ZipFile(ROOT/'data/verified/withdrawal-rpc-2026-11-02/archival-responses.zip')


def collect(batch):
    external=set()
    with archived() as z: original={p[:-5] for p in z.namelist()}
    raw_data=rows_available(); cand=candidates(raw_data)
    cand.sort(key=lambda c:(c['stamp']>=SAMPLE_END,-c['w'],c['tx'] or ''))
    chosen=[]
    for c in cand:
        tx=c['tx']
        if not tx or tx in original or tx in external or (RPC/(tx+'.json.gz')).is_file():continue
        external.add(tx);chosen.append(c)
        if len(chosen)>=batch:break
    print('candidate_receipts',len(cand),'chosen_new_tree_count',len(chosen),flush=True)
    RPC.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut={pool.submit(fetch_tx,c):c for c in chosen}
        for ix,f in enumerate(as_completed(fut),1):
            if not f.result():print('unavailable',fut[f]['tx'],flush=True)
            if ix%25==0:print('completed',ix,flush=True)


def outcome_events(x,rid):
    matches=[o for o in x['receipts_outcome'] if o['id']==rid]
    assert len(matches)==1
    out=matches[0]['outcome']
    assert out['executor_id']=='intents.near' and any(k.startswith('Success') for k in out['status'])
    return [json.loads(s[11:]) for s in out['logs'] if s.startswith('EVENT_JSON:')]


def verify_candidate(c,raw):
    x=json.loads(raw)['result']; assert x['transaction']['hash']==c['tx'] and x['final_execution_status']=='FINAL'
    return verify_effects(c,outcome_events(x,c['rid']),signed_intents(x))

def signed_transfer_binding(payer,ih,events,signed):
    assert any(v['account_id']==payer and v['intent_hash']==ih for e in events if e['event']=='intents_executed' for v in e['data'])
    data=lambda name:[v for e in events if e['event']==name for v in e['data'] if v['account_id']==payer and v['intent_hash']==ih]
    diffs=data('token_diff')
    detail=dict(signatureBinding='finalized verifier execution',payerSwapInSameIntent=bool(diffs),swapDiffs=[v['diff'] for v in diffs])
    if ih not in signed:return detail,None
    payload=signed[ih];message=json.loads(payload['payload']['message']);assert payload['payload']['recipient']=='intents.near' and message['signer_id']==payer
    def transfers(items):return [(v['receiver_id'],v['tokens'],v.get('memo','')) for v in items]
    assert transfers([v for v in message['intents'] if v['intent']=='transfer'])==transfers(data('transfer'))
    assert [v['diff'] for v in message['intents'] if v['intent']=='token_diff']==detail['swapDiffs']
    prefix,encoded=payload['public_key'].split(':',1);assert prefix=='ed25519'
    n=0
    for character in encoded:n=n*58+ALPHA.index(character)
    key=bytes(len(encoded)-len(encoded.lstrip('1')))+n.to_bytes((n.bit_length()+7)//8,'big');assert len(key)==32
    detail.update(signatureBinding='Ed25519 implicit payer' if key.hex()==payer else 'Ed25519 verifier-accepted key',referrals=[v.get('referral') for v in message['intents'] if v['intent']=='token_diff'])
    return detail,dict(hash=nep413(payload).hex(),pk=payload['public_key'],sig=payload['signature'],collector=key.hex(),role='payer')


def verify_effects(c,events,signed):
    owner=c['collector']; net=Counter()
    movements=[]; counterparty_credits=Counter(); positive_transfer_credits=0
    for event in events:
        if event['event'] in ('transfer','token_diff','intents_executed','native_withdraw'):
            assert event['standard']=='dip4'
    for e in events:
        if e['event'] not in ('mt_transfer','mt_burn','mt_mint'):continue
        assert e['standard']=='nep245'
        for d in e['data']:
            f=d.get('old_owner_id',d.get('owner_id') if e['event']=='mt_burn' else None)
            t=d.get('new_owner_id',d.get('owner_id') if e['event']=='mt_mint' else None)
            if owner not in (f,t):continue
            for k,v in zip(d['token_ids'],d['amounts'],strict=True):
                net[k]+=(int(t==owner)-int(f==owner))*int(v)
                movements.append(dict(asset=k,amount=str(v),origin=f,destination=t,kind=e['event'],memo=d.get('memo')))
                if t==owner and k=='nep141:wrap.near':counterparty_credits[f or '(mint)']+=int(v)
    assert net['nep141:wrap.near']==c['w'],(c['tx'],'canonical ledger wNEAR vs indexer')
    sigs=[]; positive=0; paid=Counter(); charged=Counter(); legs=[]; other_diffs=Counter(); bundled=[]; transfer_evidence=[]
    intent_hashes={leg['intent_hash'] for event in events if event['event']=='intents_executed' for leg in event['data'] if leg['account_id']==owner}
    executed_diffs=defaultdict(list)
    for event in events:
        if event['event']=='token_diff':
            for row in event['data']:
                if row['account_id']==owner:executed_diffs[row['intent_hash']].append(row['diff'])
    verified_hashes=set()
    for e in events:
        if e['event'] not in ('token_diff','transfer'):continue
        assert e['standard']=='dip4'
        for d in e['data']:
            if e['event']=='transfer':
                positive_transfer_credits+=(int(d.get('receiver_id')==owner)-int(d.get('account_id')==owner))*int(d.get('tokens',{}).get('nep141:wrap.near',0))
                if d.get('receiver_id')==owner and int(d.get('tokens',{}).get('nep141:wrap.near',0))>0:
                    binding,sig=signed_transfer_binding(d['account_id'],d['intent_hash'],events,signed)
                    if sig:sigs.append(sig)
                    transfer_evidence.append(dict(account=d['account_id'],amount=d['tokens']['nep141:wrap.near'],intentHash=d['intent_hash'],memo=d.get('memo',''),**binding))
                continue
            if d['account_id']!=owner:continue
            diff=d['diff']; amount=int(diff.get('nep141:wrap.near','0'))
            spent={k:-int(v) for k,v in diff.items() if k!='nep141:wrap.near' and int(v)<0}
            if amount<=0 or not spent:
                other_diffs.update({k:int(v) for k,v in diff.items()})
                continue
            payload=signed.get(d['intent_hash']);assert payload and payload['payload']['recipient']=='intents.near',(c['tx'],'collector signature missing')
            decoded=json.loads(payload['payload']['message']); assert decoded['signer_id']==owner
            assert d['intent_hash'] in intent_hashes and [leg['diff'] for leg in decoded['intents'] if leg.get('intent')=='token_diff']==executed_diffs[d['intent_hash']],(c['tx'],'ordered executed token differences do not match signed payload')
            if d['intent_hash'] not in verified_hashes:
                sigs.append(dict(hash=nep413(payload).hex(),pk=payload['public_key'],sig=payload['signature'],collector=owner));verified_hashes.add(d['intent_hash'])
            positive+=amount;paid.update(spent);charged.update({k:int(v) for k,v in d.get('fees_collected',{}).items()})
            legs.append(d['intent_hash'])
            other_received={k:int(v) for k,v in diff.items() if k!='nep141:wrap.near' and int(v)>0}
            if other_received:bundled.append(dict(intent=d['intent_hash'],otherPositiveAssetsRaw=other_received))
    # Indexed gross negative inputs likewise must be present in the actual ledger transfers.
    for k,v in c['debits'].items():
        assert sum(int(m['amount']) for m in movements if m['origin']==owner and m['asset']==k)==v,(c['tx'],k,'raw input movement')
    minted_minus_burnt=sum((int(m['destination']==owner)-int(m['origin']==owner))*int(m['amount']) for m in movements if m['asset']=='nep141:wrap.near' and m['kind'] in ('mt_mint','mt_burn'))
    # Protocol fees charged to traders can originate outside a collector's signed changes.
    unrelated=net['nep141:wrap.near']-positive-other_diffs['nep141:wrap.near']-positive_transfer_credits-minted_minus_burnt
    return dict(tx=c['tx'],receipt_id=c['rid'],collector=owner,time_utc=utc(c['stamp']),block_height=c['block'],ledger_wnear_net_raw=str(net['nep141:wrap.near']),observed_wnear_credits_raw=str(sum(counterparty_credits.values())),signed_exchange_wnear_raw=str(positive),input_raw_units=json.dumps(dict(paid),sort_keys=True),exchange_fees_raw_units=json.dumps(dict(charged),sort_keys=True),other_signed_wnear_change_raw=str(other_diffs['nep141:wrap.near']),explicit_net_wnear_transfers_raw=str(positive_transfer_credits),mint_minus_burn_wnear_raw=str(minted_minus_burnt),unattributed_net_wnear_raw=str(unrelated),wnear_counterparties_raw=json.dumps(dict(counterparty_credits),sort_keys=True),explicit_incoming_transfers=json.dumps(transfer_evidence,sort_keys=True),bundled_outputs=json.dumps(bundled,sort_keys=True),signed_exchange_intents='|'.join(legs)),sigs


def replay():
    rows=rows_available(); groups=candidates(rows);sigs=[];verified=[]
    with archived() as z:
        known={n[:-5] for n in z.namelist()}
        for c in groups:
            if not c['tx']:continue
            p=RPC/(c['tx']+'.json.gz')
            if c['tx'] in known:raw=z.read(c['tx']+'.json')
            elif p.is_file():raw=gzip.decompress(p.read_bytes())
            else:continue
            v,ss=verify_candidate(c,raw); v['in_original_794']=str(c['tx'] in known).lower(); verified.append(v);sigs.extend(ss)
    if sigs:verify_sigs(sigs)
    return groups,verified,rows


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['collect','replay']);parser.add_argument('--batch',type=int,default=100);opt=parser.parse_args()
    if opt.mode=='collect':collect(opt.batch)
    else:
        c,vs,rs=replay()
        print('unique_transfer_rows',len(rs),'two_sided_candidates',len(c),'verified_candidates',len(vs),'wrap_quantity',sum(int(v['signed_exchange_wnear_raw']) for v in vs)/1e24)
