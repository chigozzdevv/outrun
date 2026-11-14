import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { POLICY_SOURCES } from '../../compute/src/live/schema.ts';
import { LiveService } from '../src/live/service.ts';

test('an unavailable adapter retries independently and only matching code clears the operating gate', async t => {
  const start = Date.parse('2026-11-02T12:00:00Z');
  t.mock.timers.enable({ apis: ['Date'], now: start });
  const verified = 'swap fee methodology '.repeat(10).trim();
  const digest = createHash('sha256').update(verified).digest('hex');
  const manifest = { reviewedAt: '2026-10-28', hashes: Object.fromEntries(POLICY_SOURCES.map(s => [s.id, digest])) };
  const read = fs.readFile.bind(fs);
  t.mock.method(fs, 'readFile', (...args: Parameters<typeof fs.readFile>) =>
    String(args[0]).endsWith('/server/src/live/reviewed.json') ? Promise.resolve(JSON.stringify(manifest)) : read(...args));
  syncBuiltinESMExports();
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); });

  const cacheDir = await fs.mkdtemp(join(tmpdir(), 'intent-adapter-retry-'));
  const requests: string[] = [];
  let adapterText: string | null = null;
  const service = new LiveService({ cacheDir, fetcher: async input => {
    const source = POLICY_SOURCES.find(s => s.url === String(input));
    if (!source) throw Error('Downstream feed unavailable');
    requests.push(source.id);
    if (source.id === 'adapter' && adapterText === null) return new Response(null, { status: 503 });
    return new Response(source.id === 'adapter' ? adapterText : verified);
  } });
  t.after(async () => { service.stop(); await fs.rm(cacheDir, { recursive: true, force: true }); });
  await service.start(false);
  const before = structuredClone(service.snapshot.operating);
  const adapter = () => service.snapshot.review.sources.find(s => s.id === 'adapter')!;
  const check = async (minutes: number) => {
    t.mock.timers.setTime(start + minutes * 60_000);
    requests.length = 0;
    await service.refresh();
    return [...requests].sort();
  };
  const full = POLICY_SOURCES.map(s => s.id).sort();
  assert.deepEqual(await check(0), full);
  assert.equal(adapter().state, 'unavailable');
  const fullHealth = structuredClone(service.snapshot.health.documents);
  const otherSources = service.snapshot.review.sources.filter(s => s.id !== 'adapter');
  assert.deepEqual(await check(1), []);
  assert.deepEqual(await check(5), ['adapter']);
  assert.equal(adapter().state, 'unavailable');
  assert.match(service.snapshot.health.operating.error!, /methodology awaits source verification/);

  adapterText = verified;
  assert.deepEqual(await check(10), ['adapter']);
  assert.equal(adapter().currentHash, digest);
  assert.equal(adapter().state, 'unchanged');
  assert.equal(adapter().checkedAt, new Date(start + 10 * 60_000).toISOString());
  assert.deepEqual(service.snapshot.review.sources.filter(s => s.id !== 'adapter'), otherSources);
  assert.deepEqual(service.snapshot.health.documents, fullHealth);
  assert.equal(service.snapshot.health.operating.error, 'Downstream feed unavailable');
  assert.deepEqual(service.snapshot.operating, before);

  adapterText = verified + ' changed';
  assert.deepEqual(await check(60), full);
  assert.equal(adapter().state, 'changed');
  assert.deepEqual(await check(65), []);
  assert.match(service.snapshot.health.operating.error!, /methodology awaits source verification/);
  assert.deepEqual(service.snapshot.operating, before);

  delete manifest.hashes.adapter;
  adapterText = verified;
  assert.deepEqual(await check(120), full);
  assert.equal(adapter().state, 'unreviewed');
  assert.match(service.snapshot.health.operating.error!, /methodology awaits source verification/);
});
