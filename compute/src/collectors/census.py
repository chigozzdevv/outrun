"""Report the terminal individual >=1-wNEAR credit population and the entire residual sub-1 quantity."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import csv
import io
import json
import gzip
import hashlib
from decimal import Decimal,localcontext
from compute.src.collectors.history import DATA,ROOT,IDS,WRAP,utc
from compute.src.collectors.inventory import complete_chains
from compute.src.collectors.opening import terminal_week_rows
from compute.src.collectors.report import units

def census():
    rows,missing=complete_chains();assert not [r for r in missing if '-credits1-' in r['query']],'At least one incoming threshold cursor is incomplete.'
    bridge=json.loads((DATA/'full-period-wnear-bridge.json').read_text());facts=[]
    crosschecks=0
    for owner in IDS:
        hist=json.loads(gzip.decompress((ROOT/'data/verified/collector-flow-accounting-2026-11-02'/('daily-'+owner+'.json.gz')).read_bytes()))
        assert hashlib.sha256(hist['responseText'].encode()).hexdigest()==hist['responseSha256']
        indexed={r['date']:r for r in json.loads(hist['responseText'])['data']}
        week,_=terminal_week_rows(owner)
        for day in [f'2026-08-{n}' for n in range(23,30)]:
            detail=[r for r in week if utc(r['block_timestamp'])[:10]==day]
            assert all(int(r['amount'])!=0 for r in detail)
            agg=indexed.get(day,{})
            assert int(agg.get('transfers',0))==len(detail),(owner,day,'independent event count')
            assert sum(max(0,int(r['amount'])) for r in detail)==int(agg.get('amount_in',0))
            assert sum(max(0,-int(r['amount'])) for r in detail)==int(agg.get('amount_out',0))
            crosschecks+=1
        sources=[r for r in rows if r['account_id']==owner and r['asset_id']==WRAP];large=[r for r in sources if int(r['amount'])>=10**24]
        gross=next(s for s in bridge['perCollector'] if s['collector']==owner)
        total=int(Decimal(gross['allGrossCreditsWNEAR'])*10**24);observed=sum(int(r['amount']) for r in large)
        row=dict(collector=owner,allGrossCreditsWNEAR=units(total),atLeastOneCreditEvents=len(large),atLeastOneCreditWNEAR=units(observed),
                 subOneCreditWNEAR=units(total-observed),allIndexedTransferEvents=gross['indexedAccountTransferEvents'])
        if not [m for m in missing if m['query'].startswith(owner[:8]+'-debits-')]:
            debits=[r for r in sources if int(r['amount'])<0];assert len(debits)==gross['matchedNativeDeliveryReceipts']
            assert all(int(r['amount'])!=0 for r in sources)
            row['countedPositiveWNEARDebits']=len(debits)
            row['impliedIncomingIndexEvents']=row['allIndexedTransferEvents']-len(debits)
            row['impliedSubOneIndexEvents']=row['impliedIncomingIndexEvents']-len(large)
        facts.append(row)
    total=int(Decimal(bridge['grossReceiptsWNEAR'])*10**24);atleast=sum(int(Decimal(v['atLeastOneCreditWNEAR'])*10**24) for v in facts)
    result=dict(startUTC=bridge['startUTC'],endUTC=bridge['endUTC'],openingBlock=bridge['openingBlock'],closingBlock=bridge['closingBlock'],
                allGrossCreditsWNEAR=units(total),atLeastOneCreditEvents=sum(r['atLeastOneCreditEvents'] for r in facts),atLeastOneCreditWNEAR=units(atleast),
                subOneCreditWNEAR=units(total-atleast),atLeastOneShareOfAllCreditQuantityPct=format(Decimal(atleast)*100/Decimal(total),'.8f'),
                allIndexedTransferEvents=sum(r['allIndexedTransferEvents'] for r in facts),byCollector=facts,
                independentUncensoredAccountDayCountAndAmountCrosschecks=crosschecks,
                scope='Individual credits of at least one wNEAR have complete terminal cursor chains over the entire pinned study. The smaller-credit total equals independently reconciled all-credit gross less those individually enumerated credits. Where sender histories have closed, incoming event counts are calculated from published account transfer-event totals minus explicitly enumerated debit events. The smaller received entries have not been individually enumerated across the entire period.')
    if all('impliedIncomingIndexEvents' in r for r in facts):
        result['impliedIncomingIndexEvents']=sum(r['impliedIncomingIndexEvents'] for r in facts)
        result['impliedSubOneIndexEvents']=sum(r['impliedSubOneIndexEvents'] for r in facts)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--verify',action='store_true');args=p.parse_args()
    with localcontext() as c:c.prec=95;s=census()
    target=DATA/'wnear-credit-census.json';raw=json.dumps(s,indent=2)+'\n'
    rows=[dict(startUTC=s['startUTC'],endUTC=s['endUTC'],**r) for r in s['byCollector']]
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)
    public=ROOT/'public/data/near-intents-collector-wnear-credit-census-2026-08-23-to-2026-10-28.csv'
    for path,contents in ((target,raw),(public,stream.getvalue())):
        if args.verify:assert path.read_text()==contents,path
        else:path.write_text(contents)
    print({k:v for k,v in s.items() if k not in ('scope','byCollector')})
