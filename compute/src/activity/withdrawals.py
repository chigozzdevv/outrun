"""Rebuild fees explicitly charged in verifier events inside the 794 archived withdrawal trees."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import csv
import io
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from zipfile import ZipFile


SOURCE = ROOT / 'data/verified/withdrawal-rpc-2026-11-02'
DEST = ROOT / 'data/verified/paid-activity-2026-11-02'
CSV = ROOT / 'public/data/near-intents-withdrawal-linked-paid-events-2026-11-02.csv'
SUMMARY = DEST / 'withdrawal-linked-summary.json'

def derive():
    with (SOURCE/'withdrawal-rpc-matches.csv').open() as f:
        candidates={row['transaction_hash']:row for row in csv.DictReader(f)}
    collectors={r['collector_account'] for r in candidates.values()};rows=[];seen=set();fee_assets=Counter()
    with ZipFile(SOURCE/'archival-responses.zip') as z:
        assert {p.removesuffix('.json') for p in z.namelist()}==set(candidates)
        for tx,r in sorted(candidates.items(),key=lambda pair:pair[1]['receipt_utc']):
            x=json.loads(z.read(tx+'.json'))['result'];assert x['transaction']['hash']==tx and x['final_execution_status']=='FINAL'
            for ro in x['receipts_outcome']:
                o=ro['outcome']
                if o['executor_id']!='intents.near' or not any(k.startswith('Success') for k in o['status']): continue
                logs=[json.loads(s[11:]) for s in o['logs'] if s.startswith('EVENT_JSON:')]
                executed={(d['account_id'],d['intent_hash']) for e in logs if (e['standard'],e['event'])==('dip4','intents_executed') for d in e['data']}
                for e in logs:
                    if (e['standard'],e['event'])!=('dip4','token_diff'):continue
                    for d in e['data']:
                        fees={t:int(v) for t,v in d.get('fees_collected',{}).items() if int(v)>0}
                        if not fees:continue
                        account,ih=d['account_id'],d['intent_hash']
                        assert (account,ih) in executed and ih not in seen
                        assert all(int(d['diff'][token])<0 for token in fees);seen.add(ih)
                        fee_assets.update(fees)
                        rows.append(dict(withdrawal_receipt_utc=r['receipt_utc'],transaction_hash=tx,verifier_receipt_id=ro['id'],verifier_receipt_block_hash=ro['block_hash'],charged_account=account,intent_hash=ih,role='known withdrawal collector' if account in collectors else 'other charged signer in the withdrawal-selected transaction',referral=d.get('referral',''),fee_by_asset_atomic=json.dumps({k:str(v) for k,v in fees.items()},sort_keys=True,separators=(',',':')),transaction_url='https://nearblocks.io/txns/'+tx))
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)
    periods=[('2026-08-23','2026-09-22'),('2026-09-22','2026-10-22')]
    cohorts=[{r['charged_account'] for r in rows if start<=r['withdrawal_receipt_utc'][:10]<end} for start,end in periods]
    per=defaultdict(list)
    for r in rows:per[r['charged_account']].append(date.fromisoformat(r['withdrawal_receipt_utc'][:10]))
    summary=dict(withdrawalTxTrees=len(candidates),treesWithPositiveFee=len({r['transaction_hash'] for r in rows}),verifiedPositiveFeeIntents=len(rows),chargedAccounts=len(per),tokenIds=len(fee_assets),activeKnownWithdrawalCollectors=len(set(per)&collectors),otherChargedSigners=len(set(per)-collectors),knownCollectorFeeIntents=sum(r['charged_account'] in collectors for r in rows),firstWindowChargedAccounts=len(cohorts[0]),secondWindowChargedAccounts=len(cohorts[1]),bothWindows=len(cohorts[0]&cohorts[1]),otherBothWindows=len((cohorts[0]&cohorts[1])-collectors),atLeastThirtyDaysBetweenRecordedCharges=sum((max(v)-min(v)).days>=30 for v in per.values()),combinedFeesAtomicByToken={k:str(v) for k,v in sorted(fee_assets.items())},scope='Selected by observed withdrawals, not by ordinary orders. Dates use the associated verified native withdrawal receipt. Accounts may be collectors, solvers or counterparties; a signer is not an identified end customer. The archive covers fewer than 90 days.')
    return {CSV:stream.getvalue().encode(),SUMMARY:(json.dumps(summary,indent=2)+'\n').encode()}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--build',action='store_true');args=parser.parse_args()
    for path,body in derive().items():
        if args.build:path.write_bytes(body)
        else:assert path.read_bytes()==body,f'{path.name} differs from verified archived fee events'
