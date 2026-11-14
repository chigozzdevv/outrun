"""Find and verify the finalized Aug 24 UTC boundary by adjacent canonical headers."""
from compute.src.paths import ROOT, CODE_ROOT
import http.client,gzip,json,ssl,time,sys
from compute.src.collectors.history import DATA
from compute.src.collectors.receipts import SAMPLE_END
D=DATA/'first-day-headers'
def header(height,fetch):
    p=D/f'{height}.json.gz'
    if not p.exists():
        from compute.src.collectors.finality import BLOCKS,fetch_height
        cache=BLOCKS/(str(height)+'.json.gz')
        if not cache.exists() and (not fetch or not fetch_height(height)):
            raise ValueError('Finalized header unavailable at '+str(height))
        block=json.loads(gzip.decompress(cache.read_bytes()))
        if block is None:return None
        assert block['block']['header']['height']==height
        return block['block']['header']
    j=json.loads(gzip.decompress(p.read_bytes()));h=j.get('result',{}).get('header')
    if h:assert h['height']==height
    return h

def boundary(fetch):
    D.mkdir(parents=True,exist_ok=True); low,high=212636058,212636230
    while high-low>1:
        m=(low+high)//2
        while m<high and not (h:=header(m,fetch)):m+=1
        if m==high:
            m=(low+high)//2-1
            while m>low and not(h:=header(m,fetch)):m-=1
            if m==low:break
        if int(h['timestamp_nanosec'])<SAMPLE_END:low=m
        else:high=m
    a=header(low,fetch);b=header(high,fetch);assert b['prev_hash']==a['hash'] and int(a['timestamp_nanosec'])<SAMPLE_END<=int(b['timestamp_nanosec'])
    out={'tag':'Aug24','height':a['height'],'hash':a['hash'],'timestampNs':a['timestamp_nanosec']}
    (DATA/'first-day-boundary.json').write_text(json.dumps(out,indent=2)+'\n');print(out)

if __name__=='__main__':boundary('--fetch' in sys.argv)
