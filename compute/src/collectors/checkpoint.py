"""Freeze a reproducible, transaction-level acquisition checkpoint and its matched-window issuance bound."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
from collections import Counter,defaultdict
import csv
from decimal import Decimal,localcontext
import gzip
import hashlib
import io
import json
from pathlib import Path
from compute.src.collectors.validation import run as batch_release
from compute.src.collectors.receipts import archived
from compute.src.collectors.report import units
from compute.src.collectors.exchanges import collect,verify_sigs,input_units
from compute.src.collectors.history import ROOT,DATA

EDITION=DATA/'expanded-checkpoint-2026-11-02'
PUBLIC=ROOT/'public/data/near-intents-collector-acquisition-checkpoint-2026-11-02.csv'
SUMMARY=EDITION/'published-summary.json'

def materialize_assets(encoded,metadata):
    entries=[]
    for token,n in sorted(json.loads(encoded).items()):
        m=metadata.get(token,{});entry=dict(asset=token,symbol=m.get('symbol'),raw=str(n),units=format(Decimal(n).scaleb(-m['decimals']),'f') if 'decimals' in m else None)
        if EDITION.name.startswith('expanded-priority-'):
            entry['decimals']=m.get('decimals')
            entry['unitSource']='closing-block token contract ft_metadata' if 'metadataSpec' in m else 'archived 1Click supported-token catalogue' if 'decimals' in m else None
        entries.append(entry)
    return json.dumps(entries,separators=(',',':'))

def prior_receipts(meta):
    legs=list(csv.DictReader((ROOT/'public/data/near-intents-collector-conversions-2026-08-23-to-2026-10-28.csv').open()))
    native={r['transaction_hash']:r for r in csv.DictReader((ROOT/'public/data/near-intents-collector-withdrawals-2026-08-23-to-2026-10-28.csv').open())}
    mt=defaultdict(int);receipts={}
    for r in csv.DictReader(gzip.open(DATA/'selected-794-mt-events.csv.gz','rt')):
        if r['token_id']=='nep141:wrap.near' and int(r['change_raw'])>0:mt[r['account_id'],r['receipt']]+=int(r['change_raw'])
    for r in csv.DictReader(gzip.open(DATA/'selected-794-intent-events.csv.gz','rt')):
        if r['event']!='token_diff':continue
        d=json.loads(r['event_data'])
        if int(d['diff'].get('nep141:wrap.near',0))>0:
            key=(r['tx'],d['account_id'],d['intent_hash'])
            assert key not in receipts or receipts[key]==r['receipt'];receipts[key]=r['receipt']
    allrecords={};signatures=[]
    with archived() as ar:
        for r in legs:
            tx=r['transaction_hash'];owner=r['collector_account'];raw=ar.read(tx+'.json')
            sig,leg=collect(native[tx],raw);signatures.append(sig)
            for k in ('intent_hash','collector_account','wrapped_near_received','other_net_wrapped_near_transfers','input_raw_units','exchange_event_fees_collected_raw_units'):
                assert leg[k]==r[k]
            rid=receipts[tx,owner,r['intent_hash']];k=(owner,rid);assert k not in allrecords
            x=json.loads(raw)['result'];out=next(o for o in x['receipts_outcome'] if o['id']==rid)
            acquired=int(Decimal(r['wrapped_near_received'])*10**24);directed=int(Decimal(r['other_net_wrapped_near_transfers'])*10**24)
            assert mt[k]==acquired+directed and acquired>=0 and directed>=0
            allrecords[k]=dict(tx=tx,collector=owner,receipt_id=rid,block_hash=out['block_hash'],time_utc='',withdrawal_observed_utc=r['withdrawal_utc'],gross=mt[k],acquired=acquired,directed=directed,
                  source='Direct NEAR archival RPC, original 794-withdrawal exhibit',rpc=True,block=False,signed_intents=r['intent_hash'],inputs=materialize_assets(r['input_raw_units'],meta),
                  fees=materialize_assets(r['exchange_event_fees_collected_raw_units'],meta),original=True,payer_links='[]')
    verify_sigs(signatures);assert len(allrecords)==205 and set(mt)==set(allrecords)
    return allrecords,native

def derive():
    report=json.loads((EDITION/'batch-acquisition-summary.json').read_text());issuance=json.loads((EDITION/'acquisition-issuance-reconciliation.json').read_text())
    full=json.loads((DATA/'full-period-wnear-bridge.json').read_text());meta=input_units()
    if EDITION.name.startswith('expanded-priority-'):
        from compute.src.collectors.metadata import verified_units
        meta.update(verified_units())
    prior,native=prior_receipts(meta)
    combined=dict(prior);seen=set();unclassified_inside=0
    infile=EDITION/'batch-raw-receipt-classification.csv.gz'
    for r in csv.DictReader(gzip.open(infile,'rt')):
        key=(r['collector'],r['receipt_id']);assert key not in seen;seen.add(key)
        gross=int(r['observed_wnear_credits_raw']);swap=int(r['signed_exchange_wnear_raw']);payments=json.loads(r['explicit_incoming_transfers'])
        directed=sum(int(p['amount']) for p in payments);remainder=gross-swap-directed
        assert remainder>=0 and int(r['unattributed_net_wnear_raw'])==0,(r['tx'],'ambiguous gross credit allocation')
        unclassified_inside+=remainder
        if key in prior:
            d=prior[key];assert (d['tx'],d['block_hash'],d['gross'],d['acquired'],d['directed'])==(r['tx'],r['block_hash'],gross,swap,directed)
        d=dict(tx=r['tx'],collector=r['collector'],receipt_id=r['receipt_id'],block_hash=r['block_hash'],time_utc=r['time_utc'],withdrawal_observed_utc=native[r['tx']]['withdrawal_utc'] if r['tx'] in native else '',
               gross=gross,acquired=swap,directed=directed,source='FastNear Explorer raw transaction and receipt index',rpc=r['independently_matched_archival_rpc']=='true',
               block=r['independently_matched_finalized_block_receipt']=='true',signed_intents=r['signed_exchange_intents'],inputs=materialize_assets(r['input_raw_units'],meta),
               fees=materialize_assets(r['exchange_fees_raw_units'],meta),original=r['tx'] in native,payer_links=json.dumps([dict(payer=p['account'],raw=p['amount'],intent=p['intentHash'],swapInSameIntent=p['payerSwapInSameIntent'],signature=p['signatureBinding']) for p in payments],separators=(',',':')))
        combined[key]=d
    identified=sum(r['acquired'] for r in combined.values());directed=sum(r['directed'] for r in combined.values());gross_seen=sum(r['gross'] for r in combined.values())
    denom=int(Decimal(full['grossReceiptsWNEAR'])*10**24);outside=denom-gross_seen;unclassified=denom-identified-directed
    assert outside>=0 and unclassified==outside+unclassified_inside
    bound=issuance['cohorts']['expandingRawCheckpoint'];assert Decimal(bound['identifiedAcquiredWNEAR'])*10**24==identified
    output=[]
    for r in sorted(combined.values(),key=lambda r:(r['time_utc'] or r['withdrawal_observed_utc'],r['collector'],r['receipt_id'])):
        scope='collector exchange and payer transfer' if r['acquired'] and r['directed'] else 'collector exchange' if r['acquired'] else 'payer transfer' if r['directed'] else 'unclassified'
        output.append(dict(collector_account=r['collector'],transaction_hash=r['tx'],receipt_id=r['receipt_id'],receipt_block_hash=r['block_hash'],indexed_receipt_utc=r['time_utc'],withdrawal_service_utc_if_applicable=r['withdrawal_observed_utc'],
              gross_wnear_credit=units(r['gross']),signed_exchange_wnear=units(r['acquired']),other_directed_transfer_wnear=units(r['directed']),unclassified_credit_wnear=units(r['gross']-r['acquired']-r['directed']),observed_type=scope,
              signed_exchange_intents=r['signed_intents'],inputs=r['inputs'],verifier_fees_included=r['fees'],payer_transfer_details=r['payer_links'],
              record_source=r['source'],direct_archival_rpc_evidence=str(r['rpc']).lower(),independent_finalized_block_evidence=str(r['block']).lower(),original_794_transaction=str(r['original']).lower(),transaction_url='https://nearblocks.io/txns/'+r['tx']))
    buf=io.StringIO(newline='');w=csv.DictWriter(buf,fieldnames=list(output[0]),lineterminator='\n');w.writeheader();w.writerows(output);public_text=buf.getvalue()
    minted=issuance['issuance'];frozen_manifest=json.loads((EDITION/'batch-classification-release-inputs.json').read_text())
    packet_times=[json.loads(gzip.decompress((DATA/'tx-batches'/p).read_bytes()))['retrievedAt'] for p in frozen_manifest['rawTransactionPackets']]
    def pct(n,d):return format(Decimal(n)*100/Decimal(d),'.8f')
    summary=dict(edition='2026-11-02',lastIncludedBatchCaptureUTC=max(packet_times),periodStartUTC=full['startUTC'],periodEndUTC=full['endUTC'],openingBlock=full['openingBlock'],openingBlockHash=full['openingBlockHash'],closingBlock=full['closingBlock'],closingBlockHash=full['closingBlockHash'],
          indexedTransactionTrees=report['distinctReturnedTransactionTrees'],indexedTreesComparedToDirectRPC=report['returnedTreesIndependentlyComparedWithFinalizedRPC'],combinedCreditedReceipts=len(combined),
          original794SourceOnlyCreditedReceipts=len(set(prior)-seen),combinedReceiptsWithDirectRPCOrFinalizedBlockEvidence=sum(r['rpc'] or r['block'] for r in combined.values()),
          signedCollectorExchangeReceipts=sum(r['acquired']>0 for r in combined.values()),signedCollectorAcquisitionsWNEAR=units(identified),otherDirectedTransfersWNEAR=units(directed),
          observedRawGrossWNEAR=units(gross_seen),classifiedGrossWNEAR=units(identified+directed),allCollectorGrossWNEARCredits=units(denom),remainingUnclassifiedGrossWNEAR=units(unclassified),
          grossWNEAROutsideIncludedReceipts=units(outside),unclassifiedWNEARInsideIncludedReceipts=units(unclassified_inside),classifiedShareOfGrossCreditsPct=pct(identified+directed,denom),acquisitionShareOfGrossCreditsPct=pct(identified,denom),
          minimumExchangeAcquiredNativeNEARDelivered=bound['minimumIdentifiedAcquisitionsDeliveredNEAR'],recipientSpecificMinimumNEAR=bound['recipientSpecificLowerBoundsNEAR'],allVerifiedNativeDeliveriesNEAR=full['nativeDelivered794NEAR'],matchedEpochPayouts=minted['payouts'],
          matchedGrossNativeIssuanceNEAR=minted['grossMintedNEAR'],minimumDeliveredShareOfGrossMintingPct=bound['minimumDeliveredShareOfMatchedGrossMintingPct'],allThreeCollectorDeliveriesShareOfGrossMintingPct=issuance['allNativeDeliveriesRelativeToMatchedMintingPct'],
          publicCsv='/data/'+PUBLIC.name,publicCsvSha256=hashlib.sha256(public_text.encode()).hexdigest(),
          scope='Counts include only independently signed collector exchanges or executed directed transfers in the frozen receipt union. Direct archival evidence supplements the raw historical Explorer index. All three collectors’ indexed gross wNEAR debits match the successful native deliveries. Closing inventory is assigned entirely to identified acquisitions before reporting a recipient-specific lower bound. Unclassified gross credits remain a quantity, with no assumed funding source or economic type. Neither gross exchanges nor treasury custody establish external cash earnings, incremental market demand or a native burn.')
    if EDITION.name.startswith('expanded-priority-'):
        from compute.src.collectors.priority import CUTOFF,OUT
        from compute.src.collectors.inventory import complete_chains
        priority={r['transaction_hash']:int(r['qualifying_wnear_credit_raw']) for r in csv.DictReader(OUT.open())}
        assert len(priority)==2000
        eligible,missing=complete_chains();assert not any('-credits1-' in m['query'] for m in missing)
        check=Counter();ids=defaultdict(set)
        for r in eligible:
            if r['transaction_id'] in priority and r['asset_id']=='nep245:intents.near:nep141:wrap.near' and int(r['amount'])>=10**24:
                check[r['transaction_id']]+=int(r['amount']);ids[r['transaction_id']].add((r['account_id'],r['receipt_id']))
        assert dict(check)==priority
        priority_keys=set().union(*ids.values());assert priority_keys<=seen
        selected=[combined[k] for k in priority_keys]
        used={i['asset'] for r in combined.values() for i in json.loads(r['inputs'])}
        summary['exchangeInputAssetIDs']=len(used)
        summary['exchangeInputAssetIDsWithVerifiedUnitPrecision']=sum(k in meta and 'decimals' in meta[k] for k in used)
        summary['exchangeInputAssetIDsWithoutUnitPrecision']=sorted(k for k in used if k not in meta or 'decimals' not in meta[k])
        summary['priorityCohort']=dict(referenceTimeUTC=CUTOFF.isoformat(),transactions=len(priority),qualifyingCreditWNEAR=units(sum(priority.values())),
            distinctCreditedReceipts=len(priority_keys),wholeReceiptGrossWNEAR=units(sum(r['gross'] for r in selected)),
            signedExchangeWNEAR=units(sum(r['acquired'] for r in selected)),otherDirectedWNEAR=units(sum(r['directed'] for r in selected)),
            scope='These are the 2,000 eligible previously unseen transaction hashes with the largest aggregate individual credits of at least one wNEAR at the declared reference time. The rest of the frozen release also includes qualifying evidence captured independently.')
    return public_text,summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['publish','verify']);p.add_argument('--priority-2000',action='store_true');args=p.parse_args()
    if args.priority_2000:
        EDITION=DATA/'expanded-priority-checkpoint-2026-11-02';PUBLIC=ROOT/'public/data/near-intents-collector-acquisition-priority-checkpoint-2026-11-02.csv';SUMMARY=EDITION/'published-summary.json'
    with localcontext() as c:
        c.prec=95
        if not (EDITION/'batch-classification-release-inputs.json').exists():
            assert args.mode=='publish'
            if args.priority_2000:
                from compute.src.collectors.priority import snapshot
                progress=snapshot();assert progress['remainingTrees']==0,progress
            batch_release(True,release_dir=EDITION)
        batch_release(False,verify=True,release_dir=EDITION)
        csv_text,summary=derive()
    results={PUBLIC:csv_text,SUMMARY:json.dumps(summary,indent=2)+'\n'}
    for path,value in results.items():
        if args.mode=='verify':assert path.read_text()==value,path
        else:path.write_text(value)
    print(json.dumps(summary,indent=2))
