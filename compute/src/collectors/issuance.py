"""Compare traced collector acquisition/disposition bounds with actual gross native issuance over identical blocks."""
from compute.src.paths import ROOT, CODE_ROOT
from collections import Counter,defaultdict
import csv
from decimal import Decimal,localcontext
import gzip
import hashlib
import json
from zipfile import ZipFile
from compute.src.collectors.history import ROOT,DATA,IDS
from compute.src.collectors.report import units
from compute.src.collectors.receipts import archived

def minting(bridge):
    prefix='research/institutional-2026-10-28/'
    manifest={x['path']:x['sha256'] for x in json.loads((ROOT/'data/provenance/manifest.json').read_text())['files']}
    history=json.loads((ROOT/'data/verified/supply-ledger.json').read_text());assert history['status']=='epoch-boundaries-complete' and not history['error']
    ledger=history['rows']
    with ZipFile(ROOT/'data/provenance/source-data.zip') as archive:
        doc_cache={}
        def document(path):
            if path not in doc_cache:
                raw=archive.read(path);assert hashlib.sha256(raw).hexdigest()==manifest[path]
                data=json.loads(raw);doc_cache[path]=data.get('result',data)
            return doc_cache[path]
        opening=document(prefix+'supply/raw/neardata-headers-'+str(bridge['openingBlock'])+'.json')['header']
        closing=document(prefix+'revenue/raw/snapshot-block.json')['header']
        assert (opening['height'],opening['hash'])==(bridge['openingBlock'],bridge['openingBlockHash']) and (closing['height'],closing['hash'])==(bridge['closingBlock'],bridge['closingBlockHash'])
        assert ledger[0]['height']<=opening['height']<ledger[1]['height'] and ledger[-1]['height']<=closing['height']
        assert opening['epoch_id']==ledger[0]['epochId'] and closing['epoch_id']==ledger[-1]['epochId']
        included=[];previous=None
        for r in ledger:
            body=document(r['firstBlockFile']);first=body['header'];before=document(r['predecessorFile'])['header']
            assert first['hash']==r['hash'] and first['prev_hash']==before['hash'] and first['height']==r['height'] and first['epoch_id']!=before['epoch_id']
            if previous:
                p,b=previous
                assert first['epoch_id']==b['hash'] and before['epoch_id']==p['epoch_id'] and first['height']>p['height']
            previous=(first,before)
            burnt=sum(int(c['balance_burnt']) for c in body['chunks'] if int(c['height_included'])==first['height'])
            minted=int(first['total_supply'])-int(before['total_supply'])+burnt
            assert minted==int(r['grossMintedYocto'])
            if opening['height']<first['height']<=closing['height']:included.append((r,minted))
    gross=sum(r[1] for r in included);net=int(closing['total_supply'])-int(opening['total_supply'])
    return dict(payouts=len(included),firstPayoutUTC=included[0][0]['timeUTC'],lastPayoutUTC=included[-1][0]['timeUTC'],grossMintedRaw=str(gross),grossMintedNEAR=units(gross),
                endpointNetSupplyChangeRaw=str(net),impliedNativeBurnsRaw=str(gross-net),openingEpochId=opening['epoch_id'],closingEpochId=closing['epoch_id'])

def calculate(batch_receipts=None):
    b=json.loads((DATA/'full-period-wnear-bridge.json').read_text());m=minting(b);gross=int(m['grossMintedRaw'])
    prior=list(csv.DictReader((ROOT/'public/data/near-intents-collector-conversions-2026-08-23-to-2026-10-28.csv').open()))
    assert len(prior)==len({r['transaction_hash'] for r in prior})==205
    base=Counter()
    for r in prior:base[r['collector_account']]+=int(Decimal(r['wrapped_near_received'])*10**24)
    with archived() as archive:old={n[:-5] for n in archive.namelist()}
    assert len(old)==794 and all(r['transaction_hash'] in old for r in prior)
    deliveries=defaultdict(Counter)
    for r in csv.DictReader((ROOT/'public/data/near-intents-collector-withdrawals-2026-08-23-to-2026-10-28.csv').open()):
        assert r['rpc_check']=='verified' and r['rpc_receipt_status'].startswith('Success')
        deliveries[r['collector_account']][r['treasury_recipient']]+=int(Decimal(r['rpc_transfer_near'])*10**24)
    raw_rows=list(csv.DictReader(gzip.open(batch_receipts or DATA/'batch-raw-receipt-classification.csv.gz','rt')))
    direct=list(csv.DictReader((ROOT/'public/data/near-intents-collector-acquisition-receipts-2026-08-23-to-2026-10-28.csv').open()))
    tier100=list(csv.DictReader((ROOT/'public/data/near-intents-collector-indexed-acquisition-receipts-2026-08-23-to-2026-10-28.csv').open()))
    cohorts={'primaryFirstDayAnd250':[(r['collector'],r['transaction_hash'],r['receipt_id'],int(Decimal(r['signed_exchange_wnear'])*10**24)) for r in direct],
             'fixedRaw100':[(r['collector_account'],r['transaction_hash'],r['receipt_id'],int(Decimal(r['signed_collector_exchange_wnear'])*10**24)) for r in tier100],
             'expandingRawCheckpoint':[(r['collector'],r['tx'],r['receipt_id'],int(r['signed_exchange_wnear_raw'])) for r in raw_rows]}
    output={}
    def pct(n,d):return format(Decimal(n)*100/Decimal(d),'.8f')
    for cohort,entries in cohorts.items():
        known=Counter(base);seen=set();new=0
        for owner,tx,rid,n in entries:
            assert (owner,rid) not in seen;seen.add((owner,rid))
            if tx not in old and n>0:known[owner]+=n;new+=1
        rows=[]
        beneficiaries=Counter()
        for s in b['perCollector']:
            owner=s['collector'];closing=int(Decimal(s['closingWNEAR'])*10**24);delivered=int(Decimal(s['matchedNativeDeliveriesNEAR'])*10**24)
            assert Decimal(s['grossWNEARDebitsOutside794'])==0 and Decimal(s['unexplainedNetWNEAR'])==0
            minimum=max(0,known[owner]-closing)
            assert minimum<=delivered
            assert sum(deliveries[owner].values())==delivered
            if len(deliveries[owner])==1:beneficiaries[next(iter(deliveries[owner]))]+=minimum
            rows.append(dict(collector=owner,verifiedRecipients={k:units(n) for k,n in sorted(deliveries[owner].items())},identifiedAcquiredWNEAR=units(known[owner]),closingWNEAR=units(closing),minimumIdentifiedAcquisitionsDeliveredNEAR=units(minimum),allVerifiedNativeDeliveriesNEAR=units(delivered)))
        acquisitions=sum(known.values());minimum=sum(int(Decimal(r['minimumIdentifiedAcquisitionsDeliveredNEAR'])*10**24) for r in rows)
        output[cohort]=dict(originalPlusNewExchangeReceipts=len(prior)+new,identifiedAcquiredWNEAR=units(acquisitions),minimumIdentifiedAcquisitionsDeliveredNEAR=units(minimum),
                            minimumDeliveredShareOfMatchedGrossMintingPct=pct(minimum,gross),recipientSpecificLowerBoundsNEAR={k:units(v) for k,v in sorted(beneficiaries.items())},perCollector=rows)
    return dict(startUTC=b['startUTC'],endUTC=b['endUTC'],openingBlock=b['openingBlock'],openingBlockHash=b['openingBlockHash'],closingBlock=b['closingBlock'],closingBlockHash=b['closingBlockHash'],issuance=m,
                allVerifiedNativeDeliveriesNEAR=b['nativeDelivered794NEAR'],allNativeDeliveriesRelativeToMatchedMintingPct=pct(int(Decimal(b['nativeDelivered794NEAR'])*10**24),gross),cohorts=output,
                method='Every wNEAR debit in this exact historical interval matches one of the 794 successful native treasury payments by amount per collector. For each collector, assign its entire closing wNEAR inventory to observed acquisitions. The remainder of those acquisitions must have left in the matched native treasury deliveries, regardless of lot selection. The aggregate is a lower bound on token quantity bought in verified asset exchanges and delivered to custody. Issuance uses consecutive epoch boundaries between the same finalized blocks. This compares quantities and does not measure verified externally earned cash, a native burn, net market demand or a reduction in supply.')
if __name__=='__main__':
    with localcontext() as c:c.prec=100;result=calculate()
    (DATA/'acquisition-issuance-reconciliation.json').write_text(json.dumps(result,indent=2)+'\n')
    print('matched issuance',result['issuance']['payouts'],result['issuance']['grossMintedNEAR'],'all delivered share',result['allNativeDeliveriesRelativeToMatchedMintingPct'])
    for key,r in result['cohorts'].items():print(key,r['identifiedAcquiredWNEAR'],r['minimumIdentifiedAcquisitionsDeliveredNEAR'],r['minimumDeliveredShareOfMatchedGrossMintingPct'])
