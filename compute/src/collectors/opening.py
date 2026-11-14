"""Tie every first-day verifier wNEAR index entry to the change between finalized opening and closing inventory."""
from compute.src.paths import ROOT, CODE_ROOT
from collections import Counter
import csv
import json
from compute.src.collectors.history import DATA,IDS,PAGES,VERIFIER,WRAP,restore,stamp,utc

def terminal_week_rows(owner):
    base=dict(account_id=owner,from_timestamp_ms=stamp('2026-08-23')-1000,to_timestamp_ms=stamp('2026-08-30')+1000,desc=False,limit=100,asset_id=WRAP)
    cursor=None;rows=[]; cursors=set(); pages=0
    while True:
        request,body=restore(PAGES/f'{owner[:8]}-wrap-00-{pages:04}.json.gz')
        assert request==(base if cursor is None else dict(base,resume_token=cursor))
        assert all(r['asset_id']==WRAP and r['account_id']==owner for r in body['transfers'])
        rows.extend(body['transfers']);pages+=1;cursor=body.get('resume_token')
        if not cursor: break
        assert cursor not in cursors and body['transfers']
        cursors.add(cursor)
    assert len({(r['account_id'],r['block_height'],r['transfer_index']) for r in rows})==len(rows)
    return rows,pages

def verify():
    states={s['tag']:s for s in json.loads((DATA/'boundary-states.json').read_text())}
    a,b=states['Aug23'],states['Aug24']; start,end=map(int,(a['timestampNs'],b['timestampNs']))
    out=[]; allrows=[]
    for owner in IDS:
        week,pages=terminal_week_rows(owner); rows=[r for r in week if start<int(r['block_timestamp'])<=end]
        assert all(a['height']<int(r['block_height'])<=b['height'] for r in rows)
        allrows.extend(rows)
        received=sum(int(r['amount']) for r in rows if int(r['amount'])>0)
        sent=-sum(int(r['amount']) for r in rows if int(r['amount'])<0)
        opening=int(a['balances'][owner].get('nep141:wrap.near',0));closing=int(b['balances'][owner].get('nep141:wrap.near',0))
        residual=closing-opening-received+sent
        assert residual==0, (owner,residual)
        out.append(dict(collector=owner,sourcePagesComplete=pages,receivedEvents=sum(int(r['amount'])>0 for r in rows),sentEvents=sum(int(r['amount'])<0 for r in rows),receivingReceipts=len({r['receipt_id'] for r in rows if int(r['amount'])>0}),openingRaw=str(opening),grossCreditsRaw=str(received),grossDebitsRaw=str(sent),closingRaw=str(closing),unexplainedNetRaw=str(residual)))
    return dict(startUtc=utc(start),endUtc=utc(end),openingBlock=a['height'],closingBlock=b['height'],openingBlockHash=a['hash'],closingBlockHash=b['hash'],rows=out,grossCreditsRaw=str(sum(int(r['grossCreditsRaw']) for r in out)),grossDebitsRaw=str(sum(int(r['grossDebitsRaw']) for r in out)),source='Complete bidirectional verifier wNEAR transfer cursor chains for the full first week, restricted to the first UTC day. Each collector gross credit and debit sum reconciles archival opening and closing wNEAR. Credits are token movements, with attribution evaluated separately.'),allrows

if __name__=='__main__':
    result,_=verify()
    (DATA/'first-day-wnear-bridge.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
