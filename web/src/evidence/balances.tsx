import { ChartFrame } from '../charts/views';
import { treasuryReconciliation } from './treasury-facts';
import { fullNumber } from '../../../compute/src/format';

const names: Record<string, string> = {
  'buybacks.multisignature.near': 'Buyback treasury',
  'fefundsadmin.sputnik-dao.near': 'Frontend treasury',
  '1csfundsadmin.sputnik-dao.near': '1Click treasury',
};

export function TreasuryReconciliation() {
  return <ChartFrame title="Where the native NEAR accumulated" subtitle="1 September–30 October 2026 · NEAR">
    <div className="table-scroll"><table aria-label="Treasury balances and native inflows">
      <thead><tr><th scope="col">Wallet</th><th scope="col">Inflows from outside the three wallets</th><th scope="col">Balance change</th><th scope="col">Ending balance</th></tr></thead>
      <tbody>{treasuryReconciliation.map(row => <tr key={row.wallet}><th scope="row"><a href={`https://nearblocks.io/address/${row.wallet}`} target="_blank" rel="noreferrer">{names[row.wallet]}</a></th><td>{fullNumber(row.externalNEAR)}</td><td>{fullNumber(row.increaseNEAR)}</td><td>{fullNumber(row.closingNEAR)}</td></tr>)}</tbody>
    </table></div>
    <p className="figure-note">The buyback balance includes 502,793 NEAR transferred from the 1Click treasury. That transfer cancels when the three wallets are consolidated.</p>
  </ChartFrame>;
}
