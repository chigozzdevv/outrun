"""Trace the first UTC day's Ethereum-USDT inventory for the commercial collector that spent that asset on wNEAR."""
from compute.src.paths import ROOT, CODE_ROOT
from collections import Counter,defaultdict
import argparse
import csv
from decimal import Decimal,localcontext
from fractions import Fraction
import gzip
import json
import io
from compute.src.collectors.history import ROOT,DATA,PAGES,VERIFIER,restore,stable_queries,utc
from compute.src.collectors.receipts import RPC,archived,fetch_tx,verify_effects
from compute.src.collectors.finality import BLOCKS,effects
from compute.src.collectors.exchanges import signed_intents,verify_sigs,nep413,ALPHA
from compute.src.collectors.opening import verify as first_day

def payer_key(s):
    prefix,encoded=s['public_key'].split(':',1);assert prefix=='ed25519'
    value=0
    for char in encoded:value=58*value+ALPHA.index(char)
    key=bytes(len(encoded)-len(encoded.lstrip('1')))+value.to_bytes((value.bit_length()+7)//8,'big')
    assert len(key)==32
    return key.hex()

def rows():
    (key,params),=list(stable_queries()); cursor=None;page=0; records=[];seen=set()
    states={v['tag']:v for v in json.loads((DATA/'boundary-states.json').read_text())};a,b=states['Aug23'],states['Aug24']
    while True:
        req,body=restore(PAGES/(f'{key}-{page:04}.json.gz'))
        assert req==(params if cursor is None else dict(params,resume_token=cursor))
        for r in body['transfers']:
            assert r['account_id']==params['account_id'] and r['asset_id']==params['asset_id']
            if not a['height']<int(r['block_height'])<=b['height']:continue
            assert int(a['timestampNs'])<int(r['block_timestamp'])<=int(b['timestampNs'])
            ix=(r['block_height'],r['transfer_index']);assert ix not in seen;seen.add(ix);records.append(r)
        cursor=body.get('resume_token');page+=1
        if not cursor:break
    token=params['asset_id'][len(VERIFIER):];owner=params['account_id'];opening=int(a['balances'][owner].get(token,0));closing=int(b['balances'][owner].get(token,0))
    incoming=sum(int(r['amount']) for r in records if int(r['amount'])>0);outgoing=-sum(int(r['amount']) for r in records if int(r['amount'])<0)
    assert closing-opening==incoming-outgoing
    allassets=list(csv.DictReader((DATA/'first-day-all-asset-inventory.csv').open()));match=next(r for r in allassets if r['collector']==owner and r['asset_id']==token)
    assert int(match['derived_gross_credit_raw'])==incoming and int(match['indexed_gross_debit_raw'])==outgoing
    bridge=dict(owner=owner,token=token,tokenDecimals=6,studyDayUTC='2026-08-23',openingRaw=opening,grossCreditRaw=incoming,grossDebitRaw=outgoing,closingRaw=closing,sourcePages=page,totalCreditEvents=sum(int(r['amount'])>0 for r in records),totalDebitEvents=sum(int(r['amount'])<0 for r in records))
    return bridge,records

def direct(tx,rid,block,known):
    p=BLOCKS/(str(block)+'.json.gz')
    if p.is_file():
        body=json.loads(gzip.decompress(p.read_bytes()));h=body['block']['header']
        for shard in body['shards']:
            entry=next((v for v in shard['receipt_execution_outcomes'] if v['receipt']['receipt_id']==rid),None)
            if entry:
                assert entry['execution_outcome']['block_hash']==h['hash'] and entry['tx_hash']==tx
                return effects(entry),h['hash'],int(h['timestamp_nanosec'])
    if tx+'.json' in known.namelist():raw=known.read(tx+'.json')
    elif (RPC/(tx+'.json.gz')).is_file():raw=gzip.decompress((RPC/(tx+'.json.gz')).read_bytes())
    else:return None
    x=json.loads(raw)['result'];assert x['transaction']['hash']==tx and x['final_execution_status']=='FINAL'
    out=next(o for o in x['receipts_outcome'] if o['id']==rid)
    rec=next((r for r in x['receipts'] if r['receipt_id']==rid),None)
    if rec is None:
        assert x['transaction_outcome']['outcome']['receipt_ids']==[rid] and x['transaction']['receiver_id']==x['transaction']['signer_id']
        rec=dict(receipt_id=rid,receiver_id=x['transaction']['receiver_id'],receipt={'Action':{'actions':x['transaction']['actions']}})
    return effects(dict(receipt=rec,execution_outcome=out,tx_hash=tx)),out['block_hash'],None

def fetch(threshold,debits=False):
    bridge,index=rows();groups=defaultdict(list)
    for r in index:
        if (int(r['amount'])<0 if debits else int(r['amount'])>0):groups[r['transaction_id'],r['receipt_id']].append(r)
    with archived() as z:
        for (tx,rid),group in sorted(groups.items(),key=lambda kv:-sum(int(r['amount']) for r in kv[1])):
            n=abs(sum(int(r['amount']) for r in group));first=group[0]
            if n<threshold*10**6 or direct(tx,rid,first['block_height'],z):continue
            print('fetch high-quantity stablecoin input',tx,n,flush=True)
            ok=fetch_tx(dict(tx=tx,signer=first['signer_id']))
            print('archival tree',tx,ok,flush=True)
            if not ok:
                print('Archive provider unavailable. Retain measured uncovered quantity and resume later.',flush=True);return

def disposition(b,index):
    groups=defaultdict(list);sigs={};rows=[]
    for r in index:
        if int(r['amount'])<0:groups[r['transaction_id'],r['receipt_id']].append(r)
    with archived() as z:
        for (tx,rid),group in groups.items():
            first=group[0];owner=b['owner'];token=b['token'];full=direct(tx,rid,first['block_height'],z)
            if not full:continue
            state,block_hash,stamp=full;assert state
            if stamp is not None:assert stamp==int(first['block_timestamp'])
            _,_,events,signed,net,moves=state
            actual=Counter((m['target'],-m['change']) for m in moves if m['owner']==owner and m['asset']==token and m['change']<0)
            indexed=Counter((r['other_account_id'],-int(r['amount'])) for r in group);assert actual==indexed
            debits=Counter()
            for m in moves:
                if m['owner']==owner and m['asset']!='nep141:wrap.near' and m['change']<0:debits[m['asset']]-=m['change']
            c=dict(collector=owner,tx=tx,stamp=int(first['block_timestamp']),rid=rid,block=int(first['block_height']),w=net[owner]['nep141:wrap.near'],debits=debits)
            proof,ss=verify_effects(c,events,signed);sigs.update({r['hash']:r for r in ss})
            inputs=json.loads(proof['input_raw_units']);out=-sum(int(r['amount']) for r in group);spent=inputs.get(token,0)
            single_asset=len(inputs)==1 and json.loads(proof['bundled_outputs'])==[]
            assert spent<=out
            rows.append(dict(transactionHash=tx,receiptId=rid,blockHeight=first['block_height'],blockHash=block_hash,indexedDebitRaw=out,USDTSpentInVerifiedWNEARExchangesRaw=spent,standaloneUSDTToWNEAR=single_asset,wNEARAcquiredRaw=proof['signed_exchange_wnear_raw'],USDTExchangeEventFeeRaw=json.loads(proof['exchange_fees_raw_units']).get(token,0),signedIntents=proof['signed_exchange_intents']))
    if sigs:verify_sigs(list(sigs.values()))
    summary=dict(indexedDebitReceipts=len(groups),confirmedDebitReceipts=len(rows),verifiedAllPurposeDebitRaw=sum(r['indexedDebitRaw'] for r in rows),
                 verifiedAcquisitionDebitRaw=sum(r['USDTSpentInVerifiedWNEARExchangesRaw'] for r in rows),standaloneOutputWNEARRaw=str(sum(int(r['wNEARAcquiredRaw']) for r in rows if r['standaloneUSDTToWNEAR'])),exchangeEventFeeRaw=sum(r['USDTExchangeEventFeeRaw'] for r in rows))
    return summary,rows

def derive():
    b,index=rows();owner=b['owner'];token=b['token'];groups=defaultdict(list)
    for r in index:
        if int(r['amount'])>0:groups[r['transaction_id'],r['receipt_id']].append(r)
    results=[];sigs={}
    with archived() as z:
        for (tx,rid),group in groups.items():
            first=group[0];full=direct(tx,rid,first['block_height'],z)
            if not full:continue
            effect,blockhash,stamp=full;assert effect;_,_,events,signed,net,moves=effect
            if stamp is not None:assert stamp==int(first['block_timestamp'])
            incoming=Counter((m['origin'],m['change']) for m in moves if m['owner']==owner and m['asset']==token and m['change']>0)
            actual=Counter((r['other_account_id'],int(r['amount'])) for r in group);assert incoming==actual,(tx,'whole receipt gross incoming stablecoin')
            def data(name):return [d for e in events if e['event']==name for d in e['data']]
            assert all(e['standard']=='dip4' for e in events if e['event'] in ('token_diff','transfer','intents_executed'))
            diffs=data('token_diff');complete={(v['account_id'],v['intent_hash']) for v in data('intents_executed')};mine=[d for d in diffs if d['account_id']==owner]
            collector_exchange=0;other_signed=0;external=0;unsigned_transfers=0;linked=0;payers=[];exchange_inputs=Counter()
            for d in mine:
                diff=d['diff'];chg=int(diff.get(token,0));spend={k:-int(v) for k,v in diff.items() if k!=token and int(v)<0}
                if chg<=0 or not spend:other_signed+=chg;continue
                h=d['intent_hash'];assert (owner,h) in complete and h in signed
                payload=signed[h];msg=json.loads(payload['payload']['message'])
                assert payload['payload']['recipient']=='intents.near' and msg['signer_id']==owner
                assert [j['diff'] for j in msg['intents'] if j.get('intent')=='token_diff']==[v['diff'] for v in mine if v['intent_hash']==h]
                sigs[h]=dict(hash=nep413(payload).hex(),pk=payload['public_key'],sig=payload['signature'],collector=owner)
                collector_exchange+=chg;exchange_inputs.update(spend)
            for t in data('transfer'):
                n=int(t['tokens'].get(token,0));external+=(int(t['receiver_id']==owner)-int(t['account_id']==owner))*n
                if t['receiver_id']==owner and n:
                    payer=t['account_id'];ih=t['intent_hash'];matching=[d for d in diffs if d['account_id']==payer and d['intent_hash']==ih]
                    paired=bool(matching)
                    assert (t['account_id'],t['intent_hash']) in complete
                    if paired:linked+=n
                    detail=dict(sender=payer,intent=ih,raw=n,swapInSameIntent=paired,memo=t.get('memo',''),exchangeDifferences=[v['diff'] for v in matching],signatureBinding='finalized verifier execution')
                    if ih in signed:
                        payload=signed[ih];msg=json.loads(payload['payload']['message']);assert payload['payload']['recipient']=='intents.near' and msg['signer_id']==payer
                        signed_tf=[(v['receiver_id'],v['tokens'],v.get('memo','')) for v in msg['intents'] if v.get('intent')=='transfer']
                        actual_tf=[(v['receiver_id'],v['tokens'],v.get('memo','')) for v in data('transfer') if v['account_id']==payer and v['intent_hash']==ih]
                        assert signed_tf==actual_tf and [v['diff'] for v in msg['intents'] if v.get('intent')=='token_diff']==[v['diff'] for v in matching]
                        key=payer_key(payload);sigs[ih]=dict(hash=nep413(payload).hex(),pk=payload['public_key'],sig=payload['signature'],collector=key)
                        detail['signatureBinding']='Ed25519 implicit payer' if key==payer else 'Ed25519 verifier-accepted key'
                        detail['referrals']=[v.get('referral') for v in msg['intents'] if v.get('intent')=='token_diff']
                        detail['withdrawalInputs']=[dict(token=v['token'],amount=v['amount']) for v in msg['intents'] if v.get('intent')=='ft_withdraw']
                    payers.append(detail)
            mint_burn=sum(m['change'] for m in moves if m['owner']==owner and m['asset']==token and m['kind'] in ('mt_mint','mt_burn'))
            remainder=net[owner][token]-collector_exchange-other_signed-external-mint_burn
            assert remainder==0,(tx,'unmatched inbound USDT origin',remainder)
            quantities=dict(indexedCreditRaw=sum(int(r['amount']) for r in group),signedCollectorExchangeRaw=collector_exchange,otherCollectorExchangeRaw=other_signed,explicitNetTransferRaw=external,swapLinkedIncomingTransferRaw=linked,mintMinusBurnRaw=mint_burn,unexplainedNetRaw=remainder)
            results.append(dict(transactionHash=tx,receiptId=rid,blockHeight=first['block_height'],blockHash=blockhash,timeUTC=utc(first['block_timestamp']),**quantities,collectorExchangeOtherAssetsSpentRaw=json.dumps(dict(exchange_inputs),sort_keys=True),explicitSources=json.dumps(payers,sort_keys=True)))
    if sigs:verify_sigs(list(sigs.values()))
    sums={k:sum(r[k] for r in results) for k in ['indexedCreditRaw','signedCollectorExchangeRaw','otherCollectorExchangeRaw','explicitNetTransferRaw','swapLinkedIncomingTransferRaw','mintMinusBurnRaw','unexplainedNetRaw']}
    pure=sum(r['indexedCreditRaw'] for r in results if r['swapLinkedIncomingTransferRaw']==r['explicitNetTransferRaw']==r['indexedCreditRaw'] and r['signedCollectorExchangeRaw']==r['otherCollectorExchangeRaw']==r['mintMinusBurnRaw']==0)
    tags=[t for r in results for t in json.loads(r['explicitSources']) if t['swapInSameIntent']]
    return dict(bridge=b,canonicalReceipts=len(results),canonicalCreditEvents=sum(len(groups[r['transactionHash'],r['receiptId']]) for r in results),canonicalRawTotals=sums,pureSwapLinkedCreditRaw=pure,independentlyVerifiedPayerSignatures=len({t['intent'] for t in tags if t['signatureBinding'].startswith('Ed25519')}),independentlyVerifiedImplicitPayerSignatureRaw=sum(t['raw'] for t in tags if t['signatureBinding']=='Ed25519 implicit payer'),uniqueSwapLinkedPayerAccounts=len({t['sender'] for t in tags}),unclassifiedIncomingRaw=b['grossCreditRaw']-sums['indexedCreditRaw'],scope='One collector, one verifier token, one complete UTC date. Balance stocks and uncensored incoming/outgoing entries are complete. Canonical attribution applies only to the explicitly matched receipt population; a swap-linked incoming transfer proves payer execution, not independence, an agreed fee rate, operating earnings or fiat cash.'),results

def minimum_output(b,pure,rows):
    if not rows or any(not r['standaloneUSDTToWNEAR'] for r in rows):return None
    if sum(r['USDTSpentInVerifiedWNEARExchangesRaw'] for r in rows)!=b['grossDebitRaw']:return None
    available=b['openingRaw']+b['grossCreditRaw']-pure
    def rate(r):return Fraction(int(r['wNEARAcquiredRaw']),r['USDTSpentInVerifiedWNEARExchangesRaw'])
    maximum_other=Fraction(0);budget=available
    for r in sorted(rows,key=rate,reverse=True):
        used=min(budget,r['USDTSpentInVerifiedWNEARExchangesRaw']);maximum_other+=used*rate(r);budget-=used
        if budget==0:break
    output=sum(int(r['wNEARAcquiredRaw']) for r in rows);minimum=max(Fraction(0),Fraction(output)-maximum_other)
    floor=minimum.numerator//minimum.denominator
    return dict(otherFundingAvailableUSDTRaw=available,totalOutputWNEARRaw=str(output),minimumSwapLinkedWNEARRaw=str(floor),
                maximumOtherFundedOutputRationalRaw=dict(numerator=str(maximum_other.numerator),denominator=str(maximum_other.denominator)),
                method='The entire opening stablecoin stock and every other incoming unit may fund trades first, even when it would be chronologically impossible. Allocate that maximum budget to the observed exchanges with the highest realized wNEAR per USDT first, allowing fractional allocation within an exchange. Subtract that rational upper bound from total output and round the minimum down to a whole atomic unit.')

def matched_output_allocations(b,credits):
    groups={}
    for row in credits:
        for p in json.loads(row['explicitSources']):
            key=(p['sender'],p['intent']);gross=sum(int(v.get(b['token'],0)) for v in p['exchangeDifferences'])
            withdrawal=sum(int(v['amount']) for v in p.get('withdrawalInputs',[]) if 'nep141:'+v['token']==b['token'])
            g=groups.setdefault(key,[gross,withdrawal,0])
            assert g[:2]==[gross,withdrawal];g[2]+=int(p['raw'])
    sample=[(fee,gross) for gross,withdraw,fee in groups.values() if withdraw>0 and gross==withdraw+fee]
    fees=sum(f for f,g in sample);gross=sum(g for f,g in sample);rates=sorted(Fraction(f*10000,g) for f,g in sample)
    if not sample:return None
    mid=len(rates)//2;median=(rates[mid]+rates[~mid])/2
    def decimal(r):return format(Decimal(r.numerator)/Decimal(r.denominator),'.8f')
    return dict(signedOrders=len(sample),collectorAllocationsUSDTRaw=fees,signedGrossOutputUSDTRaw=gross,weightedAllocationBps=decimal(Fraction(10000*fees,gross)),medianOrderAllocationBps=decimal(median),minimumBps=decimal(rates[0]),maximumBps=decimal(rates[-1]),
                scope='Only orders for which the signed positive Ethereum-USDT output exactly equals the payer-directed collector allocation plus the signed USDT withdrawal instruction. Weighted bps uses total allocations divided by total gross output. The median weights each order equally. Both refer to this single collector, asset and UTC date.')

def treasury_delivery(b,output):
    if output is None:return None
    day,index=first_day();stock=next(v for v in day['rows'] if v['collector']==b['owner'])
    out=[r for r in index if r['account_id']==b['owner'] and int(r['amount'])<0]
    rows=list(csv.DictReader((ROOT/'public/data/near-intents-collector-withdrawals-2026-08-23-to-2026-10-28.csv').open()))
    payouts={}
    for r in rows:
        if r['collector_account']==b['owner'] and r['withdrawal_utc'].startswith(b['studyDayUTC']):
            assert r['rpc_check']=='verified' and r['rpc_receipt_status'].startswith('Success')
            payouts[r['transaction_hash']]=r
    assert set(payouts)=={r['transaction_id'] for r in out} and len(out)==len(payouts)
    payees={r['treasury_recipient'] for r in payouts.values()};assert len(payees)==1
    for v in out:
        r=payouts[v['transaction_id']];assert int(Decimal(r['rpc_transfer_near'])*10**24)==-int(v['amount'])
    lower=max(0,int(output['minimumSwapLinkedWNEARRaw'])-int(stock['closingRaw']))
    return dict(collector=b['owner'],nativeRecipient=next(iter(payees)),verifiedPayoutTransactions=sorted(payouts),grossNativeDeliveredRaw=stock['grossDebitsRaw'],collectorClosingWrappedNEARRaw=stock['closingRaw'],minimumSwapLinkedNativeNEARDeliveredRaw=str(lower),
                method='Every wrapped-NEAR debit that day matches a successful native payout to the same treasury wallet. Deduct the entire collector closing wNEAR inventory from the conservatively rounded lower bound on swap-funded wNEAR, allowing every retained unit to be swap-funded. The remaining quantity must have reached that treasury wallet by day close.')

def chronological_trace(b,credits,trades,index,delivery,fetch):
    from compute.src.collectors.balances import query
    if len(credits)!=b['totalCreditEvents'] or delivery is None:return None
    assert sum(r['swapLinkedIncomingTransferRaw'] for r in credits)==sum(r['indexedCreditRaw'] for r in credits)==b['grossCreditRaw']
    trades=sorted(trades,key=lambda row:int(row['blockHeight']));opening=trades[0];purchase_receipts={r['receiptId']:r for r in trades[1:]}
    first_in=min(int(r['block_height']) for r in index if int(r['amount'])>0)
    assert int(opening['blockHeight'])<first_in and opening['USDTSpentInVerifiedWNEARExchangesRaw']==b['openingRaw']
    def stock_at(height,blockhash,token):
        a=dict(tag='reset'+str(height),height=int(height),hash=blockhash)
        balance=query(a,b['owner'],'mt_batch_balance_of',dict(account_id=b['owner'],token_ids=[token]),fetch)
        assert balance==['0'],(height,'end-of-block inventory');return str(balance[0])
    opening_stock=stock_at(opening['blockHeight'],opening['blockHash'],b['token'])
    day,wrows=first_day();wrows=[r for r in wrows if r['account_id']==b['owner']]
    wstock=next(v for v in day['rows'] if v['collector']==b['owner']);balance=int(wstock['openingRaw'])
    tagged=0;paid=0;payments=[];seen=set();by_height=defaultdict(list)
    for r in wrows:by_height[int(r['block_height'])].append(r)
    with archived() as archive:
        for height,group in sorted(by_height.items()):
            receipts=defaultdict(list)
            for r in group:receipts[r['receipt_id']].append(r)
            for rid,rs in receipts.items():
                if rid not in purchase_receipts:continue
                assert rid not in seen;seen.add(rid)
                source=purchase_receipts[rid];n=int(source['wNEARAcquiredRaw'])
                assert all(r['transaction_id']==source['transactionHash'] for r in rs) and sum(max(0,int(r['amount'])) for r in rs)>=n
                tagged+=n
            balance+=sum(int(r['amount']) for r in group)
            debits=[r for r in group if int(r['amount'])<0]
            if debits:
                assert len(debits)==1 and balance==0,(height,'withdrawal must empty full wrapped inventory')
                row=debits[0];tx=row['transaction_id'];assert tx in delivery['verifiedPayoutTransactions']
                result=direct(tx,row['receipt_id'],height,archive);assert result;_,block_hash,stamp=result
                if stamp is not None:assert stamp==int(row['block_timestamp'])
                verified_state=stock_at(height,block_hash,'nep141:wrap.near')
                payments.append(dict(blockHeight=height,blockHash=block_hash,rawVerifierBalanceAfterWithdrawal=verified_state,transactionHash=tx,
                                     nativeDeliveryRaw=str(-int(row['amount'])),identifiedSwapFundedDeliveryRaw=str(tagged)))
                paid+=tagged;tagged=0
            assert 0<=tagged<=balance
    assert seen==set(purchase_receipts) and balance==int(wstock['closingRaw'])
    total=sum(int(r['wNEARAcquiredRaw']) for r in trades[1:]);assert paid+tagged==total and len(payments)==4
    return dict(firstOpeningInventoryTrade=dict(blockHeight=int(opening['blockHeight']),blockHash=opening['blockHash'],transactionHash=opening['transactionHash'],firstSwapPaymentBlock=first_in,
            usdtSpentRaw=opening['USDTSpentInVerifiedWNEARExchangesRaw'],wnearAcquiredRaw=opening['wNEARAcquiredRaw'],rawUSDTBalanceAfterTrade=opening_stock),
            whollySwapFundedTrades=len(trades)-1,whollySwapFundedGrossUSDTSpendRaw=sum(r['USDTSpentInVerifiedWNEARExchangesRaw'] for r in trades[1:]),
            whollySwapFundedUSDTExchangeFeesRaw=sum(r['USDTExchangeEventFeeRaw'] for r in trades[1:]),whollySwapFundedWNEARAcquiredRaw=str(total),
            verifiedNativeRecipient=delivery['nativeRecipient'],identifiedSwapFundedNativeDeliveryRaw=str(paid),identifiedWNEARAfterFinalDeliveryRaw=str(tagged),
            payments=payments,method='The earliest stablecoin debit exhausted the entire opening balance before the first verified incoming payment. Every subsequent USDT debit therefore spent swap-linked payments. The complete wNEAR ledger and separate pinned verifier state calls show that each native delivery emptied the entire collector wNEAR stock. Each delivery necessarily included all identified purchases since the preceding zero balance; the last identified purchase occurred after the fourth delivery.')

def release_timeline(summary,credits,trades,index):
    owner=summary['bridge']['owner'];token=summary['bridge']['token']
    rows=[]
    def quantity(n,decimals):
        with localcontext() as ctx:
            ctx.prec=90
            return format(Decimal(n).scaleb(-decimals),'f')
    def row(stage,time,tx,rid):return dict(stage=stage,time_utc=time,transaction_hash=tx,receipt_id=rid,authorizing_account='',recipient='',native_transfer_from='',usdt_received='',usdt_spent='',usdt_verifier_exchange_fee_included='',wnear_acquired='',native_near_delivered='',usdt_exchange_funding='',identified_swap_funded_native_delivered='',signed_referrals='',payer_gross_usdt_exchange_output='',payer_instructed_usdt_withdrawal='',signature_or_execution='',transaction_url='https://nearblocks.io/txns/'+tx)
    for c in credits:
        for payer in json.loads(c['explicitSources']):
            entry=row('swap-linked USDT transfer',c['timeUTC'],c['transactionHash'],c['receiptId'])
            gross=sum(int(d.get(token,0)) for d in payer['exchangeDifferences'])
            n=sum(int(d['amount']) for d in payer.get('withdrawalInputs',[]) if 'nep141:'+d['token']==token)
            entry.update(authorizing_account=payer['sender'],recipient=owner,usdt_received=quantity(payer['raw'],6),signed_referrals='|'.join(r or '' for r in payer.get('referrals',[])),payer_gross_usdt_exchange_output=quantity(gross,6) if gross>0 else '',payer_instructed_usdt_withdrawal=quantity(n,6) if n else '',signature_or_execution=payer['signatureBinding'])
            rows.append(entry)
    times={r['receipt_id']:utc(r['block_timestamp']) for r in index}
    trace=summary.get('chronologicalTrace')
    for t in trades:
        entry=row('collector exchange',times[t['receiptId']],t['transactionHash'],t['receiptId'])
        if trace:entry['usdt_exchange_funding']='opening USDT inventory' if t['transactionHash']==trace['firstOpeningInventoryTrade']['transactionHash'] else 'same-day signed swap payments'
        entry.update(authorizing_account=owner,recipient=owner,usdt_spent=quantity(t['USDTSpentInVerifiedWNEARExchangesRaw'],6),usdt_verifier_exchange_fee_included=quantity(t['USDTExchangeEventFeeRaw'],6),wnear_acquired=quantity(t['wNEARAcquiredRaw'],24),signature_or_execution='Ed25519 implicit collector');rows.append(entry)
    delivery=summary['minimumSwapLinkedTreasuryDelivery']
    if delivery:
        native=list(csv.DictReader((ROOT/'public/data/near-intents-collector-withdrawals-2026-08-23-to-2026-10-28.csv').open()))
        for t in native:
            if t['transaction_hash'] not in delivery['verifiedPayoutTransactions']:continue
            entry=row('native treasury delivery',t['receipt_utc'],t['transaction_hash'],t['receipt_id'])
            if trace:
                payout=next(p for p in trace['payments'] if p['transactionHash']==t['transaction_hash'])
                entry['identified_swap_funded_native_delivered']=quantity(payout['identifiedSwapFundedDeliveryRaw'],24)
            with archived() as archive:
                tree=json.loads(archive.read(t['transaction_hash']+'.json'))['result']
                native_receipt=next(r for r in tree['receipts'] if r['receipt_id']==t['receipt_id'])
                assert native_receipt['receiver_id']==t['treasury_recipient']
            entry.update(authorizing_account=owner,recipient=t['treasury_recipient'],native_transfer_from=native_receipt['predecessor_id'],native_near_delivered=t['rpc_transfer_near'],signature_or_execution='Signed withdrawal and successful native transfer');rows.append(entry)
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(sorted(rows,key=lambda r:(r['time_utc'],r['transaction_hash'],r['receipt_id'])))
    (ROOT/'public/data/near-intents-swap-funded-treasury-delivery-2026-08-23.csv').write_text(stream.getvalue())

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['fetch','derive','fetch-resets']);p.add_argument('--threshold',type=int,default=25);p.add_argument('--debits',action='store_true');args=p.parse_args()
    if args.mode=='fetch':fetch(args.threshold,args.debits)
    else:
        result,records=derive();b,idx=rows();d,outs=disposition(b,idx);result['debitExecution']=d
        # A swap-linked credited USDT can remain in the closing inventory or leave in any unverified debit.
        # All other tagged units must have left in a verified wNEAR exchange, regardless of lot convention.
        result['minimumSwapLinkedUSDTSpentOnVerifiedWNEARAcquisitionRaw']=max(0,result['pureSwapLinkedCreditRaw']-b['closingRaw']-(b['grossDebitRaw']-d['verifiedAcquisitionDebitRaw']))
        result['minimumSwapLinkedOutput']=minimum_output(b,result['pureSwapLinkedCreditRaw'],outs)
        result['minimumSwapLinkedTreasuryDelivery']=treasury_delivery(b,result['minimumSwapLinkedOutput'])
        result['chronologicalTrace']=chronological_trace(b,records,outs,idx,result['minimumSwapLinkedTreasuryDelivery'],args.mode=='fetch-resets')
        result['matchedOutputAllocations']=matched_output_allocations(b,records)
        release_timeline(result,records,outs,idx)
        (DATA/'stable-funding-summary.json').write_text(json.dumps(result,indent=2)+'\n')
        if records:
            f=io.StringIO(newline='');w=csv.DictWriter(f,fieldnames=list(records[0]),lineterminator='\n');w.writeheader();w.writerows(sorted(records,key=lambda r:(int(r['blockHeight']),r['receiptId'])));(DATA/'stable-funding-canonical.csv').write_text(f.getvalue())
        if outs:
            f=io.StringIO(newline='');w=csv.DictWriter(f,fieldnames=list(outs[0]),lineterminator='\n');w.writeheader();w.writerows(sorted(outs,key=lambda r:(int(r['blockHeight']),r['receiptId'])));(DATA/'stable-disposition-canonical.csv').write_text(f.getvalue())
        print(json.dumps(result,indent=2))
