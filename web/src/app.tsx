import { useMemo } from 'react';
import { useLiveData } from './live/provider';
import { DataStatus } from './live/status';
import { scenarioAssumptions } from '../../compute/src/models/inputs';
import { runForwardModel } from '../../compute/src/models/model';
import { ChartFrame, COLORS, DailyHistory, LineChart } from './charts/views';
import { SupplyEvidence } from './evidence/supply';
import { FeesToBuyback } from './evidence/onchain';
import { ThesisText } from './thesis/essay';
import { thesisFacts } from './thesis/facts';
import { CurrentValuation, PurchaseCapacityComparison, ReverseUnderwriting } from './components/valuation';

function ChapterTitle({ title }: { title: string }) {
  return <div className="chapter-intro"><h2>{title}</h2></div>;
}

export default function App() {
  const { snapshot } = useLiveData();
  const operating = snapshot.operating;
  const baseline = useMemo(() => runForwardModel(scenarioAssumptions(snapshot)), [snapshot]);
  const answer = useMemo(() => thesisFacts(snapshot).heroAnswer, [snapshot]);

  return <div className="app">
    <a className="skip-link" href="#main">Skip to the thesis</a>

    <main id="main">
      <section className="hero">
        <div className="hero-layout"><div><h1>Can NEAR Intents<br/>outrun <em>dilution?</em></h1><p className="hero-deck">{answer}</p><DataStatus/></div></div>
        <figure className="hero-art"><img src="/images/cover.jpg" width={1672} height={941} alt="A balance scale weighs newly arriving NEAR tokens against NEAR tokens being added by hand." fetchPriority="high" decoding="async"/></figure>
      </section>

      <div className="article-shell">
        <div className="article-content">
          <section id="thesis" className="chapter chapter-thesis">
            <ChapterTitle title="The investment case"/>
            <div className="authored-thesis"><ThesisText section="opening"/></div>
            <FeesToBuyback/>
          </section>

          <section id="evidence" className="chapter">
            <ChapterTitle title="Following the money"/>
            <ThesisText section="withdrawals"/>
            <ThesisText section="balances"/>
            <ThesisText section="evidence"/>
            <SupplyEvidence/>
          </section>

          <section id="fees" className="chapter">
            <ChapterTitle title="Where Intents fees go"/>
            <ThesisText section="fees"/>
          </section>

          <section id="mechanism" className="chapter">
            <ChapterTitle title="How much NEAR the fees could buy"/>
            <ThesisText section="mechanism"/>
            <PurchaseCapacityComparison/>
            <ThesisText section="purchaseHurdle"/>
          </section>

          <section id="durability" className="chapter">
            <ChapterTitle title="Can the fee income last?"/>
            <ThesisText section="durability"/>
            <DailyHistory daily={operating.daily} nativeCoverage={operating.nativeCoverage}/>
            <ThesisText section="competition"/>
          </section>

          <section id="valuation" className="chapter">
            <ChapterTitle title="The growth today’s price needs"/>
            <CurrentValuation/>
            <ReverseUnderwriting/>
            <ThesisText section="valuation"/>
          </section>

          <section id="holders" className="chapter">
            <ChapterTitle title="What this means for NEAR holders"/>
            <ThesisText section="holders"/>
            <ChartFrame title="Staked and unstaked holders" subtitle="Modeled share of total supply · starting share = 100">
              <LineChart labels={['Start', ...baseline.rows.map(r => 'Year ' + r.year)]} yLabel="Holder supply-share index" format={n => n.toFixed(0)} includeZero={false} reference={100} series={[{ name: 'Staked', color: COLORS.green, values: [100, ...baseline.rows.map(r => r.stakerSupplyShareIndex)] }, { name: 'Unstaked', color: COLORS.orange, values: [100, ...baseline.rows.map(r => r.nonStakerSupplyShareIndex)] }]}/>
            </ChartFrame>
            <ThesisText section="issuancePolicy"/>
          </section>

          <section id="verdict" className="chapter">
            <ChapterTitle title="Our verdict, and what would change it"/>
            <div id="investment-view"><ThesisText section="closing"/></div>
          </section>

          <section id="checks" className="chapter">
            <ChapterTitle title="Check our numbers"/>
            <ThesisText section="checks"/>
          </section>

        </div>
      </div>
    </main>

  </div>;
}
