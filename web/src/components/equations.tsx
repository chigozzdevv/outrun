import { createElement, type ReactNode } from 'react';

// Native MathML keeps equations selectable and accessible without an image or CDN.
const m = (name: string, ...children: ReactNode[]) => createElement(name, null, ...children);
const v = (name: string, sub?: string, sup?: string): ReactNode => sub && sup ? m('msubsup', m('mi', name), m('mi', sub), m('mi', sup)) : sub ? m('msub', m('mi', name), m('mi', sub)) : sup ? m('msup', m('mi', name), m('mi', sup)) : m('mi', name);
const op = (s: string) => m('mo', s);
const n = (s: number) => m('mn', String(s));
const frac = (a: ReactNode, b: ReactNode) => m('mfrac', a, b);
const row = (...a: ReactNode[]) => m('mrow', ...a);
const parens = (a: ReactNode) => row(op('('), a, op(')'));
const power = (a: ReactNode, b: ReactNode) => m('msup', a, b);

function Equation({ label, children }: { label: string; children: ReactNode }) {
  return <div className="equation">{createElement('math', { xmlns: 'http://www.w3.org/1998/Math/MathML', display: 'block', 'aria-label': label }, children)}</div>;
}

export function RevenueEquation() {
  return <Equation label="Retained revenue equals the sum of route volume times its retained fee rate. Available cash equals the greater of revenue less cash costs and zero. Acquisition budget equals the allocation times available cash.">
    {m('mtable',
      m('mtr', m('mtd', row(v('R','t'), op('='), m('munder', op('∑'), v('k')), v('V','k,t'), v('τ','k,t')))),
      m('mtr', m('mtd', row(v('F','t'), op('='), m('mi','max'), parens(row(v('R','t'),op('−'),v('O','t'),op(','),n(0)))))),
      m('mtr', m('mtd', row(v('A','t'), op('='), v('a','t'), v('F','t'))))) }
  </Equation>;
}

export function FundingTraceEquation() {
  return <Equation label="The identified swap-funded NEAR purchases equal the amount delivered to the treasury plus the identified quantity retained after the final payout.">{row(v('Q'), op('='), v('T'), op('+'), v('C'))}</Equation>;
}

export function AcquisitionEquation() {
  return <Equation label="Tokens acquired equal acquisition budget divided by execution price. Purchase coverage equals acquired tokens divided by gross issuance.">{row(v('Q','t'),op('='),frac(v('A','t'),v('P','t','exec')),m('mspace'),op(';'),m('mspace'),v('K','t'),op('='),frac(v('Q','t'),v('I','t')))}</Equation>;
}

export function SupplyEquation() {
  return <Equation label="Ending supply equals opening supply plus issuance minus native burns.">{row(v('S','t'),op('='),v('S','t−1'),op('+'),v('I','t'),op('−'),v('B','t','native'))}</Equation>;
}

export function CoverageEquation() {
  return <Equation label="When valuation and execution prices match, purchase coverage equals allocation times cash availability divided by revenue multiple times issuance rate.">{row(v('K'),op('='),frac(row(v('a'),v('μ')),row(v('m'),v('r'))))}</Equation>;
}

export function ReturnEquation() {
  return <Equation label="The terminal price ratio equals the change in revenue multiple times revenue growth times the inverse change in supply.">{row(frac(v('P','T'),v('P','0')),op('='),frac(v('m','T'),v('m','0')),op('×'),frac(v('R','T'),v('R','0')),op('×'),frac(v('S','0'),v('S','T')))}</Equation>;
}

export function RequiredGrowthEquation() {
  return <Equation label="Required annual volume growth equals the target price ratio times the starting revenue multiple divided by the terminal multiple times one plus issuance rate to the power T, all to the power one over T, minus one.">{row(v('g'),op('='),power(parens(row(v('h'),frac(v('m','0'),v('m','T')),power(parens(row(n(1),op('+'),v('r'))),v('T')))),frac(n(1),v('T'))),op('−'),n(1))}</Equation>;
}

export function HolderEquation() {
  return <Equation label="A holder's supply-share ratio equals their balance growth times the inverse supply growth. Their dollar-value ratio equals balance growth times the price ratio.">{m('mtable',
    m('mtr',m('mtd',row(frac(row(v('H','T'),op('/'),v('S','T')),row(v('H','0'),op('/'),v('S','0'))),op('='),frac(v('H','T'),v('H','0')),op('×'),frac(v('S','0'),v('S','T'))))),
    m('mtr',m('mtd',row(frac(row(v('H','T'),v('P','T')),row(v('H','0'),v('P','0'))),op('='),frac(v('H','T'),v('H','0')),op('×'),frac(v('P','T'),v('P','0'))))))}</Equation>;
}
