"""Resolve indexed receipt vs archival outcome block identifiers only where they differ."""
from compute.src.paths import ROOT, CODE_ROOT
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from zipfile import ZIP_DEFLATED, ZipFile


DIR=ROOT/'data/verified/withdrawal-rpc-2026-11-02'
CAPTURE=DIR/'block-header-responses.zip'
ENDPOINT='https://archival-rpc.mainnet.fastnear.com'
START='2026-08-23'
MID='2026-09-22'
EXT='2026-10-22'


def read_header(raw, block_id):
    h=json.loads(raw)['result']['header']
    if isinstance(block_id,int):
        assert h['height']==block_id
    else:
        assert h['hash']==block_id
    assert h['timestamp_nanosec'].isdigit()
    sec,ns=divmod(int(h['timestamp_nanosec']),10**9)
    timestamp=datetime.fromtimestamp(sec,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+f'.{ns:09d}Z'
    return {'hash':h['hash'],'height':h['height'],'utc':timestamp,'parent':h['prev_hash']}


def capture(block_id):
    body={'jsonrpc':'2.0','id':'withdrawal-block-time','method':'block','params':{'block_id':block_id}}
    for attempt in range(3):
        try:
            raw=subprocess.check_output(['curl','-fsS','--connect-timeout','10','--max-time','24','--header','Content-Type: application/json','--data-binary',json.dumps(body),ENDPOINT],stderr=subprocess.DEVNULL)
            read_header(raw,block_id)
            return raw
        except (subprocess.CalledProcessError,KeyError,ValueError):
            time.sleep((attempt+1)*3)
    return None


def window(timestamp):
    return 'extension' if timestamp>=EXT else 'second' if timestamp>=MID else 'first' if timestamp>=START else 'before study'


def main(fetch):
    rows=[r for r in csv.DictReader((DIR/'withdrawal-rpc-matches.csv').open()) if r['indexed_block_equals_rpc_outcome_block']=='false']
    headers={}
    if CAPTURE.exists():
        with ZipFile(CAPTURE) as archive:
            headers={name:archive.read(name) for name in archive.namelist()}
    def get(block_id):
        key=f'{block_id}.json'
        if key not in headers and fetch:
            time.sleep(1.2)
            data=capture(block_id)
            if data:headers[key]=data
        return read_header(headers[key],block_id) if key in headers else None
    audits=[]
    for row in rows:
        outcome=get(row['rpc_receipt_block_hash'])
        indexed_height=int(row['receipt_block'])
        indexed_at_height=get(indexed_height) if outcome else None
        entry={'transaction_hash':row['transaction_hash'],'receipt_id':row['receipt_id'],'indexedBlockHeight':indexed_height,
               'indexedBlockHash':row['indexed_block_hash'],'indexedUTC':row['receipt_utc'],'rpcOutcomeBlockHash':row['rpc_receipt_block_hash'],
               'canonicalIndexedHeight':indexed_at_height,'rpcOutcomeHeader':outcome,
               'isCanonicalAtIndexedHeight':indexed_at_height['hash']==row['indexed_block_hash'] if indexed_at_height else None,
               'outcomeHeightMinusIndexedHeight':outcome['height']-indexed_height if outcome else None,
               'outcomeWindowMatchesIndexedWindow':window(outcome['utc'])==window(row['receipt_utc']) if outcome else None}
        audits.append(entry)
    if fetch:
        tmp=CAPTURE.with_suffix('.part')
        with ZipFile(tmp,'w',ZIP_DEFLATED) as archive:
            for key,raw in sorted(headers.items()):archive.writestr(key,raw)
        tmp.replace(CAPTURE)
    summary={'differentHashes':len(audits),'resolvedBothHeaders':sum(a['canonicalIndexedHeight'] is not None and a['rpcOutcomeHeader'] is not None for a in audits),
             'windowDisagreements':sum(a['outcomeWindowMatchesIndexedWindow'] is False for a in audits),'rows':audits}
    (DIR/'receipt-block-times.json').write_text(json.dumps(summary,indent=2)+'\n')
    print({k:v for k,v in summary.items() if k!='rows'})


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--fetch',action='store_true')
    main(parser.parse_args().fetch)
