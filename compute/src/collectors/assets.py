"""Infer first-day gross credits for every verifier asset using a complete debit index and two full owner inventories."""
from compute.src.paths import ROOT, CODE_ROOT
from collections import Counter
import csv
import io
import json
from decimal import Decimal,localcontext
from compute.src.collectors.history import DATA,ROOT,IDS,PAGES,VERIFIER,stamp,restore
from compute.src.collectors.exchanges import input_units

def debits(owner):
    reqbase=dict(account_id=owner,from_timestamp_ms=stamp('2026-08-23')-1000,to_timestamp_ms=stamp('2026-08-30')+1000,desc=False,limit=100,direction='sender')
    page=0;cursor=None;records=[];seen=set()
    while True:
        req,body=restore(PAGES/f'{owner[:8]}-debits-00-{page:04}.json.gz')
        assert req==(reqbase if cursor is None else dict(reqbase,resume_token=cursor))
        for r in body['transfers']:
            assert r['account_id']==owner and int(r['amount'])<=0
            if not r['asset_id'].startswith(VERIFIER):continue
            assert r['receipt_account_id']=='intents.near'
            key=(r['block_height'],r['transfer_index']);assert key not in seen;seen.add(key);records.append(r)
        cursor=body.get('resume_token');page+=1
        if not cursor:break
    return records,page

def derive():
    s={v['tag']:v for v in json.loads((DATA/'boundary-states.json').read_text())};a=s['Aug23'];b=s['Aug24']
    meta=input_units();result=[];queries=[]
    for owner in IDS:
        index,pages=debits(owner); day=[r for r in index if a['height']<int(r['block_height'])<=b['height']]
        assert all(int(a['timestampNs'])<int(r['block_timestamp'])<=int(b['timestampNs']) for r in day)
        gross=Counter();counts=Counter()
        for r in day:
            token=r['asset_id'][len(VERIFIER):];gross[token]-=int(r['amount']);counts[token]+=1
        tokens=set(a['balances'][owner])|set(b['balances'][owner])|set(gross)
        queries.append(dict(owner=owner,reportedDebitEvents=len(day),weeklySourcePages=pages))
        for t in sorted(tokens):
            opening=int(a['balances'][owner].get(t,'0'));closing=int(b['balances'][owner].get(t,'0'))
            outgoing=gross[t];incoming=closing-opening+outgoing;assert incoming>=0,(owner,t,incoming)
            if not any((opening,closing,incoming,outgoing)):continue
            def units(v):return format(Decimal(v).scaleb(-meta[t]['decimals']),'f') if t in meta else ''
            result.append(dict(collector=owner,asset_id=t,symbol=meta[t]['symbol'] if t in meta else '',decimals=meta[t]['decimals'] if t in meta else '',opening_raw=opening,derived_gross_credit_raw=incoming,indexed_gross_debit_raw=outgoing,closing_raw=closing,debit_events=counts[t],opening_units=units(opening),derived_gross_credit_units=units(incoming),indexed_gross_debit_units=units(outgoing),closing_units=units(closing)))
    bridge=json.loads((DATA/'first-day-wnear-bridge.json').read_text())
    for w in bridge['rows']:
        x=next(r for r in result if r['collector']==w['collector'] and r['asset_id']=='nep141:wrap.near')
        assert int(w['grossCreditsRaw'])==x['derived_gross_credit_raw'] and int(w['grossDebitsRaw'])==x['indexed_gross_debit_raw']
    return result,queries

if __name__=='__main__':
    with localcontext() as ctx:ctx.prec=95;rows,queries=derive()
    stream=io.StringIO(newline='');w=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows)
    (DATA/'first-day-all-asset-inventory.csv').write_text(stream.getvalue())
    summary=dict(perCollectorSources=queries,collectorAssetRows=len(rows),assetIDs=len({r['asset_id'] for r in rows}),assetsWithGrossCredit=len({r['asset_id'] for r in rows if r['derived_gross_credit_raw']>0}),explanation='Credits for each verifier asset are inferred from closing balance minus opening balance plus the exhaustive bidirectional-account sender-index debit amount, in raw units. For wNEAR, uncensored independent bidirectional event histories cross-check gross credits directly. Other assets do not gain transaction-level payer attribution by using this identity.')
    (DATA/'first-day-all-asset-inventory.json').write_text(json.dumps(summary,indent=2)+'\n');print(summary)
