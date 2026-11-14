import { useLiveData } from './provider';
import { DAY } from '../../../compute/src/live/schema';

const day = (iso: string) => new Date(iso.slice(0, 10) + 'T12:00:00Z').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const time = (iso: string) => iso.length === 10 ? day(iso) : new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC';

/** Tells readers which figures are current and which stop at an earlier date. */
export function DataStatus() {
  const { snapshot: s, connection } = useLiveData();
  const through = day(new Date(Date.parse(s.operating.endExclusive) - DAY).toISOString());
  const live = s.health.market.successAt !== null || s.health.chain.successAt !== null;
  // A single failed check is retried within minutes; only a long gap means the figures stopped updating.
  const lastSuccess = s.health.operating.successAt;
  const paused = live && s.health.operating.error !== null && (lastSuccess === null || Date.now() - Date.parse(lastSuccess) > 6 * 60 * 60_000);
  return <p className="data-status">
    {live ? <>Live data: NEAR price at {time(s.market.asOf)}; supply and issuance at {time(s.chain.asOf)}.</> : <>Archived figures: NEAR price and supply as of {day(s.market.asOf)}.</>}
    {' '}Intents volume, fees and treasury receipts run through {through}{paused ? '; updates to these figures are paused' : ''}.
    {connection === 'offline' ? ' Live data is unavailable right now.' : connection === 'connecting' && !live ? ' Connecting to live data.' : ''}
  </p>;
}
