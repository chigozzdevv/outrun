import { ChartFrame } from '../charts/views';
import { atomicFloor, atomicNumber, onchainResults as d } from './onchain-results';
import { fullNumber } from '../../../compute/src/format';
import { downloadName, downloadUrl } from '../thesis/downloads';

const walletNames: Record<string, string> = {
  'buybacks.multisignature.near': 'Buyback treasury',
  'fefundsadmin.sputnik-dao.near': 'Frontend treasury',
  '1csfundsadmin.sputnik-dao.near': '1Click treasury',
};
const recipientName = (account: string) => walletNames[account] ?? (account.length > 36 ? `${account.slice(0, 6)}…${account.slice(-5)}` : account);

export function NativeInflowOrigins() {
  const labels: Record<string, string> = {
    original_three_verifier_owners: 'The three collectors',
    additional_verifier_owners: 'Other withdrawal accounts',
    other_native_transfer: 'Other native senders',
    within_tracked_group: 'Transfer between the treasury wallets',
  };
  return <ChartFrame title="Origins of native treasury receipts" subtitle="1 September–30 October 2026 · three treasury wallets">
    <div className="table-scroll"><table aria-label="Origins of native treasury receipts"><thead><tr><th scope="col">Transfer route</th><th scope="col">Receipts</th><th scope="col">Native NEAR</th></tr></thead>
      <tbody>{Object.entries(d.inflowOrigins.coverageByClass).map(([key, row]) => <tr key={key}><th scope="row">{labels[key]}</th><td>{fullNumber(row.receipts, 0)}</td><td>{fullNumber(Number(row.nativeNEAR), 2)}</td></tr>)}</tbody>
    </table></div>
    <p className="figure-note">Transfers between the treasury wallets cancel out when the three wallets are added together. Receipts from other accounts can also return treasury-owned capital through intermediaries.</p>
  </ChartFrame>;
}

export function FeesToBuyback() {
  const a = d.expandedAcquisitions;
  const rows = [
    ['wNEAR the three collectors bought through exchanges', fullNumber(Number(a.signedCollectorAcquisitionsWNEAR), 2), 'wNEAR'],
    ['NEAR from those purchases delivered to the buyback treasury (at least)', fullNumber(Math.floor(Number(a.minimumExchangeAcquiredNativeNEARDelivered) * 100) / 100, 2), 'NEAR'],
    ['All NEAR the three collectors delivered to the treasury wallets', fullNumber(Number(a.allVerifiedNativeDeliveriesNEAR), 2), 'NEAR'],
    [`New NEAR created by the network (${a.matchedEpochPayouts} epoch payouts)`, fullNumber(Number(a.matchedGrossNativeIssuanceNEAR), 2), 'NEAR'],
    ['Delivered purchases as a share of new NEAR', `${Number(a.minimumDeliveredShareOfGrossMintingPct).toFixed(2)}%`, 'of new NEAR'],
    ['Upper bound: every collector delivery counted as a purchase', `${Number(a.allThreeCollectorDeliveriesShareOfGrossMintingPct).toFixed(2)}%`, 'of new NEAR'],
  ];
  return <ChartFrame title="From fees to the buyback treasury" subtitle={<>23 August–28 October 2026 · three collectors · <a href={downloadUrl(a.publicCsv)} download={downloadName(a.publicCsv)}>acquisition records</a></>}>
    <div className="table-scroll"><table aria-label="From fees to the buyback treasury"><thead><tr><th scope="col">Stage</th><th scope="col">Quantity</th><th scope="col">Unit</th></tr></thead>
      <tbody>{rows.map(([label, value, unit]) => <tr key={label}><th scope="row">{label}</th><td>{value}</td><td>{unit}</td></tr>)}</tbody>
    </table></div>
    <p className="figure-note">Purchases are exchanges into wNEAR inside Intents; deliveries are native NEAR withdrawn to the treasury. New NEAR is gross minting over the same epoch payouts. Bought NEAR is held, so it does not reduce total supply.</p>
  </ChartFrame>;
}

export function CollectorFlowAccounting() {
  return <ChartFrame title="Collector balances and treasury deliveries" subtitle="23 August–28 October 2026, 07:26 UTC · token quantities">
    <div className="table-scroll"><table aria-label="Collector wNEAR balance reconciliation"><thead><tr><th scope="col">Collector</th><th scope="col">Opening wNEAR</th><th scope="col">wNEAR credits</th><th scope="col">Native NEAR delivered</th><th scope="col">Closing wNEAR</th></tr></thead>
      <tbody>{d.flow.perCollector.map(row => <tr key={row.collector}><th scope="row">{row.collector.slice(0, 6)}…{row.collector.slice(-5)}</th><td>{fullNumber(Number(row.openingWNEAR), 3)}</td><td>{fullNumber(Number(row.allGrossCreditsWNEAR), 3)}</td><td>{fullNumber(Number(row.matchedNativeDeliveriesNEAR), 3)}</td><td>{fullNumber(Number(row.closingWNEAR), 3)}</td></tr>)}</tbody>
    </table></div>
    <p className="figure-note">For each collector: opening wrapped balance + credits − redeemed principal = closing wrapped balance. The balances reconcile exactly before rounding to three decimals.</p>
  </ChartFrame>;
}

export function SwapFundedTreasury() {
  const { bridge, chronology: trace } = d.funding;
  const rows = [
    ['Swap-linked payments received', fullNumber(atomicNumber(bridge.grossCreditRaw, 6), 6), 'USDT'],
    ['Payments spent acquiring wNEAR', fullNumber(atomicNumber(trace.whollySwapFundedGrossUSDTSpendRaw, 6), 6), 'USDT'],
    ['Protocol fees included in that spend', fullNumber(atomicNumber(trace.whollySwapFundedUSDTExchangeFeesRaw, 6), 6), 'USDT'],
    ['wNEAR acquired with those payments', atomicFloor(trace.whollySwapFundedWNEARAcquiredRaw), 'wNEAR'],
    ['Acquired NEAR delivered to the buyback treasury', atomicFloor(trace.identifiedSwapFundedNativeDeliveryRaw), 'NEAR'],
    ['Acquired wNEAR retained after the last payout', atomicFloor(trace.identifiedWNEARAfterFinalDeliveryRaw), 'wNEAR'],
  ];
  return <ChartFrame title="Swap payments, acquisitions and treasury delivery" subtitle="23 August 2026 · one collector · Ethereum-origin USDT">
    <div className="table-scroll"><table aria-label="Swap payments, acquisitions and treasury delivery"><thead><tr><th scope="col">Stage</th><th scope="col">Quantity</th><th scope="col">Asset</th></tr></thead>
      <tbody>{rows.map(([label, value, asset]) => <tr key={label}><th scope="row">{label}</th><td>{value}</td><td>{asset}</td></tr>)}</tbody>
    </table></div>
    <p className="figure-note">Acquisition spending includes the protocol fee. Acquired tokens were either delivered as native NEAR or retained as wNEAR. Acquired quantities are rounded down to six decimals.</p>
  </ChartFrame>;
}

export function VerifiedTreasuryPayments() {
  return <ChartFrame title="Payments made by the treasury wallets" subtitle="Creation of each wallet through 2 November 2026">
    <div className="table-scroll"><table aria-label="Treasury outgoing asset payments"><thead><tr><th scope="col">Wallet</th><th scope="col">Recipient</th><th scope="col">Asset</th><th scope="col">Amount</th></tr></thead>
      <tbody>{d.treasury.movements.map(row => <tr key={`${row.wallet}-${row.asset}-${row.recipient}`}><th scope="row">{walletNames[row.wallet]}</th><td>{row.asset.startsWith('intents.near/') ? <span title={row.recipient}>{recipientName(row.recipient)}</span> : <a href={`https://nearblocks.io/address/${row.recipient}`} target="_blank" rel="noreferrer">{recipientName(row.recipient)}</a>}</td><td>{row.symbol}</td><td>{fullNumber(Number(row.amountUnits), 6)}</td></tr>)}</tbody>
    </table></div>
    <p className="figure-note">Amounts are grouped by wallet, recipient and asset. Transfers between the treasury wallets are included. Internal wNEAR moved to an account inside Intents, which has no public NEAR account page. Returned proposal deposits and refunded transaction budgets are separate from these asset payments.</p>
  </ChartFrame>;
}

export function PaidAccountRecurrence() {
  const c = d.cohort;
  const labels: Record<string, string> = {
    '1Click-referred intent': 'Accounts with a 1Click referral',
    'counterparty in a 1Click-referred execution': 'Other charged trading counterparties',
    'configured aggregation account': 'Known fee-aggregation account',
  };
  return <ChartFrame title="Repeat fee activity by account role" subtitle="Accounts charged during 1 August 2026, 12:00–12:10 UTC">
    <div className="table-scroll"><table aria-label="Repeat fee activity by account role"><thead><tr><th scope="col">Account role</th><th scope="col">Baseline accounts</th><th scope="col">Paid again in days 1–30</th><th scope="col">Paid again in days 61–90</th></tr></thead>
      <tbody>{Object.entries(c.outcomesByWitnessRole).map(([name, row]) => <tr key={name}><th scope="row">{labels[name]}</th><td>{row.baseline}</td><td>{row.windows.days1to30['verified-repeat']}</td><td>{row.windows.days61to90['verified-repeat']}</td></tr>)}</tbody>
    </table></div>
    <p className="figure-note">Follow-up periods: 2–31 August and 1–30 October 2026. A repeat requires another positive protocol fee. Baseline activity does not establish an account’s first use of Intents.</p>
  </ChartFrame>;
}
