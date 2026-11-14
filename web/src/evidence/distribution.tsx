import { ChartFrame } from '../charts/views';
import { interfaceRouting } from './routing-facts';
import { pct, usd } from '../../../compute/src/format';

export function InterfaceRouting() {
  return <ChartFrame title="Concentration among third-party interfaces" subtitle="Covered 1Click swaps · 4 October–2 November 2026, 13:35 UTC">
    <div className="table-scroll"><table aria-label="Covered third-party interface volume concentration"><thead><tr><th scope="col">Interface</th><th scope="col">Volume</th><th scope="col">Share of third-party volume</th></tr></thead>
      <tbody>{interfaceRouting.volumes.topFive.map(row => <tr key={row.name}><th scope="row">{row.name}</th><td>{usd(row.usd, 2)}</td><td>{pct(row.usd / interfaceRouting.volumes.thirdPartyUsd, 2)}</td></tr>)}</tbody>
    </table></div>
    <p className="figure-note">NearStats.org, CC BY 4.0; derived from its public 1Click Explorer attribution. One interface per swap; volume is valued at the larger leg. These are <a href="/data/routing/20261004-20261102/interfaces.csv" download="interfaces.csv">volume shares</a>, not shares of fee revenue.</p>
  </ChartFrame>;
}
