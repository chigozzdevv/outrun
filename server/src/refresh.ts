import { LiveService } from './live/service.ts';
const service = new LiveService();
await service.start(false);
await service.refresh(true);
const s = service.snapshot;
console.log(JSON.stringify({ id: s.id, generatedAt: s.generatedAt, periods: s.operating.periods, quote: s.market, chain: { block: s.chain.blockHeight, issuanceRate: s.chain.issuanceRate }, sources: s.review.sources.map(({ id, state }) => ({ id, state })), health: s.health }, null, 2));
service.stop();
if (Object.values(s.health).some(feed => feed.error || !feed.successAt)) process.exitCode = 1;
