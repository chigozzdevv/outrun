"""Match public gross verifier wNEAR movements to both pinned inventory states over the entire withdrawal exhibit."""
from compute.src.paths import ROOT, CODE_ROOT
from collections import Counter
import csv
from decimal import Decimal,localcontext
import gzip
import hashlib
import io
import json
from compute.src.collectors.history import ROOT,DATA,IDS,PAGES,WRAP,ORIGINAL_START,ORIGINAL_END,stamp,restore,utc

def daily(owner):
    d=ROOT/'data/verified/collector-flow-accounting-2026-11-02'/('daily-'+owner+'.json.gz')
    archive=json.loads(gzip.decompress(d.read_bytes()));text=archive['responseText'];assert hashlib.sha256(text.encode()).hexdigest()==archive['responseSha256']
    values=json.loads(text)['data']
    assert len(values)<100 or values[-1]['date']<'2026-08-23'
    assert len({v['date'] for v in values})==len(values)
    rows=[v for v in values if '2026-08-23'<=v['date']<'2026-10-28']
    return sum(int(r['amount_in']) for r in rows),sum(int(r['amount_out']) for r in rows),sum(int(r['transfers']) for r in rows),len(rows)

def tail(owner):
    start=stamp('2026-10-28');params=dict(account_id=owner,asset_id=WRAP,from_timestamp_ms=start-1000,to_timestamp_ms=ORIGINAL_END//10**6+1000,desc=False,limit=100)
    rows=[];seen=set();page=0;cursor=None
    while True:
        req,body=restore(PAGES/f'{owner[:8]}-tail-00-{page:04}.json.gz')
        assert req==(params if cursor is None else dict(params,resume_token=cursor))
        for r in body['transfers']:
            assert r['asset_id']==WRAP and r['account_id']==owner
            if not start*10**6<=int(r['block_timestamp'])<=ORIGINAL_END:continue
            idx=(r['block_height'],r['transfer_index']);assert idx not in seen;seen.add(idx);rows.append(r)
        cursor=body.get('resume_token');page+=1
        if not cursor:break
    received=sum(int(r['amount']) for r in rows if int(r['amount'])>0);sent=-sum(int(r['amount']) for r in rows if int(r['amount'])<0)
    return received,sent,len(rows),page

def amount(n):return format(Decimal(n).scaleb(-24),'f')

def derive():
    s={v['tag']:v for v in json.loads((DATA/'boundary-states.json').read_text())}
    a,b=s['Aug23'],s['Oct28']; assert int(a['timestampNs'])==ORIGINAL_START and int(b['timestampNs'])==ORIGINAL_END
    wd=list(csv.DictReader((ROOT/'public/data/near-intents-collector-withdrawals-2026-08-23-to-2026-10-28.csv').open()))
    out=[]
    for owner in IDS:
        opening=int(a['balances'][owner].get('nep141:wrap.near',0));closing=int(b['balances'][owner].get('nep141:wrap.near',0))
        before_in,before_out,before_n,active_days=daily(owner)
        after_in,after_out,after_n,tail_pages=tail(owner)
        received=before_in+after_in;sent=before_out+after_out;residual=closing-opening-received+sent
        assert residual==0,(owner,'gross flow vs canonical inventories',residual)
        matching=[r for r in wd if r['collector_account']==owner]
        native=sum(int(Decimal(r['service_output_near'])*10**24) for r in matching)
        assert 0<=native<=sent,(owner,'matched native redemptions vs gross wrapped debits')
        out.append(dict(collector=owner,openingWNEAR=amount(opening),completeDayCreditsWNEAR=amount(before_in),completeDayDebitsWNEAR=amount(before_out),
          partialDayCreditsWNEAR=amount(after_in),partialDayDebitsWNEAR=amount(after_out),allGrossCreditsWNEAR=amount(received),allGrossDebitsWNEAR=amount(sent),closingWNEAR=amount(closing),
          unexplainedNetWNEAR=amount(residual),indexedAccountTransferEvents=before_n+after_n,matchedNativeDeliveryReceipts=len(matching),
          matchedNativeDeliveriesNEAR=amount(native),grossWNEARDebitsOutside794=amount(sent-native),fullDayIndexRows=active_days,partialDaySourcePages=tail_pages))
    return dict(startUTC=utc(ORIGINAL_START),endUTC=utc(ORIGINAL_END),openingBlock=a['height'],openingBlockHash=a['hash'],closingBlock=b['height'],closingBlockHash=b['hash'],perCollector=out,
           grossReceiptsWNEAR=str(sum(Decimal(r['allGrossCreditsWNEAR']) for r in out)),grossDebitsWNEAR=str(sum(Decimal(r['allGrossDebitsWNEAR']) for r in out)),
           nativeDelivered794NEAR=str(sum(Decimal(r['matchedNativeDeliveriesNEAR']) for r in out)),grossDebitsOutside794WNEAR=str(sum(Decimal(r['grossWNEARDebitsOutside794']) for r in out)),
           rationale='For each collector, 66 complete UTC calendar dates use NearBlocks daily wNEAR quantity aggregates. The final partial calendar day uses a separately terminal-paginated FastNear bidirectional wNEAR transfer index. Their sum is checked against opening and closing verifier balances at canonical hashes. Matching net balances supports quantity accounting and does not authenticate each gross entry. Gross credits include asset exchanges and other transfers. Gross wrapped debits outside matched native withdrawals require outcome-specific attribution.')

if __name__=='__main__':
    with localcontext() as ctx:
        ctx.prec=95;s=derive()
    (DATA/'full-period-wnear-bridge.json').write_text(json.dumps(s,indent=2)+'\n');print(json.dumps(s,indent=2))
    stream=io.StringIO(newline='');records=[dict(startUTC=s['startUTC'],endUTC=s['endUTC'],openingBlock=s['openingBlock'],closingBlock=s['closingBlock'],**r) for r in s['perCollector']]
    writer=csv.DictWriter(stream,fieldnames=list(records[0]),lineterminator='\n');writer.writeheader();writer.writerows(records)
    (ROOT/'public/data/near-intents-collector-wnear-reconciliation-2026-08-23-to-2026-10-28.csv').write_text(stream.getvalue())
