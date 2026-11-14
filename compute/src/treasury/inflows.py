"""Recover the initiating owner of each native receipt in the frozen 60-day treasury ledger."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import base64
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import gzip
import hashlib
import io
import json
from pathlib import Path
import zipfile

from compute.src.collectors.batches import BATCH, HOST, packet, request
from compute.src.collectors.exchanges import verify_sigs, nep413, b58
from compute.src.collectors.finality import from_actions
from compute.src.collectors.receipts import RPC, archived
from compute.src.collectors.funding import payer_key
from compute.src.collectors.history import ROOT, IDS, utc

OUT = ROOT / 'data/verified/treasury-inflow-origins-2026-11-02'
TREASURY = ROOT / 'data/verified/treasury-rpc-2026-11-02'
PACKETS = OUT / 'tx-batches'
PRIMARY = OUT / 'archival-rpc'
WALLETS = {'buybacks.multisignature.near', '1csfundsadmin.sputnik-dao.near', 'fefundsadmin.sputnik-dao.near'}
PUBLIC = ROOT / 'public/data/near-intents-native-treasury-inflow-origins-2026-09-01-to-2026-10-30.csv'
MANIFEST = OUT/'release-sources.json'

def release_file(path,text,verify_only):
    if verify_only:
        assert path.read_bytes()==text.encode(),path
    else:
        temp=path.with_suffix('.part');temp.write_bytes(text.encode());temp.replace(path)

def ledger():
    saved = json.loads((TREASURY/'receipt-ledger.json').read_text())
    rows = saved['receipts']
    assert saved['receiptStart'] == '2026-09-01' and saved['receiptEnd'] == '2026-10-31'
    assert len(rows) == len({row['id'] for row in rows}) == len({row['transactionHash'] for row in rows}) == 2578
    assert all(row['receiver'] in WALLETS and int(row['amountYocto']) > 0 for row in rows)
    return rows

def treasury_rpc_sources():
    cached = {}
    for path in (ROOT/'data/verified/treasury-outflows-2026-11-02/sources').glob('*.gz'):
        record = json.loads(gzip.decompress(path.read_bytes()))
        query = record.get('request') or {}
        if query.get('method') == 'EXPERIMENTAL_tx_status':
            assert hashlib.sha256(record['responseText'].encode()).hexdigest() == record['responseSha256']
            result = json.loads(record['responseText'])['result']
            assert result['final_execution_status'] == 'FINAL'
            cached[result['transaction']['hash']] = (path, result)
    return cached

def indexed_trees(hashes):
    for path in sorted([*BATCH.glob('*.json.gz'), *PACKETS.glob('*.json.gz')]):
        _, txs = packet(path)
        for tx in txs:
            if tx['transaction']['hash'] in hashes:
                yield path, tx

def fetch():
    PACKETS.mkdir(parents=True, exist_ok=True)
    rows = ledger()
    targets = {row['transactionHash'] for row in rows}
    with archived() as archive:
        available = {Path(n).stem for n in archive.namelist()}
    available.update(path.name.split('.')[0] for path in RPC.glob('*.gz'))
    available.update(treasury_rpc_sources())
    available.update(tx['transaction']['hash'] for _, tx in indexed_trees(targets))
    missing = [row['transactionHash'] for row in sorted(rows, key=lambda r:-int(r['amountYocto'])) if row['transactionHash'] not in available]
    print('Already cached', len(targets & available), 'of',len(targets), 'remaining',len(missing),flush=True)
    for start in range(0,len(missing),20):
        ids = missing[start:start+20]
        query = {'tx_hashes':ids}
        name = hashlib.sha256(json.dumps(query,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        path = PACKETS/(name+'.json.gz')
        if not path.exists():
            raw, headers = request('/v0/transactions',query)
            record = dict(url='https://'+HOST+'/v0/transactions',request=query,retrievedAt=datetime.now(timezone.utc).isoformat(),responseHeaders=headers,sha256=hashlib.sha256(raw).hexdigest(),raw=raw.decode())
            temp=path.with_suffix('.part')
            temp.write_bytes(gzip.compress(json.dumps(record,separators=(',',':')).encode(),mtime=0))
            temp.replace(path)
        req, result = packet(path)
        assert req == ids
        print('Requested',start+len(ids),'of',len(missing),'unreturned',len(ids)-len(result),flush=True)

def index_blocks(selected):
    blocks = {}
    with zipfile.ZipFile(TREASURY/'incoming-indexer-responses.zip') as arch:
        for name in arch.namelist():
            doc=json.loads(arch.read(name))
            assert hashlib.sha256(doc['body'].encode()).hexdigest() == doc['sha256']
            for raw in json.loads(doc['body'])['data']:
                rid=raw['receipt_id']
                if rid not in selected: continue
                r=selected[rid]
                assert raw['transaction_hash']==r['transactionHash'] and raw['receiver_account_id']==r['receiver'] and raw['predecessor_account_id']==r['predecessor']
                assert str(raw['block']['block_timestamp'])==r['timestampNs'] and int(raw['block']['block_height'])==r['block']
                assert raw['outcome']['status'] is True and all(a['action']=='TRANSFER' for a in raw['actions']) and int(raw['actions_agg']['deposit'])==int(r['amountYocto'])
                h=raw['block']['block_hash']
                assert rid not in blocks or h==blocks[rid]
                blocks[rid]=h
    assert blocks.keys()==selected.keys()
    return blocks

def convert_rpc(tx):
    assert tx['final_execution_status']=='FINAL'
    root=tx['transaction_outcome'];body={r['receipt_id']:r for r in tx['receipts']}
    for rid in root['outcome']['receipt_ids']:
        if rid not in body:
            t=tx['transaction']
            assert t['receiver_id']==t['signer_id']
            body[rid]=dict(receipt_id=rid,predecessor_id=t['signer_id'],receiver_id=t['receiver_id'],receipt={'Action':{'actions':t['actions']}})
    return dict(transaction=tx['transaction'],execution_outcome=root,receipts=[dict(receipt=body.get(o['id']),execution_outcome=o) for o in tx['receipts_outcome']])

def source_iterator(txs):
    # Prefer archival JSON-RPC when the complete tree is already preserved.
    left=set(txs)
    def ready(tx,key,src):
        if key in left:
            left.remove(key)
            return key,convert_rpc(tx),src
    with archived() as arch:
        for name in arch.namelist():
            h=Path(name).stem
            if h in left:
                yield ready(json.loads(arch.read(name))['result'],h,'final RPC / '+str(Path(arch.filename).relative_to(ROOT))+'#'+name)
    for h in sorted(left.copy()):
        path=RPC/(h+'.json.gz')
        if path.is_file():
            yield ready(json.loads(gzip.decompress(path.read_bytes()))['result'],h,'final RPC / '+str(path.relative_to(ROOT)))
    for h,(path,rpc) in treasury_rpc_sources().items():
        if h in left: yield ready(rpc,h,'final RPC / '+str(path.relative_to(ROOT)))
    for h in sorted(left.copy()):
        path=PRIMARY/(h+'.json.gz')
        if path.is_file():
            record=json.loads(gzip.decompress(path.read_bytes()))
            assert record['request']['params']['tx_hash']==h and hashlib.sha256(record['responseText'].encode()).hexdigest()==record['sha256']
            yield ready(json.loads(record['responseText'])['result'],h,'final RPC / '+str(path.relative_to(ROOT)))
    for path,tx in indexed_trees(left):
        h=tx['transaction']['hash']
        if h in left:
            left.remove(h)
            yield h,tx,'transaction index / '+str(path.relative_to(ROOT))

def frozen_sources(hashes,manifest):
    bypath=defaultdict(dict)
    for tx,source in manifest['transactions'].items():
        method,filename=source.split(' / ',1)
        file=filename.split('#',1)[0]
        bypath[(method,file)][tx]=filename
    assert set(manifest['transactions'])==set(hashes)
    for relative,sha in manifest['files'].items():
        assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==sha, relative
    for (method,name),targets in bypath.items():
        file=ROOT/name
        if method=='transaction index':
            _,trees=packet(file)
            for tx in trees:
                h=tx['transaction']['hash']
                if h in targets:yield h,tx,manifest['transactions'][h]
        elif file.suffix=='.zip':
            with zipfile.ZipFile(file) as archive:
                for h,reference in targets.items():
                    member=reference.split('#')[1];assert member==h+'.json'
                    yield h,convert_rpc(json.loads(archive.read(member))['result']),manifest['transactions'][h]
        else:
            assert len(targets)==1
            h=next(iter(targets));source=json.loads(gzip.decompress(file.read_bytes()))
            rpc=json.loads(source['responseText']) if 'responseText' in source else source
            yield h,convert_rpc(rpc['result']),manifest['transactions'][h]

def actions(receipt):
    return (receipt or {}).get('receipt',{}).get('Action',{}).get('actions',[])

def action_names(ops):
    names=[]
    for action in ops:
        if 'Delegate' in action:
            names.extend('delegate/'+label for label in action_names(action['Delegate']['delegate_action']['actions']))
        elif 'FunctionCall' in action: names.append(action['FunctionCall']['method_name'])
        else: names.append(next(iter(action)))
    return names

def direct_witness(indexed,collect):
    from compute.src.treasury.intermediary import request_rpc,RPC_HOST
    h=indexed['transaction']['hash'];signer=indexed['transaction']['signer_id']
    q=dict(jsonrpc='2.0',id='treasury-native-inflow-origin',method='EXPERIMENTAL_tx_status',params=dict(tx_hash=h,sender_account_id=signer,wait_until='FINAL'))
    p=PRIMARY/(h+'.json.gz')
    if p.is_file():
        record=json.loads(gzip.decompress(p.read_bytes()));raw=record['responseText'].encode()
        assert record['request']==q and record['url']=='https://'+RPC_HOST and record['sha256']==hashlib.sha256(raw).hexdigest()
    else:
        if not collect:raise FileNotFoundError(p)
        raw=request_rpc(q);record=dict(url='https://'+RPC_HOST,request=q,retrievedAt=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(raw).hexdigest(),responseText=raw.decode())
    result=json.loads(raw)['result']
    assert result['final_execution_status']=='FINAL' and indexed['transaction']==result['transaction']
    rawout={o['id']:o for o in result['receipts_outcome']};bodies={b['receipt_id']:b for b in result['receipts']};checked=set()
    for item in indexed['receipts']:
        o=item['execution_outcome'];body=item['receipt']
        if not o:continue
        direct=rawout[o['id']];assert o['block_hash']==direct['block_hash']
        for key in ('executor_id','status','logs','receipt_ids','tokens_burnt','gas_burnt'):
            assert o['outcome'][key]==direct['outcome'][key]
        if body and o['id'] in bodies:
            assert all(body[k]==bodies[o['id']][k] for k in ('receipt_id','predecessor_id','receiver_id','receipt'))
        checked.add(o['id'])
    assert set(rawout)==checked
    if not p.is_file():
        PRIMARY.mkdir(exist_ok=True)
        tmp=p.with_suffix('.part');tmp.write_bytes(gzip.compress(json.dumps(record,separators=(',',':')).encode(),mtime=0));tmp.replace(p)
    return p

def direct_samples():
    # Extra direct witnesses for the largest withdrawal owners plus every non-verifier sender.
    rows=list(csv.DictReader(PUBLIC.open()))
    additional=sorted([r for r in rows if r['owner_in_original_three']=='false'],key=lambda r:-int(r['amountYocto']))
    selected=[*additional[:6],*(r for r in rows if r['predecessor']!='intents.near' and r['transaction_signer']),
              *(r for r in rows if r['withdrawal_owner'].startswith('c0addac935'))]
    targets={r['transaction_hash'] for r in selected if not r['trace_source'].startswith('final RPC')}
    for _,tx in indexed_trees(targets):
        h=tx['transaction']['hash'];direct_witness(tx,True);targets.remove(h)
        print('Matched direct and indexed complete trees:',h,flush=True)
    assert not targets

def decimal_near(amount):
    with localcontext() as ctx:
        ctx.prec=80
        return format(Decimal(amount).scaleb(-24),'f')

def classify(record, tx, source, native_hash, signatures):
    rid=record['id'];h=record['transactionHash'];quantity=record['amountYocto'];recipient=record['receiver']
    doc=dict(receipt_utc=utc(record['timestampNs']),native_receipt_id=rid,native_block_height=record['block'],native_block_hash=native_hash,
             transaction_hash=h,predecessor=record['predecessor'],treasury_recipient=recipient,native_near=decimal_near(quantity),
             flow_class='unavailable_transaction_tree',withdrawal_owner='',owner_in_original_three='',intent_hash='',verifier_receipt_id='',verifier_block_hash='',
             authority='',signed_intent_actions='',same_intent_signed_wnear_exchange_raw='',same_intent_exchange_inputs_raw='',
             unwrap_receipt_id='',trace_source=source,transaction_signer='',transaction_receiver='',root_actions='',native_transfer_trigger='',
             source_transaction_url='https://nearblocks.io/txns/'+h)
    if tx is None: return doc
    assert tx['transaction']['hash']==tx['execution_outcome']['id']==h
    doc['transaction_signer']=tx['transaction']['signer_id']
    doc['transaction_receiver']=tx['transaction']['receiver_id']
    doc['root_actions']='|'.join(action_names(tx['transaction']['actions']))
    entries={e['execution_outcome']['id']:e for e in tx['receipts'] if e['execution_outcome']}
    body=entries[rid]['receipt'];native=entries[rid]['execution_outcome']
    assert body['receipt_id']==rid and body['receiver_id']==recipient and body['predecessor_id']==record['predecessor']
    assert actions(body) and all(list(a)==['Transfer'] for a in actions(body)) and sum(int(a['Transfer']['deposit']) for a in actions(body))==int(quantity)
    assert native['block_hash']==native_hash and native['outcome']['executor_id']==recipient and any(k.startswith('Success') for k in native['outcome']['status'])
    if 'block_height' in native:
        assert int(native['block_height'])==record['block'] and int(native['block_timestamp'])==int(record['timestampNs'])
    if record['predecessor']!='intents.near':
        doc['flow_class']='within_tracked_group' if record['predecessor'] in WALLETS else 'other_native_transfer'
        if rid in tx['execution_outcome']['outcome']['receipt_ids']:
            doc['native_transfer_trigger']='original transaction actions'
        else:
            parents=[x for x in entries.values() if rid in x['execution_outcome']['outcome']['receipt_ids']]
            assert len(parents)==1
            doc['native_transfer_trigger']='|'.join(action_names(actions(parents[0]['receipt'])))
        return doc
    parent={}
    for e in [tx['execution_outcome'],*(x['execution_outcome'] for x in entries.values())]:
        for generated in e['outcome']['receipt_ids']:
            assert generated not in parent
            parent[generated]=e['id']
    cb=entries[parent[rid]]
    fc=[a['FunctionCall'] for a in actions(cb['receipt']) if 'FunctionCall' in a and a['FunctionCall']['method_name']=='do_native_withdraw']
    if not fc:
        doc['flow_class']='other_verifier_transfer'
        return doc
    assert len(fc)==1 and cb['receipt']['receiver_id']=='intents.near'
    callback=json.loads(base64.b64decode(fc[0]['args']))['withdraw']
    assert callback['receiver_id']==recipient and callback['amount']==quantity
    initiating=entries[parent[cb['execution_outcome']['id']]]
    out=initiating['execution_outcome'];auth=initiating['receipt']
    assert out['outcome']['executor_id']=='intents.near' and any(k.startswith('Success') for k in out['outcome']['status'])
    events=defaultdict(list)
    standards={'native_withdraw':'dip4','transfer':'dip4','token_diff':'dip4','intents_executed':'dip4','mt_burn':'nep245','mt_transfer':'nep245','mt_mint':'nep245'}
    for log in out['outcome']['logs']:
        if not log.startswith('EVENT_JSON:'): continue
        e=json.loads(log[11:])
        if e['event'] in standards:
            assert e['standard']==standards[e['event']]
            events[e['event']].extend(e['data'])
    ws=[d for d in events['native_withdraw'] if d['receiver_id']==recipient and d['amount']==quantity]
    if len(ws)!=1:
        doc['flow_class']='source_not_unique_in_verifier_outcome'
        return doc
    w=ws[0];owner=w['account_id'];ih=w['intent_hash'];wrap='nep141:wrap.near'
    assert any(d['account_id']==owner and d['intent_hash']==ih for d in events['intents_executed'])
    assert len([d for d in events['mt_burn'] if d['owner_id']==owner and d['token_ids']==[wrap] and d['amounts']==[quantity] and d.get('memo')=='withdraw'])==1
    # Both the unwrap and callback must be children of this same verifier execution.
    unwrap=[]
    for child in out['outcome']['receipt_ids']:
        ent=entries.get(child,{})
        rec=ent.get('receipt') or {}
        if rec.get('predecessor_id')!='intents.near' or rec.get('receiver_id')!='wrap.near': continue
        for a in actions(rec):
            call=a.get('FunctionCall',{})
            if call.get('method_name')=='near_withdraw' and json.loads(base64.b64decode(call['args']))['amount']==quantity:
                assert any(k.startswith('Success') for k in ent['execution_outcome']['outcome']['status'])
                refund=str(int(quantity)+int(call['deposit']))
                paid=[entries.get(c,{}) for c in ent['execution_outcome']['outcome']['receipt_ids']]
                assert any(v.get('receipt') and v['receipt']['predecessor_id']=='wrap.near' and v['receipt']['receiver_id']=='intents.near'
                           and actions(v['receipt'])==[{'Transfer':{'deposit':refund}}] and any(k.startswith('Success') for k in v['execution_outcome']['outcome']['status']) for v in paid)
                unwrap.append(child)
    assert len(unwrap)==1,(h,unwrap)
    doc.update(flow_class='verifier_native_withdrawal',withdrawal_owner=owner,owner_in_original_three=str(owner in IDS).lower(),
               intent_hash=ih,verifier_receipt_id=out['id'],verifier_block_hash=out['block_hash'],authority='verifier executed signed intent',unwrap_receipt_id=unwrap[0],native_transfer_trigger='do_native_withdraw')
    signed=from_actions(actions(auth)).get(ih)
    if signed is None: return doc
    assert signed['payload']['recipient']=='intents.near'
    msg=json.loads(signed['payload']['message'])
    assert msg['signer_id']==owner
    assert any((v.get('intent'),v.get('receiver_id'),v.get('amount'))==('native_withdraw',recipient,quantity) for v in msg['intents'])
    key=payer_key(signed);digest=nep413(signed)
    assert b58(digest)==ih
    signatures[ih]=dict(hash=digest.hex(),pk=signed['public_key'],sig=signed['signature'],collector=key)
    doc['authority']='NEP-413 Ed25519 implicit owner' if key==owner else 'NEP-413 Ed25519 verifier-accepted key'
    doc['signed_intent_actions']='|'.join(v['intent'] for v in msg['intents'])
    diffs=[d['diff'] for d in events['token_diff'] if d['account_id']==owner and d['intent_hash']==ih]
    assert diffs==[v['diff'] for v in msg['intents'] if v['intent']=='token_diff']
    usd=Counter();gained=0
    for diff in diffs:
        if int(diff.get(wrap,0))>0 and any(int(v)<0 for k,v in diff.items() if k!=wrap):
            gained+=int(diff[wrap])
            usd.update({k:-int(v) for k,v in diff.items() if int(v)<0})
    if gained:
        credits=Counter();debits=Counter()
        for d in events['mt_transfer']:
            for k,v in zip(d['token_ids'],d['amounts'],strict=True):
                if d['old_owner_id']==owner: debits[k]+=int(v)
                if d['new_owner_id']==owner: credits[k]+=int(v)
        assert credits[wrap]>=gained and all(debits[k]>=v for k,v in usd.items()), (h,'signed acquisition lacks gross settlement')
    doc['same_intent_signed_wnear_exchange_raw']=str(gained)
    doc['same_intent_exchange_inputs_raw']=json.dumps(dict(usd),sort_keys=True,separators=(',',':'))
    return doc

def summarize(rows):
    owners=defaultdict(list);cohorts=defaultdict(list);month=defaultdict(list);byWallet=defaultdict(list)
    for r in rows:
        if r['withdrawal_owner']: owners[r['withdrawal_owner']].append(r)
    for r in rows:
        family=r['flow_class']
        if family=='verifier_native_withdrawal':
            if r['owner_in_original_three']=='true': family='original_three_verifier_owners'
            else: family='additional_verifier_owners'
        cohorts[family].append(r)
        month[(r['receipt_utc'][:7],family)].append(r)
        byWallet[(r['treasury_recipient'],family)].append(r)
    def stats(group):
        amounts=sum(int(r['amountYocto']) for r in group)
        return dict(receipts=len(group),nativeNEAR=decimal_near(amounts))
    sr=sorted(owners,key=lambda o:-sum(int(r['amountYocto']) for r in owners[o]))
    detailed=[dict(withdrawingOwner=o,inOriginalThree=o in IDS,recipients=sorted(set(r['treasury_recipient'] for r in owners[o])),**stats(owners[o])) for o in sr]
    mechanisms=[r for r in rows if int(r['same_intent_signed_wnear_exchange_raw'] or 0)>0]
    instructions=defaultdict(list)
    nonverifier=defaultdict(list)
    for r in rows:
        if r['withdrawal_owner']:
            scope='original_three' if r['owner_in_original_three']=='true' else 'additional'
            instructions[(scope,r['signed_intent_actions'] or 'unsupported signature format')].append(r)
        elif r['flow_class']=='other_native_transfer':
            nonverifier[(r['predecessor'],r['treasury_recipient'],r['native_transfer_trigger'])].append(r)
    return dict(windowStartInclusiveUTC='2026-09-01T00:00:00Z',windowEndExclusiveUTC='2026-10-31T00:00:00Z',
                frozenReceipts=len(rows),grossNEAR=decimal_near(sum(int(r['amountYocto']) for r in rows)),
                externalImmediatePredecessor=stats([r for r in rows if r['predecessor'] not in WALLETS]),
                independentlyVerifiedEd25519Intents=len(set(r['intent_hash'] for r in rows if r['authority'].startswith('NEP-413'))),
                archivalRPCRows=sum(r['trace_source'].startswith('final RPC') for r in rows),
                indexedTreeRows=sum(r['trace_source'].startswith('transaction index') for r in rows),
                coverageByClass={k:stats(v) for k,v in sorted(cohorts.items())},
                matchedMonths=[dict(month=m,classification=k,**stats(v)) for (m,k),v in sorted(month.items())],
                matchedWallets=[dict(wallet=w,classification=k,**stats(v)) for (w,k),v in sorted(byWallet.items())],
                signedInstructionMix=[dict(ownerScope=key[0],instructions=key[1],**stats(rs)) for key,rs in sorted(instructions.items())],
                otherProvenNativeTransfers=[dict(predecessor=key[0],recipient=key[1],trigger=key[2],**stats(rs)) for key,rs in sorted(nonverifier.items())],
                distinctWithdrawalOwners=len(owners),observedOnceOutsideOriginalThree=sum(o not in IDS and len(rs)==1 for o,rs in owners.items()),
                observedOnceOutsideOriginalThreeQuantities=stats([r for o,rs in owners.items() if o not in IDS and len(rs)==1 for r in rs]),
                linkedOriginalServiceRecord=stats([r for r in rows if r['also_in_original_794']=='true']),
                originalThreeOwnerButOutsideServiceRecords=stats([r for r in rows if r['owner_in_original_three']=='true' and r['also_in_original_794']=='false']),
                identifiedSameWithdrawalIntentAcquisitions=dict(receipts=len(mechanisms),wNEAR=decimal_near(sum(int(r['same_intent_signed_wnear_exchange_raw']) for r in mechanisms)),
                     byOwnerScope={group:dict(receipts=len(groupRows),wNEAR=decimal_near(sum(int(r['same_intent_signed_wnear_exchange_raw']) for r in groupRows))) for group in ('original_three','additional') for groupRows in [[r for r in mechanisms if (r['owner_in_original_three']=='true')==(group=='original_three')]]}),
                distinctOwnersAndRecipients=detailed,
                interpretation='Native balances, withdrawal ownership and signed exchange legs are different observations. A signer is not an identified legal entity or proof of independent funding. Quantities on each leg of the same route must not be summed. Transactions outside the original service history are classified from the exact native receipt and both verifier and wrap.near executions. Raw transaction-index matches and direct finalized RPC matches are labeled separately.')

def upstream_cases(records):
    samples=OUT/'largest-withdrawals-summary.json'; recycled=OUT/'intermediary-summary.json'
    refs={}
    if samples.exists():
        sample=json.loads(samples.read_text())
        for r in sample['owners']:
            assert r['wNEARHistoryEntries']==2 and Decimal(r['endingWNEAR'])==0
            credit,=r['creditReceipts'];t,=credit['explicitIncomingTransfers'];assert t['signature']=='NEP-413 Ed25519 implicit owner'
            assert decimal_near(int(t['amountRaw']))==r['nativePayoutNEAR']
            refs[r['nativeWithdrawalTransaction']]=(t['authorizer'],r['nativePayoutNEAR'],credit['transaction'],'complete intermediary wNEAR history and signed incoming transfer')
    if recycled.exists():
        r=json.loads(recycled.read_text())
        refs[r['nativePaymentTransaction']]=(r['transferringTreasury'],r['minimumNativeNEARFromTrackedTreasuryInventory'],r['fundingTransaction'],'tracked treasury inventory; complete interval movements and archival pre/post balances')
    for row in records:
        h=row['transaction_hash']
        for k,v in zip(('verified_upstream_wnear_account','native_near_traced_to_that_upstream_account','upstream_funding_transaction','upstream_basis'),refs.get(h,('','','','')),strict=True):
            row[k]=v
        if h in refs:assert row['native_near']==row['native_near_traced_to_that_upstream_account']

def original_matches(records):
    study=ROOT/'public/data/near-intents-collector-withdrawals-2026-08-23-to-2026-10-28.csv'
    service={r['transaction_hash']:r for r in csv.DictReader(study.open())}
    assert len(service)==794
    for r in records:
        h=r['transaction_hash'];r['also_in_original_794']=str(h in service).lower()
        if h not in service:continue
        earlier=service[h]
        assert earlier['collector_account']==r['withdrawal_owner'] and earlier['treasury_recipient']==r['treasury_recipient']
        assert earlier['receipt_id']==r['native_receipt_id'] and Decimal(earlier['rpc_transfer_near'])==Decimal(r['native_near'])

def page_totals(result,hashes):
    small={k:v for k,v in result.items() if k not in ('distinctOwnersAndRecipients','otherProvenNativeTransfers','interpretation')}
    sample=OUT/'largest-withdrawals-summary.json';recycling=OUT/'intermediary-summary.json'
    if sample.exists():
        source=json.loads(sample.read_text())
        owners={e['authorizer'] for case in source['owners'] for c in case['creditReceipts'] for e in c['explicitIncomingTransfers']}
        assert len(owners)==1
        zeroStartEnd=sum(int(case['openingStock']['wNEARRaw'])==int(case['withdrawalBlockStock']['wNEARRaw'])==0 for case in source['owners'])
        fundingChecks=[c['wholeTransactionCounterassetCheck'] for case in source['owners'] for c in case['creditReceipts']]
        small['sixLargestAdditionalWithdrawals']=dict(withdrawingOwners=len(source['owners']),tracedNativeNEAR=source['totalNativePayoutNEAR'],upstreamAccount=owners.pop(),
                                                     ownersWithZeroOpeningAndFinalWNEAR=zeroStartEnd,independentlyVerifiedUpstreamSignatures=source['signedUpstreamTransfersOrExchangeIntents'],
                                                     upstreamTransactionsWithNoCounterassetOrReciprocalPayment=source['sourceTransactionsWithNoSecondAssetOrReciprocalPayment'],
                                                     exactUpstreamInventoryDebits=sum(int(c['sourceBlockChangeRaw'])==-int(c['signedTransferWNEARRaw']) for c in fundingChecks))
    if recycling.exists():
        s=json.loads(recycling.read_text())
        small['groupInventoryRecycled']=dict(nativeNEAR=s['minimumNativeNEARFromTrackedTreasuryInventory'],remainingWNEARToCollector=s['otherWNEARSentToOriginalCollector'],nativeRecipient=s['nativeTreasuryRecipient'])
    shared=OUT/'shared-source-summary.json'
    if shared.exists():
        source=json.loads(shared.read_text())
        key=str(PUBLIC.relative_to(ROOT))
        if source.get('sourceDigests',{}).get(key)==hashes[key]:
            small['sharedSourceMatches']={k:v for k,v in source.items() if k not in ('scope','sourceDigests')}
            hashes.update(source['sourceDigests'])
    small['sourceDigests']=hashes
    return small

def run(write,freeze,verify):
    rows=ledger();bytx={r['transactionHash']:r for r in rows}
    blocks=index_blocks({r['id']:r for r in rows})
    records={};signatures={}
    manifest=json.loads(MANIFEST.read_text()) if verify else None
    if freeze:
        candidates=list(csv.DictReader(PUBLIC.open()))
        chosen={row['transaction_hash']:row['trace_source'] for row in candidates}
        assert chosen.keys()==bytx.keys() and all(chosen.values()), 'Write the complete 2,578-row selection before freezing it'
        manifest=dict(transactions=chosen,files={})
    trees=frozen_sources(bytx,manifest) if verify or freeze else source_iterator(bytx)
    for h,tx,source in trees:
        assert h not in records
        r=bytx[h];records[h]=classify(r,tx,source,blocks[r['id']],signatures)
    for h,r in bytx.items():
        if h not in records: records[h]=classify(r,None,'',blocks[r['id']],signatures)
    verify_sigs(list(signatures.values()))
    records=[dict(records[r['transactionHash']],amountYocto=r['amountYocto']) for r in rows]
    upstream_cases(records)
    original_matches(records)
    result=summarize(records)
    if freeze or verify:
        assert result['archivalRPCRows']+result['indexedTreeRows']==len(rows)
        assert 'unavailable_transaction_tree' not in result['coverageByClass']
    if freeze:
        sources={r['transaction_hash']:r['trace_source'] for r in records}
        files={src.split(' / ',1)[1].split('#',1)[0] for src in sources.values()}
        for evidence in ('receipt-ledger.json','incoming-indexer-responses.zip'):
            files.add(str((TREASURY/evidence).relative_to(ROOT)))
        files.update(str(p.relative_to(ROOT)) for p in (OUT/'intermediary-summary.json',OUT/'largest-withdrawals-summary.json'))
        files.add('public/data/near-intents-collector-withdrawals-2026-08-23-to-2026-10-28.csv')
        for p in (OUT/'shared-source-summary.json',OUT/'shared-source-timing.json',*sorted((OUT/'intermediary-sources').glob('*.gz')),*sorted((OUT/'large-withdrawal-origins').glob('*.gz')),
                  *sorted((OUT/'shared-source-eligible').glob('*.gz')),*sorted((OUT/'shared-source').glob('*.gz')),*sorted(PRIMARY.glob('*.gz'))):
            files.add(str(p.relative_to(ROOT)))
        intermediary=json.loads((OUT/'intermediary-summary.json').read_text())
        files.update(src.split(' / ',1)[1].split('#',1)[0] for src in intermediary['archivalTransactionSources'])
        files.update(json.loads((OUT/'shared-source-timing.json').read_text()).values())
        manifest=dict(transactions=sources,files={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in sorted(files)})
    if write or verify or freeze:
        stream=io.StringIO(newline='');w=csv.DictWriter(stream,fieldnames=list(records[0]),lineterminator='\n');w.writeheader();w.writerows(records)
        content={PUBLIC:stream.getvalue(),OUT/'summary.json':json.dumps(result,indent=2)+'\n'}
        hashes={str(p.relative_to(ROOT)):hashlib.sha256(txt.encode()).hexdigest() for p,txt in content.items()}
        linked=ROOT/'public/data'
        for p in (linked/'near-intents-treasury-inventory-rerouting-2026-09-09.csv',linked/'near-intents-six-largest-additional-treasury-withdrawals-2026-09-01-to-2026-10-30.csv'):
            hashes[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
        small=page_totals(result,hashes)
        if freeze or verify: assert 'sharedSourceMatches' in small, 'Replay the common-source census against the final native receipt CSV first'
        content[OUT/'page-summary.json']=json.dumps(small,indent=2)+'\n'
        for path,body in content.items():
            if verify:assert path.read_bytes()==body.encode(),path
            else:
                tmp=path.with_suffix('.part');tmp.write_text(body);tmp.replace(path)
        outputHashes={**hashes,**{str(p.relative_to(ROOT)):hashlib.sha256(body.encode()).hexdigest() for p,body in content.items()}}
        if verify:assert manifest['outputs']==outputHashes
        if freeze:
            manifest['outputs']=outputHashes
            tmp=MANIFEST.with_suffix('.part');tmp.write_text(json.dumps(manifest,indent=2)+'\n');tmp.replace(MANIFEST)
    small={k:v for k,v in result.items() if k not in ('distinctOwnersAndRecipients','interpretation','matchedMonths','matchedWallets')}
    print(json.dumps(small,indent=2))

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--collect',action='store_true')
    parser.add_argument('--write',action='store_true')
    parser.add_argument('--freeze',action='store_true')
    parser.add_argument('--verify',action='store_true')
    parser.add_argument('--collect-witnesses',action='store_true')
    args=parser.parse_args()
    if args.collect: fetch()
    elif args.collect_witnesses: direct_samples()
    else: run(args.write,args.freeze,args.verify)
