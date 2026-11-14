"""Reproduce the receipt-level collector acquisition exhibit from frozen public records.

The first day is an uncensored, stock-reconciled credit population. The size tiers
are separately queried and terminal-paginated over the full pinned historical window.
Their union is not represented as the complete commercial or acquisition history.
"""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
from collections import Counter,defaultdict
import csv
from decimal import Decimal,localcontext
import gzip
import io
import json

from compute.src.collectors.exchanges import input_units,verify_sigs
from compute.src.collectors.finality import block_proofs,effects
from compute.src.collectors.opening import verify as first_day
from compute.src.collectors.receipts import archived,RPC,verify_effects
from compute.src.collectors.large import load_tier
from compute.src.collectors.history import ROOT,DATA,WRAP,VERIFIER,ORIGINAL_START,ORIGINAL_END,utc

PUBLIC=ROOT/'public/data/near-intents-collector-acquisition-receipts-2026-08-23-to-2026-10-28.csv'
MANIFEST=DATA/'acquisition-release-inputs.json'
SUMMARY=DATA/'acquisition-summary.json'

def units(raw):
    return format(Decimal(raw).scaleb(-24),'f')

def visible_groups(tiers):
    bridge,day=first_day()
    groups={'firstDay':[r for r in day if int(r['amount'])>0]}
    for threshold in tiers:groups['atLeast'+str(threshold)]=load_tier(threshold)
    return bridge,groups

def rowkey(r):
    return r['account_id'],r['block_height'],r['transfer_index']

def unique(rows):
    output={}
    for r in rows:
        k=rowkey(r)
        if k in output:
            assert all(output[k][f]==r[f] for f in ('receipt_id','asset_id','amount','transaction_id','other_account_id')),k
        output[k]=r
    return list(output.values())

def rpc_fallback(rows,proofs):
    needed=defaultdict(list); sigs={}
    for r in rows:
        key=r['account_id'],r['receipt_id']
        if key not in proofs and r['transaction_id']:needed[key].append(r)
    with archived() as z:
        known=set(z.namelist())
        for (owner,rid),records in needed.items():
            tx=records[0]['transaction_id']; assert all(r['transaction_id']==tx for r in records)
            if tx+'.json' in known:raw=z.read(tx+'.json')
            elif (RPC/(tx+'.json.gz')).is_file():raw=gzip.decompress((RPC/(tx+'.json.gz')).read_bytes())
            else:continue
            x=json.loads(raw)['result']; assert x['final_execution_status']=='FINAL' and x['transaction']['hash']==tx
            orig=next((r for r in x['receipts'] if r['receipt_id']==rid),None);out=next(e for e in x['receipts_outcome'] if e['id']==rid)
            if orig is None:
                # A transaction to its own signer may execute the transaction's actions as a local receipt.
                assert x['transaction_outcome']['outcome']['receipt_ids']==[rid] and x['transaction']['receiver_id']==x['transaction']['signer_id']
                orig=dict(receipt_id=rid,receiver_id=x['transaction']['receiver_id'],receipt={'Action':{'actions':x['transaction']['actions']}})
            state=effects({'receipt':orig,'execution_outcome':out,'tx_hash':tx})
            assert state
            _,_,events,signed,balances,moves=state
            data=[m for m in moves if m['owner']==owner]
            actual=Counter((m['asset'],m['origin'] if m['change']>0 else m['target'],m['change']) for m in data)
            indexed=Counter((r['asset_id'][len(VERIFIER):],r['other_account_id'],int(r['amount'])) for r in records)
            assert not indexed-actual,(tx,indexed-actual)
            debits=Counter()
            for m in data:
                if m['asset']!='nep141:wrap.near' and m['change']<0:debits[m['asset']]-=m['change']
            c=dict(collector=owner,tx=tx,stamp=int(records[0]['block_timestamp']),rid=rid,block=int(records[0]['block_height']),w=balances[owner]['nep141:wrap.near'],debits=debits)
            row,sl=verify_effects(c,events,signed);sigs.update({s['hash']:s for s in sl})
            row['canonical_block_hash']=out['block_hash'];row['proof_source']='NEAR archival RPC finalized transaction';proofs[owner,rid]=row
    if sigs:verify_sigs(list(sigs.values()))

def summarize(selected,proofs,original,metadata):
    groups=defaultdict(list)
    for r in selected:groups[r['account_id'],r['receipt_id']].append(r)
    quantity=sum(int(r['amount']) for r in selected)
    receipts={k:proofs[k] for k in groups if k in proofs}
    validated=sum(sum(int(r['amount']) for r in groups[k]) for k in receipts)
    def amount(field):return sum(int(r[field]) for r in receipts.values())
    swaps=[v for v in receipts.values() if int(v['signed_exchange_wnear_raw'])>0]
    fresh=[v for v in swaps if v['tx'] not in original]
    direct=[t for r in receipts.values() for t in json.loads(r['explicit_incoming_transfers'])]
    inputs=defaultdict(lambda:{'receipts':0,'spent':0,'wnear':0,'fees':0})
    for v in swaps:
        used=json.loads(v['input_raw_units'])
        if len(used)!=1 or json.loads(v['bundled_outputs']):continue
        asset,raw=next(iter(used.items()));s=inputs[asset];s['receipts']+=1;s['spent']+=raw;s['wnear']+=int(v['signed_exchange_wnear_raw']);s['fees']+=json.loads(v['exchange_fees_raw_units']).get(asset,0)
    breakdown=[]
    for asset,v in sorted(inputs.items(),key=lambda t:(-t[1]['wnear'],t[0])):
        meta=metadata.get(asset,{})
        qty=lambda n:format(Decimal(n).scaleb(-meta['decimals']),'f') if 'decimals' in meta else None
        breakdown.append(dict(assetId=asset,symbol=meta.get('symbol'),receipts=v['receipts'],spentRaw=str(v['spent']),spentUnits=qty(v['spent']),wNEARReceived=units(v['wnear']),verifierFeeIncludedRaw=str(v['fees']),verifierFeeIncludedUnits=qty(v['fees'])))
    return dict(indexCreditEvents=len(selected),indexCreditReceipts=len(groups),indexGrossWNEAR=units(quantity),receiptVerifiedEvents=sum(len(groups[k]) for k in receipts),
      receiptVerifiedReceipts=len(receipts),receiptVerifiedIndexWNEAR=units(validated),awaitingCanonicalReceipts=len(groups)-len(receipts),awaitingCanonicalIndexWNEAR=units(quantity-validated),
      canonicalWholeReceiptGrossWNEAR=units(amount('observed_wnear_credits_raw')),canonicalWholeReceiptSignedExchangeWNEAR=units(amount('signed_exchange_wnear_raw')),
      canonicalWholeReceiptExplicitNetTransfersWNEAR=units(amount('explicit_net_wnear_transfers_raw')),canonicalWholeReceiptUnattributedNetWNEAR=units(amount('unattributed_net_wnear_raw')),
      directedIncomingTransferWNEAR=units(sum(int(t['amount']) for t in direct)),swapLinkedDirectedIncomingWNEAR=units(sum(int(t['amount']) for t in direct if t['payerSwapInSameIntent'])),
      independentlySignedSwapLinkedIncomingWNEAR=units(sum(int(t['amount']) for t in direct if t['payerSwapInSameIntent'] and t['signatureBinding'].startswith('Ed25519'))),
      directedIncomingTransferEntries=len(direct),distinctSwapLinkedTransferPayers=len({t['account'] for t in direct if t['payerSwapInSameIntent']}),
      verifiedExchangeReceipts=len(swaps),verifiedUniqueCollectorExchangeIntents=len({(v['collector'],h) for v in swaps for h in v['signed_exchange_intents'].split('|')}),
      verifiedExchangeReceiptsOutsideWithdrawal794=len(fresh),verifiedExchangeWNEAROutsideWithdrawal794=units(sum(int(r['signed_exchange_wnear_raw']) for r in fresh)),
      paidInputAssets=len({a for v in swaps for a in json.loads(v['input_raw_units'])}),bundledOutputReceipts=sum(json.loads(v['bundled_outputs'])!=[] for v in swaps),
      standaloneInputBreakdown=breakdown)

def derive(tiers,frozen=None):
    bridge,group_rows=visible_groups(tiers);allrows=unique(r for rows in group_rows.values() for r in rows)
    allowed=set(tuple(x) for x in frozen['keys']) if frozen else None
    chosen=allrows if allowed is None else [r for r in allrows if (r['account_id'],r['receipt_id']) in allowed]
    blockrows=chosen if not frozen else [r for r in chosen if [r['account_id'],r['receipt_id']] in frozen['inBlockArchive']]
    proofs,pres,observed=block_proofs(blockrows)
    blockkeys=[list(k) for k in sorted(proofs)]
    rpc_fallback(chosen,proofs)
    if allowed is not None:assert set(proofs)==allowed,'frozen canonical receipt unavailable'
    with archived() as z:original={s[:-5] for s in z.namelist()}
    metadata=input_units()
    tier_by_key=defaultdict(list)
    for label,rows in group_rows.items():
        for k in {(r['account_id'],r['receipt_id']) for r in rows}:tier_by_key[k].append(label)
    ledger=[]
    for key,v in sorted(proofs.items(),key=lambda p:(p[1]['block_height'],p[0])):
        assert int(v['unattributed_net_wnear_raw'])==0,(key,'unexplained canonical units; investigate before aggregating')
        inputs=json.loads(v['input_raw_units']);fees=json.loads(v['exchange_fees_raw_units'])
        def decorated(raw):
            return [dict(id=k,symbol=metadata[k]['symbol'] if k in metadata else None,raw=str(n),units=format(Decimal(n).scaleb(-metadata[k]['decimals']),'f') if k in metadata else None) for k,n in sorted(raw.items())]
        leg=dict(time_utc=v['time_utc'],collector=v['collector'],transaction_hash=v['tx'],receipt_id=v['receipt_id'],block_height=v['block_height'],block_hash=v['canonical_block_hash'],source=v['proof_source'],
                 population='|'.join(tier_by_key[key]),in_withdrawal_794=str(v['tx'] in original).lower(),ledger_wnear_credited=units(int(v['observed_wnear_credits_raw'])),ledger_wnear_net=units(int(v['ledger_wnear_net_raw'])),signed_exchange_wnear=units(int(v['signed_exchange_wnear_raw'])),
                 explicit_net_transfers_wnear=units(int(v['explicit_net_wnear_transfers_raw'])),other_signed_wnear_change=units(int(v['other_signed_wnear_change_raw'])),mint_minus_burn_wnear=units(int(v['mint_minus_burn_wnear_raw'])),unattributed_net_wnear=units(int(v['unattributed_net_wnear_raw'])),
                 exchange_inputs=json.dumps(decorated(inputs),sort_keys=True,separators=(',',':')),exchange_event_fees=json.dumps(decorated(fees),sort_keys=True,separators=(',',':')),exchange_intents=v['signed_exchange_intents'],additional_bundle_outputs=v['bundled_outputs'],
                 explicit_incoming_transfers=v['explicit_incoming_transfers'],settlement_counterparties_wnear_raw=v['wnear_counterparties_raw'],transaction_url='https://nearblocks.io/txns/'+v['tx'])
        ledger.append(leg)
    totals={k:summarize(rs,proofs,original,metadata) for k,rs in group_rows.items()}
    totals['distinctUnion']=summarize(allrows,proofs,original,metadata)
    prior=json.loads((ROOT/'data/verified/withdrawal-rpc-2026-11-02/conversion-summary.json').read_text())
    flows=json.loads((DATA/'full-period-wnear-bridge.json').read_text())
    old=Decimal(prior['wrappedNEARReceivedInMatchedConversionLegs']);union=totals['distinctUnion']
    new=Decimal(union['verifiedExchangeWNEAROutsideWithdrawal794']);gross=Decimal(flows['grossReceiptsWNEAR'])
    for r in proofs.values():
        assert int(r['signed_exchange_wnear_raw'])<=int(r['observed_wnear_credits_raw']),r['tx']
    combined=dict(originalWithdrawalTreeExchangeReceipts=prior['executedSameIntentConversions'],originalWithdrawalTreeExchangeWNEAR=str(old),
        additionalExchangeReceiptsOutside794=union['verifiedExchangeReceiptsOutsideWithdrawal794'],additionalExchangeWNEAROutside794=str(new),
        minimumIdentifiedExchangeWNEAR=str(old+new),allCollectorGrossInternalWNEARCredits=str(gross),
        minimumIdentifiedExchangeShareOfGrossCreditsPct=format((old+new)*100/gross,'.8f'),
        meaning='This combines the published same-intent exchanges inside the 794 withdrawal trees with separately verified exchange receipts in transactions outside those trees. It measures a verified minimum within total collector wNEAR credits and does not assign the other credits to any economic category.')
    summary=dict(periodStartUTC=utc(ORIGINAL_START),periodEndUTC=utc(ORIGINAL_END),firstDayBalanceBridge=bridge,cohorts=totals,
          verifiedAcquisitionFloorAcrossExhibits=combined,
          scope='The full first UTC day includes every wNEAR credit to all three collectors. Independent full-period threshold cohorts cover individual incoming entries satisfying each threshold, with overlaps removed in distinctUnion. Each verified row binds its receipt ID and ledger settlement to a successful finalized verifier outcome. Signed positive collector token differences are commercial inventory exchanges. Explicit third-party transfers, minting and wrapped-claim redemption are measured separately. Fees are verifier-event token charges; transaction evidence alone does not identify an enterprise payer, external cash, net earnings or ultimate input funding.')
    return summary,ledger,dict(tiers=tiers,keys=[list(k) for k in sorted(proofs)],inBlockArchive=blockkeys)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['preview','publish','verify']);p.add_argument('--tiers',type=int,nargs='+',default=[1000]);args=p.parse_args()
    frozen=json.loads(MANIFEST.read_text()) if args.mode=='verify' else None
    with localcontext() as ctx:
        ctx.prec=90
        summary,rows,manifest=derive(frozen['tiers'] if frozen else args.tiers,frozen)
    print(json.dumps(summary['cohorts'],indent=2))
    stream=io.StringIO(newline='');w=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows)
    results={SUMMARY:json.dumps(summary,indent=2)+'\n',PUBLIC:stream.getvalue(),MANIFEST:json.dumps(manifest,indent=2)+'\n'}
    for threshold in manifest['tiers']:
        cohort=summary['cohorts']['atLeast'+str(threshold)]
        results[DATA/'large-credits'/f'summary-{threshold}.json']=json.dumps({'thresholdWNEAR':threshold,'periodStartUTC':summary['periodStartUTC'],'periodEndUTC':summary['periodEndUTC'],**cohort},indent=2)+'\n'
    if args.mode=='publish':
        for p,data in results.items():p.write_text(data)
    elif args.mode=='verify':
        for p,data in results.items():assert p.read_text()==data,p
