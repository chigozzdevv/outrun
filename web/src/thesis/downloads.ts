import catalogue from '../../../data/evidence/2026-11-02/catalog.json';
import { shortDate } from '../../../compute/src/format.ts';
import { DAY, type LiveSnapshot } from '../../../compute/src/live/schema.ts';

export function downloadUrl(path: string): string {
  return catalogue.datasets.find(item => item.source.replace(/^public/, '') === path)?.download?.url ?? path;
}

export function downloadName(path: string): string {
  const live = path.match(/^\/api\/thesis\/(daily|native)\.csv\?snapshot=/);
  if (live) return live[1] === 'daily' ? 'daily-observations.csv' : 'native-receipts.csv';
  const url = downloadUrl(path);
  return catalogue.datasets.find(item => item.download?.url === url)?.download?.filename ?? url.slice(url.lastIndexOf('/') + 1);
}

export function snapshotDownload(snapshot: LiveSnapshot, kind: 'daily' | 'native') {
  const { operating } = snapshot;
  const start = (kind === 'daily' ? operating.daily[0].date : operating.nativeCoverage.start).slice(0, 10);
  const end = new Date(Date.parse(operating.endExclusive) - DAY).toISOString().slice(0, 10);
  const startYear = start.slice(0, 4), endYear = end.slice(0, 4);
  const dates = `${shortDate(start)}${startYear === endYear ? '' : ' ' + startYear}–${shortDate(end)} ${endYear}`;
  const native = kind === 'native';
  const title = native ? 'Native NEAR transfers into treasury wallets' : 'Daily settlement volume, fees and treasury receipts';
  const stem = native ? 'native-receipts' : 'daily-observations';
  return {
    href: `/api/thesis/${kind}.csv?snapshot=${encodeURIComponent(snapshot.id)}`,
    title: `${title}, ${dates}`,
    filename: `${stem}.csv`,
  };
}
