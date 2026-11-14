import { fullNumber, usd } from '../../../compute/src/format';
import { periodLabel } from '../live/metrics';
import { ChartFrame } from '../charts/views';
import { useLiveData } from '../live/provider';

export function TreasuryEvidence() {
  const { snapshot } = useLiveData();
  const period = snapshot.operating.periods[1];
  const rows = [
    ['Reported swap fees (protocol and affiliate)', usd(period.grossFeesUsd, 2)],
    ['Wallet collections (native and wrapped NEAR)', usd(period.collectionsUsd, 2)],
    ['NEAR received from outside the three treasury wallets', `${fullNumber(period.nativeReceiptsNear, 0)} NEAR`],
    ['Fees retained by each partner and the operator', 'Unknown'],
    ['Cash remaining after attributable costs', 'Unknown'],
    ['Complete acquisition spending in USD', 'Not reconciled'],
  ];

  return <ChartFrame title="Reported fees and treasury receipts" subtitle={`${periodLabel(period)} · UTC`}>
    <div className="table-scroll">
      <table aria-label="Reported fees and treasury receipts">
        <thead><tr><th scope="col">Measure</th><th scope="col">Amount</th></tr></thead>
        <tbody>{rows.map(([measure, amount]) => <tr key={measure}><th scope="row">{measure}</th><td>{amount}</td></tr>)}</tbody>
      </table>
    </div>
    <p className="figure-note"><a href="https://api.llama.fi/summary/fees/near-intents?dataType=dailyFees" target="_blank" rel="noreferrer">Fees</a> accrue on swaps. <a href="https://github.com/DefiLlama/dimension-adapters/blob/master/fees/near-intents/index.ts" target="_blank" rel="noreferrer">Collections</a> can arrive later. Both receipt measures exclude direct transfers between the three treasury wallets and overlap where they count the same native NEAR. An outside immediate sender can also return treasury-owned capital.</p>
  </ChartFrame>;
}
