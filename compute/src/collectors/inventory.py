"""Reconcile each verifier asset over the historical study using terminal debit queries and RPC inventories.

Also compare individual indexed >=1 wNEAR credits with the full wNEAR gross
denominator. Missing cursor chains remain explicit and cannot generate full-period totals.
"""
from compute.src.paths import ROOT, CODE_ROOT
from collections import Counter,defaultdict
import argparse
import csv
from decimal import Decimal,localcontext
import io
import json
from compute.src.collectors.history import ROOT,DATA,PAGES,IDS,VERIFIER,WRAP,queries,restore,ORIGINAL_START,ORIGINAL_END
from compute.src.collectors.opening import terminal_week_rows
from compute.src.collectors.exchanges import input_units

def complete_chains():
    all_rows={};open_chains=[]
    def put(r):
        if not ORIGINAL_START<int(r['block_timestamp'])<=ORIGINAL_END or not r['asset_id'].startswith(VERIFIER):return
        # The public transfer index caps raw amounts at signed Int128 extrema.
        assert -(2**127)<int(r['amount'])<2**127-1,(r['receipt_id'],'ambiguous saturated transfer amount')
        key=(r['account_id'],r['block_height'],r['transfer_index'],r['amount'])
        if key in all_rows:
            assert all(all_rows[key][f]==r[f] for f in ('receipt_id','other_account_id','asset_id','transaction_id','amount'))
        all_rows[key]=r
    for key,params in queries():
        # The independently terminal-paginated, uncensored wNEAR stream already includes every >=1 first-week credit.
        if '-credits1-00' in key:
            source,_=terminal_week_rows(params['account_id'])
            for r in source:
                if int(r['amount'])>=10**24:put(r)
            continue
        cursor=None;page=0
        while True:
            p=PAGES/(f'{key}-{page:04}.json.gz')
            if not p.exists():
                open_chains.append(dict(query=key,nextPage=page));break
            req,body=restore(p)
            assert req==(params if cursor is None else dict(params,resume_token=cursor))
            for r in body['transfers']:
                assert r['account_id']==params['account_id']
                if 'min_amount' in params:assert int(r['amount'])>=10**24 and r['asset_id']==WRAP
                else:assert int(r['amount'])<=0
                put(r)
            cursor=body.get('resume_token');page+=1
            if not cursor:break
    return list(all_rows.values()),open_chains

def derive():
    from compute.src.collectors.metadata import verified_units
    rows,missing=complete_chains();state={s['tag']:s for s in json.loads((DATA/'boundary-states.json').read_text())}
    a,b=state['Aug23'],state['Oct28'];wnea=json.loads((DATA/'full-period-wnear-bridge.json').read_text());meta=input_units()
    supplement=verified_units();assert not set(supplement)&set(meta);meta.update(supplement)
    report=dict(sourceRowsAvailable=len(rows),incompleteQueries=missing,completeForAllCollectors=not missing,collectorResult=[])
    output=[]
    for owner in IDS:
        lacking=[r['query'] for r in missing if r['query'].startswith(owner[:8])]
        debits=Counter();credits_w=0;credit_n=0;debit_n=Counter()
        for r in rows:
            if r['account_id']!=owner:continue
            asset=r['asset_id'][len(VERIFIER):];n=int(r['amount'])
            if n<0:debits[asset]-=n;debit_n[asset]+=1
            elif r['asset_id']==WRAP and n>=10**24:credits_w+=n;credit_n+=1
        incomplete_debit=any('debits' in k for k in lacking);incomplete_credits=any('credits1' in k for k in lacking)
        result=dict(owner=owner,fullDebitSource=not incomplete_debit,fullOneWNEARCreditSource=not incomplete_credits,
                    availableOneWNEARCreditEvents=credit_n,availableOneWNEARCreditRaw=str(credits_w))
        gross=next(x for x in wnea['perCollector'] if x['collector']==owner)
        total=int(Decimal(gross['allGrossCreditsWNEAR'])*10**24);assert credits_w<=total
        result['totalWNEARCreditRaw']=str(total)
        if not incomplete_credits:result['subOneWNEARCreditRaw']=str(total-credits_w)
        if not incomplete_debit:
            assert debits['nep141:wrap.near']==int(Decimal(gross['allGrossDebitsWNEAR'])*10**24)
            for t in sorted(set(a['balances'][owner])|set(b['balances'][owner])|set(debits)):
                opening=int(a['balances'][owner].get(t,0));closing=int(b['balances'][owner].get(t,0));sent=debits[t];received=closing-opening+sent
                assert received>=0,(owner,t,received)
                if not any((opening,closing,sent,received)):continue
                def units(n):return format(Decimal(n).scaleb(-meta[t]['decimals']),'f') if t in meta else ''
                output.append(dict(openingBlock=a['height'],openingBlockHash=a['hash'],closingBlock=b['height'],closingBlockHash=b['hash'],owner=owner,token=t,symbol=meta[t]['symbol'] if t in meta else '',decimals=meta[t]['decimals'] if t in meta else '',unitMetadataSource='closing-block token contract ft_metadata' if t in supplement else 'archived 1Click supported-token catalogue' if t in meta else '',openingRaw=str(opening),derivedCreditsRaw=str(received),indexedDebitsRaw=str(sent),
                     closingRaw=str(closing),outgoingEvents=debit_n[t],openingUnits=units(opening),derivedCreditUnits=units(received),indexedDebitUnits=units(sent),closingUnits=units(closing)))
        report['collectorResult'].append(result)
    ids={r['token'] for r in output}
    report.update(collectorAssetRows=len(output),distinctAssetIDs=len(ids),assetsWithReadableUnits=sum(k in meta for k in ids),
                  archivalTokenMetadataAssets=len(ids&set(supplement)),referenceCatalogAssets=len((ids&set(meta))-set(supplement)),
                  publicCsv='/data/near-intents-collector-asset-inventory-2026-08-23-to-2026-10-28.csv',
                  methodology='Full inventories and independently pinned verifier balances bound both ends. Incoming units for each asset are derived from closing minus opening plus exhaustively indexed outgoing units. Denominations use the preserved token catalogue and supplemental historical NEP-148 contract metadata. Incoming units are inventory movements and do not identify a fee payer or establish cash earnings.')
    return report,output

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--verify',action='store_true');options=parser.parse_args()
    with localcontext() as ctx:ctx.prec=95;summary,rows=derive()
    if options.verify:assert summary['completeForAllCollectors'],summary['incompleteQueries']
    if summary['completeForAllCollectors']:
        stream=io.StringIO(newline='');w=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows)
        files={DATA/'full-period-all-asset-inventory.csv':stream.getvalue(),ROOT/'public'/summary['publicCsv'].removeprefix('/'):stream.getvalue(),DATA/'full-period-all-asset-summary.json':json.dumps(summary,indent=2)+'\n'}
        for path,body in files.items():
            if options.verify:assert path.read_text()==body,path
            else:path.write_text(body)
    print(json.dumps(summary,indent=2))
