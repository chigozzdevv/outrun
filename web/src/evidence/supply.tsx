import { useLiveData } from '../live/provider';
import { periodLabel } from '../live/metrics';
import { fullNumber } from '../../../compute/src/format';
import { ChartFrame } from '../charts/views';

/** Historical supply accounting from the matched windows. */
export function SupplyEvidence() {
  const { snapshot } = useLiveData();
  const [earlier, later] = snapshot.operating.supply.windows;
  const rows = [
    ['Gross minting', fullNumber(Number(earlier.grossMintedNEAR), 3), fullNumber(Number(later.grossMintedNEAR), 3)],
    ['Derived native burns', fullNumber(Number(earlier.nativeBurnDerivedNEAR), 3), fullNumber(Number(later.nativeBurnDerivedNEAR), 3)],
    ['Net supply change', fullNumber(Number(earlier.netSupplyChangeNEAR), 3), fullNumber(Number(later.netSupplyChangeNEAR), 3)],
    ['Wallet receipts / gross minting', earlier.receiptQuantityToGrossMintingPct === null ? 'Not defined' : `${Number(earlier.receiptQuantityToGrossMintingPct).toFixed(2)}%`, later.receiptQuantityToGrossMintingPct === null ? 'Not defined' : `${Number(later.receiptQuantityToGrossMintingPct).toFixed(2)}%`],
  ];
  return <ChartFrame title="NEAR issuance and burns" subtitle="Two complete 30-day windows · quantities in NEAR">
    <div className="table-scroll"><table aria-label="Historical minting and native burns"><thead><tr><th>Metric</th><th>{periodLabel(snapshot.operating.periods[0], false)}</th><th>{periodLabel(snapshot.operating.periods[1], false)}</th></tr></thead><tbody>{rows.map(([label, a, b]) => <tr key={label}><th scope="row">{label}</th><td>{a}</td><td>{b}</td></tr>)}</tbody></table></div>
  </ChartFrame>;
}
