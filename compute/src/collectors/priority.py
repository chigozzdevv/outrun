"""Freeze the 2,000 most valuable still-unseen >=1 wNEAR transaction hashes at the priority cutoff."""
from compute.src.paths import ROOT, CODE_ROOT
import csv
from collections import Counter
from datetime import datetime
from decimal import Decimal,localcontext
import gzip
import hashlib
import io
import json
from compute.src.collectors.inventory import complete_chains
from compute.src.collectors.batches import BATCH,packet,PACE
from compute.src.collectors.receipts import archived
from compute.src.collectors.report import units
from compute.src.collectors.history import DATA,WRAP

CUTOFF=datetime.fromisoformat('2026-11-02T16:58:22+00:00')
OUT=DATA/'priority-2000-at-2026-11-02T165822Z.csv'

def snapshot():
    rows,missing=complete_chains();assert not any('-credits1-' in r['query'] for r in missing)
    with archived() as archive:prior={n[:-5] for n in archive.namelist()}
    now=set()
    for p in BATCH.glob('*.json.gz'):
        content=json.loads(gzip.decompress(p.read_bytes()));req,tx=packet(p);hashes={x['transaction']['hash'] for x in tx}
        now.update(hashes)
        if datetime.fromisoformat(content['retrievedAt'])<=CUTOFF:prior.update(hashes)
    byhash=Counter()
    for r in rows:
        if r['asset_id']!=WRAP or int(r['amount'])<10**24 or r['transaction_id'] in prior:continue
        assert r['transaction_id'];byhash[r['transaction_id']]+=int(r['amount'])
    sorted_hashes=sorted(byhash,key=lambda tx:(-byhash[tx],tx));focus=sorted_hashes[:2000]
    assert len(focus)==2000
    out=io.StringIO(newline='');w=csv.writer(out,lineterminator='\n');w.writerow(['priority_rank','transaction_hash','qualifying_wnear_credit_raw','qualifying_wnear_credit'])
    for rank,tx in enumerate(focus,1):w.writerow([rank,tx,str(byhash[tx]),units(byhash[tx])])
    if OUT.exists():assert OUT.read_text()==out.getvalue()
    else:OUT.write_text(out.getvalue())
    covered=set(focus)&now
    return dict(baselineUTC=CUTOFF.isoformat(),targetHashes=len(focus),targetCreditWNEAR=units(sum(byhash[tx] for tx in focus)),
                acquiredTrees=len(covered),acquiredTreesCreditWNEAR=units(sum(byhash[tx] for tx in covered)),remainingTrees=len(focus)-len(covered),
                remainingCreditWNEAR=units(sum(byhash[tx] for tx in focus if tx not in covered)),
                note='Acquired trees means raw historical transaction bodies retrieved from the Explorer index. Receipt execution and signature attribution require a separate verification pass.')
if __name__=='__main__':
    with localcontext() as c:c.prec=95;s=snapshot()
    print(json.dumps(s,indent=2))
