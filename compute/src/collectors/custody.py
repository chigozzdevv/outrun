"""Measure every verifier balance event involving a collector in the selected 794 trees."""
from compute.src.paths import ROOT, CODE_ROOT
from collections import Counter
import csv
from decimal import Decimal,localcontext
import gzip
import io
import json
from zipfile import ZipFile
from compute.src.collectors.history import ROOT,DATA,IDS,VERIFIER,TIMEBASE
from compute.src.collectors.balances import snapshots


def main():
    d=ROOT/'data/verified/withdrawal-rpc-2026-11-02'
    amounts=Counter(); peers=Counter();events=[];intents=[]
    members=set(IDS)
    with ZipFile(d/'archival-responses.zip') as z:
        for name in sorted(z.namelist()):
            tx=name[:-5];tree=json.loads(z.read(name))['result']
            assert tree['transaction']['hash']==tx and tree['final_execution_status']=='FINAL'
            for receipt in tree['receipts_outcome']:
                outcome=receipt['outcome'];rid=receipt['id']
                if outcome['executor_id']!='intents.near' or not any(k.startswith('Success') for k in outcome['status']):continue
                for ix,line in enumerate(outcome['logs']):
                    if not line.startswith('EVENT_JSON:'):continue
                    e=json.loads(line[11:]);kind=e['event']
                    for di,v in enumerate(e.get('data',[])):
                        if e['standard']=='dip4':
                            if v.get('account_id') in members or v.get('receiver_id') in members:
                                intents.append(dict(tx=tx,receipt=rid,log_index=ix,data_index=di,event=kind,account_id=v.get('account_id'),event_data=json.dumps(v,sort_keys=True,separators=(',',':'))))
                            continue
                        if kind not in ('mt_mint','mt_burn','mt_transfer'):continue
                        assert e['standard']=='nep245'
                        f=v.get('old_owner_id',v.get('owner_id') if kind=='mt_burn' else None)
                        t=v.get('new_owner_id',v.get('owner_id') if kind=='mt_mint' else None)
                        for asset,raw in zip(v['token_ids'],v['amounts'],strict=True):
                            for owner in members & {f,t}:
                                amount=int(raw)*(int(t==owner)-int(f==owner));counterparty=f if t==owner else t
                                amounts[owner,asset,kind,'in' if amount>0 else 'out']+=abs(amount)
                                peers[owner,asset,counterparty or '', 'in' if amount>0 else 'out']+=abs(amount)
                                events.append(dict(tx=tx,receipt=rid,log_index=ix,data_index=di,event=kind,account_id=owner,
                                  token_id=asset,change_raw=str(amount),other_account=counterparty or '',memo=v.get('memo','')))
    for basename,data in [('selected-794-mt-events',events),('selected-794-intent-events',intents)]:
        stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=data[0],lineterminator='\n');writer.writeheader();writer.writerows(data)
        (DATA/(basename+'.csv.gz')).write_bytes(gzip.compress(stream.getvalue().encode(),mtime=0))
    states=json.loads((DATA/'boundary-states.json').read_text());a=next(v for v in states if v['tag']=='Aug23');b=next(v for v in states if v['tag']=='Oct28')
    result=[]
    for owner in IDS:
        assets=set(a['balances'][owner])|set(b['balances'][owner])|{k[1] for k in amounts if k[0]==owner}
        for token in sorted(assets):
            opening=int(a['balances'][owner].get(token,0));closing=int(b['balances'][owner].get(token,0))
            credits=sum(v for k,v in amounts.items() if k[0]==owner and k[1]==token and k[3]=='in')
            debits=sum(v for k,v in amounts.items() if k[0]==owner and k[1]==token and k[3]=='out')
            result.append(dict(owner=owner,asset=token,openingRaw=str(opening),closingRaw=str(closing),selectedTreeCreditsRaw=str(credits),
                          selectedTreeDebitsRaw=str(debits),netChangesOutsideSelectedTreesRaw=str(closing-opening-credits+debits)))
    fields=list(result[0]);p=DATA/'selected-794-state-bridge.csv';stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n');writer.writeheader();writer.writerows(result);p.write_text(stream.getvalue())
    summary={'start':{k:v for k,v in a.items() if k!='balances'},'end':{k:v for k,v in b.items() if k!='balances'},'balanceEvents':len(events),'signedIntentEventEntries':len(intents),
             'wrapRows':[r for r in result if r['asset']=='nep141:wrap.near'],
             'wrapCounterparties':[{'owner':o,'otherAccount':p,'direction':s,'amountNEAR':str(Decimal(v)/TIMEBASE)} for (o,k,p,s),v in sorted(peers.items()) if k=='nep141:wrap.near'],
             'scope':'All NEP-245 verifier ledger events inside 794 selected RPC transaction trees, compared to complete token inventories at two pinned blocks. Residual is net movement outside those trees, not presumed acquisitions.'}
    (DATA/'selected-794-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary

if __name__=='__main__':
    with localcontext() as c:
        c.prec=80;s=main();print(s['balanceEvents'],s['signedIntentEventEntries'],s['wrapRows'])
