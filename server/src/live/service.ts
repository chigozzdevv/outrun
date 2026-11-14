import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, rename, open, unlink } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { DAY, POLICY_SOURCES, REVIEWED_PROTOCOL_VERSIONS, WALLETS, type Daily, type FeedHealth, type LiveSnapshot, type Period, type SupplyWindow } from '../../../compute/src/live/schema.ts';
import { fallbackSnapshot, validateSnapshot } from '../../../compute/src/live/snapshot.ts';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const CACHE = resolve(ROOT, '.cache/live-thesis');
const RPC = process.env.NEAR_RPC_URL || 'https://rpc.mainnet.near.org';
const ARCHIVE = process.env.NEAR_ARCHIVE_URL || 'https://archival-rpc.mainnet.fastnear.com';
const NEARDATA = 'https://mainnet.neardata.xyz/v0/block';
const USDC = '17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1';
const OPERATING_INTERVAL = 5 * 60_000;
const CHAIN_INTERVAL = 60_000;
const DOCUMENT_INTERVAL = 60 * 60_000;
const YOCTO = 10n ** 24n;
const nowIso = () => new Date().toISOString();
const hash = (text: string) => createHash('sha256').update(text).digest('hex');
const dayOf = (time: number) => new Date(time).toISOString().slice(0, 10);
const ns = (date: string) => BigInt(Date.parse(date)) * 1_000_000n;
const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const finite = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x) && x >= 0;
export function units(input: string, decimals = 24): bigint {
  const m = input.match(/^(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/);
  if (!m || input.length > 120) throw Error('Invalid native amount');
  const fraction = m[2] || '', shift = decimals + Number(m[3] || 0) - fraction.length;
  if (shift < 0 || shift > 100) throw Error('Native amount precision exceeds the source unit');
  return BigInt(m[1] + fraction) * 10n ** BigInt(shift);
}
export function native(n: bigint): string {
  const sign = n < 0n ? '-' : '', a = n < 0n ? -n : n;
  return sign + (a / YOCTO).toString() + ((a % YOCTO) ? '.' + (a % YOCTO).toString().padStart(24, '0').replace(/0+$/, '') : '');
}
export function losslessReceipts(text: string): unknown {
  return JSON.parse(text, (key: string, value: unknown, context?: { source?: string }) => {
    if (['deposit','block_timestamp'].includes(key) && typeof value === 'number') {
      if (!context?.source) throw Error('This Node version cannot preserve receipt precision');
      return context.source;
    }
    return value;
  });
}
export function parseSeries(payload: unknown, now: number): Map<string, number> {
  const rows = (payload as { totalDataChart?: unknown })?.totalDataChart;
  if (!Array.isArray(rows) || rows.length < 60) throw Error('Daily source is incomplete');
  const result = new Map<string, number>();
  for (const row of rows) {
    if (!Array.isArray(row) || row.length !== 2 || !Number.isSafeInteger(row[0]) || !finite(row[1]) || row[0] % 86400 !== 0) throw Error('Daily source schema changed');
    const date = dayOf(row[0] * 1000);
    if (result.has(date)) throw Error('Duplicate daily observation');
    if (row[0] * 1000 < Math.floor(now / DAY) * DAY) result.set(date, row[1]);
  }
  return result;
}
/** A calendar buffer is a publication policy, not a claim of upstream finality. */
export function reportingWindow(series: Map<string, number>[], now: number) {
  const bufferDays = 2;
  const cutoff = Math.floor(now / DAY) * DAY - bufferDays * DAY;
  const common = [...series[0].keys()].filter(date => series.every(values => values.has(date))).sort();
  const latestAvailableDate = common.at(-1);
  const last = common.filter(date => Date.parse(date) + DAY <= cutoff).at(-1);
  if (!last || !latestAvailableDate || now - Date.parse(latestAvailableDate) > 4 * DAY || now - Date.parse(last) > 6 * DAY) throw Error('Daily sources have not delivered a current reporting window');
  return { bufferDays, latestAvailableDate, last };
}

export function revisedDailyDates(previous: Daily[], next: Daily[]): string[] {
  const before = new Map(previous.map(row => [row.date, row]));
  return next.filter(row => {
    const earlier = before.get(row.date);
    return earlier && (['volumeUsd','grossFeesUsd','collectionsUsd'] as const).some(key => earlier[key] !== null && row[key] !== null && earlier[key] !== row[key]);
  }).map(row => row.date);
}

export function receiptReplayRanges(start: string, end: string, coveredStart: string, coveredEnd: string, schema?: number) {
  if (schema !== 3) return [{ start, end }];
  const overlap = dayOf(Math.max(Date.parse(start), Math.min(Date.parse(end), Date.parse(coveredEnd)) - 2 * DAY));
  const ranges = [{ start: overlap, end }];
  if (start < coveredStart) ranges.unshift({ start, end: coveredStart < end ? coveredStart : end });
  return ranges;
}
export function normalizeDocument(text: string): string {
  if (!/<(?:!doctype|html|head|body|main|article)\b/i.test(text)) return text.replace(/\s+/g, ' ').trim();
  return text.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}
interface Header { height: number; hash: string; prev_hash: string; prev_height?: number; timestamp_nanosec: string; total_supply: string; epoch_id: string; next_epoch_id: string; latest_protocol_version: number }
interface Block { header: Header; chunks: { height_included: number; balance_burnt: string }[] }
type StakeObservation = Required<Pick<LiveSnapshot['chain'], 'activeValidatorStakeNear' | 'validatorEpochId' | 'validatorEpochStartHeight'>>;
type EpochBlock = Pick<Header, 'height' | 'hash' | 'prev_hash' | 'epoch_id' | 'next_epoch_id' | 'total_supply'>;
export function epochStakeObservation(info: { epoch_start_height: number; current_validators: unknown }, final: EpochBlock, first: EpochBlock, before: EpochBlock): StakeObservation {
  if (!Number.isSafeInteger(info.epoch_start_height) || first.height !== info.epoch_start_height || first.height > final.height ||
    first.epoch_id !== final.epoch_id || before.epoch_id === first.epoch_id || before.next_epoch_id !== first.epoch_id ||
    first.prev_hash !== before.hash || before.height >= first.height) throw Error('Validator set and finalized block do not share an epoch boundary');
  const validators = info.current_validators;
  if (!Array.isArray(validators) || validators.length === 0) throw Error('Missing current validators');
  const accounts = new Set<string>();
  let stake = 0n;
  for (const row of validators) {
    if (typeof row?.account_id !== 'string' || !row.account_id || accounts.has(row.account_id) || typeof row.stake !== 'string' || !/^[1-9]\d*$/.test(row.stake)) throw Error('Invalid or duplicate validator stake');
    accounts.add(row.account_id);
    stake += BigInt(row.stake);
  }
  if (stake > BigInt(final.total_supply)) throw Error('Active validator stake exceeds finalized supply');
  return { activeValidatorStakeNear: Number(stake) / 1e24, validatorEpochId: final.epoch_id, validatorEpochStartHeight: first.height };
}
interface Payout { height: number; hash: string; predecessorHeight: number; predecessorHash: string; epochId: string; nextEpochId: string; timestampNs: string; grossMintedYocto: string; latestProtocolVersionReportedByBlock: number }
export interface Receipt { id: string; timestampNs: string; block: number; predecessor: string; receiver: string; amountYocto: string; transactionHash: string }
export function validateNativeLedger(snapshot: LiveSnapshot, receipts: Receipt[]) {
  if (new Set(receipts.map(row => row.id)).size !== receipts.length) throw Error('Duplicate native receipt identifiers');
  for (const window of snapshot.operating.supply.windows) {
    const total = receipts.filter(row => BigInt(row.timestampNs) >= ns(window.startInclusiveUTC) && BigInt(row.timestampNs) < ns(window.endExclusiveUTC) && WALLETS.includes(row.receiver as typeof WALLETS[number]) && !WALLETS.includes(row.predecessor as typeof WALLETS[number])).reduce((sum, row) => sum + BigInt(row.amountYocto), 0n);
    if (total !== units(window.externalNativeWalletReceiptsNEAR)) throw Error('Native source ledger differs from the exact published quantity');
  }
}
interface CollectorCache { receipts: Receipt[]; receiptEnd: string; receiptStart?: string; receiptSchema?: number; payouts: Payout[]; headers: Record<string, Block> }
interface SourceRecord { url: string; checkedAt: string; sha256: string; bytes: number; method: string }

export class LiveService {
  snapshot: LiveSnapshot = fallbackSnapshot();
  private state: CollectorCache = { receipts: [], receiptEnd: '2026-10-22', payouts: [], headers: {} };
  private timer: ReturnType<typeof setInterval> | null = null;
  private running: Promise<void> | null = null;
  private records: SourceRecord[] = [];
  private nextIndexerRequest = 0;
  private versions = new Map<string, { snapshot: LiveSnapshot; receipts: Receipt[] }>();
  private cacheDir: string;
  private fetcher: typeof fetch;
  constructor(options: { cacheDir?: string; fetcher?: typeof fetch } = {}) { this.cacheDir = options.cacheDir ?? CACHE; this.fetcher = options.fetcher ?? fetch; }
  private starting: Promise<void> | null = null;
  private closed = false;

  start(autoRefresh = true): Promise<void> {
    this.starting ??= this.initialize(autoRefresh);
    return this.starting;
  }
  private async initialize(autoRefresh: boolean) {
    await mkdir(resolve(this.cacheDir, 'sources'), { recursive: true });
    try { const saved = JSON.parse(await readFile(resolve(this.cacheDir, 'snapshot.json'), 'utf8')); validateSnapshot(saved); this.snapshot = saved; } catch { /* A cold start uses the explicitly dated preserved snapshot. */ }
    try { this.state = JSON.parse(await readFile(resolve(this.cacheDir, 'collector.json'), 'utf8')); } catch { await this.bootstrap(); }
    this.remember();
    if (autoRefresh) void this.refresh();
    this.timer = setInterval(() => { void this.refresh(); }, CHAIN_INTERVAL);
    this.timer.unref();
  }
  stop() { this.closed = true; if (this.timer) clearInterval(this.timer); }
  async refresh(force = false) {
    if (this.closed) return;
    if (this.running) return this.running;
    this.running = this.withWriterLock(force).finally(() => { this.running = null; });
    return this.running;
  }
  private async withWriterLock(force: boolean): Promise<void> {
    const lockPath = resolve(this.cacheDir, 'refresh.lock');
    let lock;
    try { lock = await open(lockPath, 'wx'); await lock.writeFile(JSON.stringify({ pid: process.pid, startedAt: Date.now() })); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
      try {
        const owner = JSON.parse(await readFile(lockPath, 'utf8'));
        let alive = true;
        try { process.kill(owner.pid, 0); } catch { alive = false; }
        if (!alive) { await unlink(lockPath); return this.withWriterLock(force); }
        const saved = JSON.parse(await readFile(resolve(this.cacheDir, 'snapshot.json'), 'utf8')); validateSnapshot(saved); this.snapshot = saved;
      } catch { /* Another process is still initializing its verified snapshot. */ }
      return;
    }
    try {
      // A second server process may have advanced the common verified cache.
      try { const saved = JSON.parse(await readFile(resolve(this.cacheDir, 'snapshot.json'), 'utf8')); validateSnapshot(saved); if (Date.parse(saved.generatedAt) > Date.parse(this.snapshot.generatedAt)) { this.snapshot = saved; this.state = JSON.parse(await readFile(resolve(this.cacheDir, 'collector.json'), 'utf8')); } } catch { /* The initial snapshot is preserved in the application. */ }
      await this.collect(force);
    } finally { await lock.close(); await unlink(lockPath).catch(() => {}); }
  }
  private async atomic(name: string, value: unknown) {
    const path = resolve(this.cacheDir, name), temp = path + '.tmp';
    await writeFile(temp, JSON.stringify(value)); await rename(temp, path);
  }
  private remember() {
    this.versions.set(this.snapshot.id, { snapshot: this.snapshot, receipts: [...this.state.receipts] });
    while (this.versions.size > 20) this.versions.delete(this.versions.keys().next().value!);
  }
  private async bootstrap() {
    const ledger = JSON.parse(await readFile(resolve(ROOT, 'data/evidence/2026-11-02/seeds/supply-ledger.json'), 'utf8'));
    this.state.payouts = ledger.rows;
    const endpoints = JSON.parse(await readFile(resolve(ROOT, 'data/evidence/2026-11-02/seeds/supply-endpoints.json'), 'utf8'));
    for (const pair of Object.values(endpoints) as { lastBlockStrictlyBefore: Header; firstBlockAtOrAfter: Header }[]) for (const h of [pair.lastBlockStrictlyBefore, pair.firstBlockAtOrAfter]) this.state.headers[h.hash] = { header: h, chunks: [] };
    const rows = (await readFile(resolve(ROOT, 'data/evidence/2026-11-02/seeds/native-receipts.csv'), 'utf8')).trim().split(/\r?\n/);
    for (const line of rows.slice(1)) {
      const [, , utc, block, id, tx, predecessor, receiver, amount] = line.split(',');
      if (!id || !WALLETS.includes(receiver as typeof WALLETS[number])) throw Error('Invalid preserved receipt');
      this.state.receipts.push({ id, timestampNs: (BigInt(Date.parse(utc)) * 1_000_000n).toString(), block: Number(block), predecessor, receiver, amountYocto: units(amount).toString(), transactionHash: tx });
    }
  }
  private async request(url: string, body?: unknown, lossless = false): Promise<unknown> {
    if (this.closed) throw Error('Updater stopped');
    // Resume a rate-limited cold backfill from recently captured, closed-period pages.
    if (this.state.receiptSchema !== 3 && url.includes('/v3/accounts/') && url.includes('/receipts?')) {
      try {
        const saved = JSON.parse(await readFile(resolve(this.cacheDir, 'sources', hash(url + JSON.stringify(body || '')) + '.json'), 'utf8'));
        if (Date.now() - Date.parse(saved.checkedAt) < 30 * 60_000 && hash(saved.body) === saved.sha256) { this.records.push({ url, checkedAt: saved.checkedAt, sha256: saved.sha256, bytes: saved.bytes, method: 'GET' }); return JSON.parse(saved.body); }
      } catch { /* Resume through the public API when no verified page is cached. */ }
    }
    const indexer = url.startsWith('https://api.nearblocks.io/');
    let response: Response | undefined;
    for (let attempt = 0; attempt < 3; attempt++) {
      if (indexer) { await pause(Math.max(0, this.nextIndexerRequest - Date.now())); this.nextIndexerRequest = Date.now() + (process.env.NEARBLOCKS_API_KEY ? 1500 : 7000); }
      response = await this.fetcher(url, { method: body ? 'POST' : 'GET', headers: { 'User-Agent': 'IntentThesis/1.0 public-data research', ...(body ? { 'Content-Type': 'application/json' } : {}), ...(process.env.NEARBLOCKS_API_KEY && url.startsWith('https://api.nearblocks.io/') ? { Authorization: `Bearer ${process.env.NEARBLOCKS_API_KEY}` } : {}) }, body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(20_000) });
      if (response.status !== 429 && response.status < 500) break;
      await response.body?.cancel();
      if (response.status === 429 && indexer) this.nextIndexerRequest = Date.now() + Math.max(60_000, Number(response.headers.get('retry-after') || 0) * 1000);
      if (attempt < 2 && !indexer) await pause(Math.min(15_000, Math.max(2000 * 2 ** attempt, Number(response.headers.get('retry-after') || 0) * 1000)));
    }
    if (!response?.ok) throw Error(`Source returned ${response?.status ?? 'no response'}: ${new URL(url).hostname}`);
    const text = await response.text();
    if (text.length > 8_000_000) throw Error('Source response exceeds the supported size');
    const record = { url, checkedAt: nowIso(), sha256: hash(text), bytes: Buffer.byteLength(text), method: body ? 'POST' : 'GET' };
    this.records.push(record);
    // Keep the most recent response for each exact request; its digest is listed in source-manifest.json.
    const request = body as { method?: string; params?: Record<string, unknown> } | undefined;
    const keyBody = request && ['query', 'EXPERIMENTAL_protocol_config'].includes(request.method ?? '') ? { ...request, params: { ...request.params, block_id: 'latest-finalized-snapshot' } } : body;
    await writeFile(resolve(this.cacheDir, 'sources', hash(url + JSON.stringify(keyBody || '')) + '.json'), JSON.stringify({ ...record, request: body ?? null, body: text }));
    return lossless ? losslessReceipts(text) : JSON.parse(text);
  }
  private async rpc(method: string, params: unknown, archive = false): Promise<any> {
    const x = await this.request(archive ? ARCHIVE : RPC, { jsonrpc: '2.0', id: 'intent-thesis-readonly', method, params }) as { result?: unknown; error?: { message?: string } };
    if (x.error || !x.result) throw Error(`RPC ${method}: ${x.error?.message ?? 'missing result'}`);
    return x.result;
  }
  private validateBlock(x: any): Block {
    const b = x.result ?? x;
    if (!b.header || !Number.isSafeInteger(b.header.height) || !/^\d+$/.test(b.header.total_supply) || !/^\d+$/.test(b.header.timestamp_nanosec) || typeof b.header.hash !== 'string' || !Array.isArray(b.chunks)) throw Error('Block schema changed');
    const h = b.header;
    if (b.chunks.some((c: any) => !Number.isSafeInteger(c.height_included) || typeof c.balance_burnt !== 'string' || !/^\d+$/.test(c.balance_burnt)) || !Number.isSafeInteger(h.latest_protocol_version) || ![h.hash,h.prev_hash,h.epoch_id,h.next_epoch_id].every(value => typeof value === 'string' && value.length > 0)) throw Error('Block accounting schema changed');
    const block = { header: { height: h.height, hash: h.hash, prev_hash: h.prev_hash, prev_height: h.prev_height, timestamp_nanosec: h.timestamp_nanosec, total_supply: h.total_supply, epoch_id: h.epoch_id, next_epoch_id: h.next_epoch_id, latest_protocol_version: h.latest_protocol_version }, chunks: b.chunks.map((c: any) => ({ height_included: c.height_included, balance_burnt: c.balance_burnt })) };
    this.state.headers[h.hash] = block;
    return block;
  }
  private async block(id: number | string): Promise<Block> {
    const cached = typeof id === 'string' ? this.state.headers[id] : Object.values(this.state.headers).find(b => b.header.height === id);
    // An endpoint seed has no chunks. It is sufficient as an endpoint, but payout blocks are fetched in extendPayouts.
    if (cached) return cached;
    const bind = (value: unknown) => { const block = this.validateBlock(value); if (typeof id === 'number' ? block.header.height !== id : block.header.hash !== id) throw Error('Block response does not match its requested identity'); return block; };
    try { return bind(await this.request(`${NEARDATA}/${id}/headers`)); }
    catch { return bind(await this.rpc('block', { block_id: id }, true)); }
  }
  private async market(): Promise<LiveSnapshot['market']> {
    const url = 'https://coins.llama.fi/prices/current/coingecko:near';
    const x = await this.request(url) as { coins?: Record<string, { price: number; timestamp: number; confidence?: number }> };
    const coin = x.coins?.['coingecko:near'];
    if (!coin || !finite(coin.price) || coin.price === 0 || !finite(coin.timestamp) || (Date.now() - coin.timestamp * 1000 > 2 * 60 * 60_000 || coin.timestamp * 1000 - Date.now() > 5 * 60_000) || (coin.confidence !== undefined && coin.confidence < .9)) throw Error('Price quote is missing, stale or low confidence');
    return { priceUsd: coin.price, asOf: new Date(coin.timestamp * 1000).toISOString(), source: url };
  }
  private async validatorStake(h: Header): Promise<Partial<StakeObservation>> {
    const { activeValidatorStakeNear, validatorEpochId, validatorEpochStartHeight } = this.snapshot.chain;
    if (validatorEpochId === h.epoch_id && activeValidatorStakeNear !== undefined && validatorEpochStartHeight !== undefined && validatorEpochStartHeight <= h.height && activeValidatorStakeNear <= Number(h.total_supply) / 1e24) {
      return { activeValidatorStakeNear, validatorEpochId, validatorEpochStartHeight };
    }
    // The RPC set is identified by epoch, not sampled at an individual block.
    const info = await this.rpc('validators', { epoch_id: h.epoch_id });
    if (!Number.isSafeInteger(info.epoch_start_height) || info.epoch_start_height <= 0 || info.epoch_start_height > h.height) throw Error('Invalid validator epoch start');
    const first = (await this.block(info.epoch_start_height)).header;
    const before = (await this.block(first.prev_hash)).header;
    return epochStakeObservation(info, h, first, before);
  }
  private async chain(): Promise<{ value: LiveSnapshot['chain']; block: Block }> {
    const final = this.validateBlock(await this.rpc('block', { finality: 'final' }));
    const h = final.header;
    if (Math.abs(Date.now() - Number(BigInt(h.timestamp_nanosec) / 1_000_000n)) > 10 * 60_000) throw Error('Finalized chain observation is stale');
    const [config, stake, ...accounts] = await Promise.all([
      this.rpc('EXPERIMENTAL_protocol_config', { block_id: h.hash }),
      this.validatorStake(h).catch(() => ({})), // Use the labeled scenario assumption if the epoch observation cannot be verified.
      ...WALLETS.map(account => this.rpc('query', { request_type: 'view_account', account_id: account, block_id: h.hash })),
    ]);
    const ratio = (x: unknown) => { if (!Array.isArray(x) || x.length !== 2 || !x.every(finite) || x[1] === 0 || x[0] > x[1]) throw Error('Invalid protocol ratio'); return x[0] / x[1]; };
    if (config.chain_id !== 'mainnet') throw Error('Wrong chain');
    let frontendUsdc: number | null = null;
    try { const value = await this.rpc('query', { request_type: 'call_function', account_id: USDC, method_name: 'ft_balance_of', args_base64: Buffer.from(JSON.stringify({ account_id: WALLETS[1] })).toString('base64'), block_id: h.hash }); const amount = JSON.parse(Buffer.from(value.result).toString()); if (!/^\d+$/.test(amount)) throw Error('Invalid token balance'); frontendUsdc = Number(amount) / 1e6; } catch { /* Unavailable remains unknown, never a zero. */ }
    return { block: final, value: { asOf: new Date(Number(BigInt(h.timestamp_nanosec) / 1_000_000n)).toISOString(), blockHeight: h.height, blockHash: h.hash, supplyNear: Number(h.total_supply) / 1e24, issuanceRate: ratio(config.max_inflation_rate), stakerIssuanceShare: 1 - ratio(config.protocol_reward_rate), protocolVersion: config.protocol_version, frontendUsdc, ...stake, wallets: accounts.map((account, i) => { if (!/^\d+$/.test(account.amount) || account.block_hash !== h.hash) throw Error('Wallet snapshot does not share the finalized block'); return { account: WALLETS[i], nativeNear: Number(account.amount) / 1e24, codeHash: account.code_hash }; }) } };
  }
  private async extendReceipts(start: string, end: string) {
    const coveredStart = this.state.receiptStart ?? this.snapshot.operating.nativeCoverage.start;
    const ranges = receiptReplayRanges(start, end, coveredStart, this.state.receiptEnd, this.state.receiptSchema);
    const upper = ns(end);
    const tip = await this.request('https://api.nearblocks.io/v3/blocks/latest?limit=1') as { data?: { block_timestamp: string }[] };
    if (!tip.data?.length || BigInt(tip.data[0].block_timestamp) < upper) throw Error('Receipt indexer has not reached the end of the comparison window');
    const added = new Map<string, Receipt>();
    for (const range of ranges) {
    const lower = ns(range.start), upper = ns(range.end);
    for (const account of WALLETS) {
      let cursor: string | null = null, complete = false;
      const cursors = new Set<string>();
      let previousTimestamp = upper;
      for (let page = 0; page < 120; page++) {
        const url = `https://api.nearblocks.io/v3/accounts/${account}/receipts?limit=100&before_ts=${upper}${cursor ? `&next=${encodeURIComponent(cursor)}` : ''}`;
        const payload = await this.request(url) as { data: any[]; meta?: { next_page?: string } };
        if (!Array.isArray(payload.data)) throw Error('Receipt source schema changed');
        let before = false;
        for (const r of payload.data) {
          const timestamp = String(r.block?.block_timestamp ?? '');
          if (!/^\d+$/.test(timestamp) || !Array.isArray(r.actions) || typeof r.receipt_id !== 'string' || typeof r.outcome?.status !== 'boolean') throw Error('Incomplete indexed receipt');
          if (BigInt(timestamp) > previousTimestamp) throw Error('Receipt ordering changed during pagination');
          previousTimestamp = BigInt(timestamp);
          if (BigInt(timestamp) < lower) { before = true; continue; }
          if (BigInt(timestamp) >= upper || r.receiver_account_id !== account || r.outcome.status !== true || !r.actions.some((action: any) => action.action === 'TRANSFER')) continue;
          if (!r.actions.every((action: any) => action.action === 'TRANSFER')) throw Error('A mixed native-transfer receipt requires full-action verification');
          if (typeof r.actions_agg?.deposit !== 'string') throw Error('Exact native transfer quantity is unavailable');
          const amount = units(r.actions_agg.deposit, 0);
          if (amount === 0n) continue;
          const row: Receipt = { id: r.receipt_id, timestampNs: timestamp, block: Number(r.block.block_height), predecessor: r.predecessor_account_id, receiver: account, amountYocto: amount.toString(), transactionHash: r.transaction_hash };
          const previous = added.get(row.id);
          if (previous && JSON.stringify(previous) !== JSON.stringify(row)) throw Error('Conflicting receipt identifiers');
          added.set(row.id, row);
        }
        const next = payload.meta?.next_page;
        if (before || payload.data.length === 0 || !next) { complete = true; break; }
        if (cursors.has(next)) throw Error('Receipt cursor repeated before coverage was complete');
        cursors.add(next); cursor = next;
        await pause(1400);
      }
      if (!complete) throw Error('Receipt history exceeded the verified pagination limit');
      await pause(1400);
    }
    }
    // Replace both verified intervals, including a prefix when publication moves backward.
    const old = new Map(this.state.receipts.filter(r => !ranges.some(range => BigInt(r.timestampNs) >= ns(range.start) && BigInt(r.timestampNs) < ns(range.end))).map(r => [r.id, r]));
    for (const row of added.values()) old.set(row.id, row);
    this.state.receipts = [...old.values()].filter(r => BigInt(r.timestampNs) >= ns(start)).sort((a, b) => Number(BigInt(a.timestampNs) - BigInt(b.timestampNs)));
    this.state.receiptStart = start; this.state.receiptEnd = end > this.state.receiptEnd ? end : this.state.receiptEnd; this.state.receiptSchema = 3;
  }
  private async extendPayouts(final: Block, firstDate: string) {
    if (!REVIEWED_PROTOCOL_VERSIONS.includes(final.header.latest_protocol_version)) throw Error('Supply accounting requires review for the new protocol version');
    let predecessorHash = final.header.next_epoch_id;
    const existing = new Map(this.state.payouts.map(row => [row.predecessorHash, row]));
    const newRows: Payout[] = [];
    for (let count = 0; count < 1000; count++) {
      const known = existing.get(predecessorHash);
      if (known && BigInt(known.timestampNs) < ns(firstDate)) break;
      // Previously verified epoch links let us traverse the covered ledger without
      // fetching hundreds of old headers when only an earlier prefix is missing.
      if (known) { predecessorHash = known.epochId; continue; }
      const before = await this.block(predecessorHash);
      let first: Block | null = null;
      for (let offset = 1; offset <= 32; offset++) {
        try { const candidate = await this.block(before.header.height + offset); if (candidate.header.prev_hash === before.header.hash) { first = candidate; break; } } catch { /* A skipped height has no block. */ }
      }
      if (!first || first.header.epoch_id === before.header.epoch_id || first.header.next_epoch_id !== before.header.hash) throw Error('Epoch payout boundary does not link');
      if (!REVIEWED_PROTOCOL_VERSIONS.includes(first.header.latest_protocol_version)) throw Error('Supply accounting requires review for the new protocol version');
      const burnt = first.chunks.filter(c => c.height_included === first!.header.height).reduce((sum, c) => sum + BigInt(c.balance_burnt), 0n);
      const minted = BigInt(first.header.total_supply) - BigInt(before.header.total_supply) + burnt;
      if (minted < 0n) throw Error('Negative gross minting at an epoch boundary');
      newRows.push({ height: first.header.height, hash: first.header.hash, predecessorHeight: before.header.height, predecessorHash: before.header.hash, epochId: first.header.epoch_id, nextEpochId: first.header.next_epoch_id, timestampNs: first.header.timestamp_nanosec, grossMintedYocto: minted.toString(), latestProtocolVersionReportedByBlock: first.header.latest_protocol_version });
      if (BigInt(first.header.timestamp_nanosec) < ns(firstDate)) break;
      predecessorHash = before.header.next_epoch_id;
      if (count === 999) throw Error('Epoch history incomplete');
    }
    const ordered = [...this.state.payouts, ...newRows].sort((a, b) => a.height - b.height);
    const preceding = ordered.filter(row => BigInt(row.timestampNs) < ns(firstDate)).at(-1);
    if (!preceding) throw Error('Payout coverage does not precede the comparison start');
    this.state.payouts = [...(preceding ? [preceding] : []), ...ordered.filter(row => BigInt(row.timestampNs) >= ns(firstDate))];
    for (let i = 1; i < this.state.payouts.length; i++) {
      const a = this.state.payouts[i - 1], b = this.state.payouts[i];
      if (b.epochId !== a.predecessorHash) throw Error('A payout epoch is missing from the minting ledger');
    }
  }
  private async endpoint(date: string, final: Block): Promise<Header> {
    const target = ns(date);
    const known = [...Object.values(this.state.headers).map(b => ({ height: b.header.height, timestamp: BigInt(b.header.timestamp_nanosec) })), ...this.state.payouts.map(p => ({ height: p.height, timestamp: BigInt(p.timestampNs) }))].sort((a, b) => a.height - b.height);
    const lower = known.filter(h => h.timestamp < target).at(-1), upper = known.find(h => h.timestamp >= target) ?? { height: final.header.height, timestamp: BigInt(final.header.timestamp_nanosec) };
    if (!lower || upper.timestamp < target) throw Error('Supply endpoint cannot be bracketed');
    let lo = lower.height, hi = upper.height;
    while (hi - lo > 1) {
      const mid = Math.floor((hi + lo) / 2);
      let b: Block;
      try { b = await this.block(mid); }
      catch {
        let next: Block | null = null;
        for (let offset = 1; offset < 16 && mid + offset <= hi; offset++) { try { next = await this.block(mid + offset); break; } catch { /* Skip absent heights. */ } }
        if (!next) throw Error('Could not resolve a skipped block height'); b = next;
      }
      if (BigInt(b.header.timestamp_nanosec) < target) lo = b.header.height;
      else {
        const before = await this.block(b.header.prev_hash);
        if (BigInt(before.header.timestamp_nanosec) < target) return before.header;
        hi = before.header.height;
      }
    }
    const before = await this.block(lo), after = await this.block(hi);
    if (after.header.prev_hash !== before.header.hash || BigInt(before.header.timestamp_nanosec) >= target || BigInt(after.header.timestamp_nanosec) < target) throw Error('Supply endpoint is not an adjacent UTC boundary');
    return before.header;
  }
  private async operating(final: Block): Promise<LiveSnapshot['operating']> {
    const now = Date.now();
    const [vol, fees, collections] = await Promise.all([
      this.request('https://api.llama.fi/summary/dexs/near-intents'),
      this.request('https://api.llama.fi/summary/fees/near-intents?dataType=dailyFees'),
      this.request('https://api.llama.fi/summary/fees/near-intents?dataType=dailyRevenue'),
    ]).then(rows => rows.map(row => parseSeries(row, now)));
    const { last, ...reporting } = reportingWindow([vol, fees, collections], now);
    const end = dayOf(Date.parse(last) + DAY), start = dayOf(Date.parse(end) - 60 * DAY), middle = dayOf(Date.parse(end) - 30 * DAY);
    await this.extendReceipts(start, end);
    await this.extendPayouts(final, start);
    const dailyNative = new Map<string, bigint>();
    for (const receipt of this.state.receipts) if (!WALLETS.includes(receipt.predecessor as typeof WALLETS[number])) { const day = dayOf(Number(BigInt(receipt.timestampNs) / 1_000_000n)); dailyNative.set(day, (dailyNative.get(day) ?? 0n) + BigInt(receipt.amountYocto)); }
    const daily: Daily[] = [];
    const firstDate = [...new Set([...vol.keys(), ...fees.keys(), ...collections.keys()])].sort()[0];
    for (let time = Date.parse(firstDate); time < Date.parse(end); time += DAY) { const date = dayOf(time); daily.push({ date, volumeUsd: vol.get(date) ?? null, grossFeesUsd: fees.get(date) ?? null, collectionsUsd: collections.get(date) ?? null, nativeReceiptsNear: date >= start ? Number(native(dailyNative.get(date) ?? 0n)) : null, priceUsd: null }); }
    const period = (a: string, b: string, days: number): Period => {
      const rows = daily.filter(row => row.date >= a && row.date < b);
      if (rows.length !== days || rows.some(row => row.volumeUsd === null || row.grossFeesUsd === null || row.collectionsUsd === null)) throw Error('A comparison window contains missing observations');
      const matching = this.state.receipts.filter(r => BigInt(r.timestampNs) >= ns(a) && BigInt(r.timestampNs) < ns(b) && !WALLETS.includes(r.predecessor as typeof WALLETS[number]));
      return { start: a, endExclusive: b, days, volumeUsd: rows.reduce((s, r) => s + r.volumeUsd!, 0), grossFeesUsd: rows.reduce((s, r) => s + r.grossFeesUsd!, 0), collectionsUsd: rows.reduce((s, r) => s + r.collectionsUsd!, 0), nativeReceiptsNear: Number(native(matching.reduce((s, r) => s + BigInt(r.amountYocto), 0n))), wallets: WALLETS.map(account => { const r = matching.filter(r => r.receiver === account); return { account, receiptCount: r.length, amountNear: Number(native(r.reduce((s, r) => s + BigInt(r.amountYocto), 0n))) }; }) };
    };
    const periods = [period(start, middle, 30), period(middle, end, 30)];
    const endpoints = await Promise.all([start, middle, end].map(date => this.endpoint(date, final)));
    const windows: SupplyWindow[] = periods.map((p, i) => {
      const payouts = this.state.payouts.filter(row => BigInt(row.timestampNs) >= ns(p.start) && BigInt(row.timestampNs) < ns(p.endExclusive));
      if (!payouts.length) throw Error('No verified payout coverage');
      const gross = payouts.reduce((sum, row) => sum + BigInt(row.grossMintedYocto), 0n), net = BigInt(endpoints[i+1].total_supply) - BigInt(endpoints[i].total_supply), burn = gross - net;
      if (burn < 0n || payouts.some(row => !REVIEWED_PROTOCOL_VERSIONS.includes(row.latestProtocolVersionReportedByBlock))) throw Error('Supply accounting rule requires review');
      return { startInclusiveUTC: p.start + 'T00:00:00Z', endExclusiveUTC: p.endExclusive + 'T00:00:00Z', openingBlock: endpoints[i].height, closingBlock: endpoints[i+1].height, openingSupplyYocto: endpoints[i].total_supply, closingSupplyYocto: endpoints[i+1].total_supply, payoutCount: payouts.length, grossMintedYocto: gross.toString(), grossMintedNEAR: native(gross), netSupplyChangeYocto: net.toString(), netSupplyChangeNEAR: native(net), nativeBurnDerivedYocto: burn.toString(), nativeBurnDerivedNEAR: native(burn), externalNativeWalletReceiptsNEAR: native(this.state.receipts.filter(r => BigInt(r.timestampNs) >= ns(p.start) && BigInt(r.timestampNs) < ns(p.endExclusive) && !WALLETS.includes(r.predecessor as typeof WALLETS[number])).reduce((sum, row) => sum + BigInt(row.amountYocto), 0n)), receiptQuantityToGrossMintingPct: gross === 0n ? null : String(p.nativeReceiptsNear / Number(native(gross)) * 100), supplyIdentityResidualYocto: '0' };
    });
    let longPeriods: LiveSnapshot['operating']['longPeriods'] = null;
    try { const middle90 = dayOf(Date.parse(end) - 90 * DAY), start180 = dayOf(Date.parse(end) - 180 * DAY); longPeriods = [period(start180, middle90, 90), period(middle90, end, 90)].map(({ start, endExclusive, days, volumeUsd, grossFeesUsd, collectionsUsd }) => ({ start, endExclusive, days, volumeUsd, grossFeesUsd, collectionsUsd })); } catch { /* Missing history remains unavailable. */ }
    const coveredReceipts = this.state.receipts.filter(r => BigInt(r.timestampNs) >= ns(start) && BigInt(r.timestampNs) < ns(end));
    const protocolVersions = [...new Set(this.state.payouts.filter(row => BigInt(row.timestampNs) >= ns(start) && BigInt(row.timestampNs) < ns(end)).map(row => row.latestProtocolVersionReportedByBlock))].sort((a, b) => a - b);
    return { checkedAt: nowIso(), reporting: { ...reporting, revisedDates: revisedDailyDates(this.snapshot.operating.daily, daily) }, endExclusive: end, daily, periods, longPeriods, nativeCoverage: { start, end: last, internalTransfersExcludedNear: Number(native(coveredReceipts.filter(r => WALLETS.includes(r.predecessor as typeof WALLETS[number])).reduce((s, r) => s + BigInt(r.amountYocto), 0n))), externalReceiptCount: coveredReceipts.filter(r => !WALLETS.includes(r.predecessor as typeof WALLETS[number])).length }, supply: { windows, protocolVersions } };
  }
  private async documents(sourceId?: (typeof POLICY_SOURCES)[number]['id']): Promise<LiveSnapshot['review']> {
    let reviewed: { reviewedAt: string; hashes: Record<string, string> } = { reviewedAt: '2026-10-28', hashes: {} };
    try {
      const saved = JSON.parse(await readFile(resolve(ROOT, 'server/src/live/reviewed.json'), 'utf8'));
      if (typeof saved?.reviewedAt === 'string' && saved.hashes && typeof saved.hashes === 'object') reviewed = saved;
    } catch { /* No review is inferred from a successful fetch. */ }
    const requested = sourceId ? POLICY_SOURCES.filter(source => source.id === sourceId) : POLICY_SOURCES;
    const sources = await Promise.all(requested.map(async source => {
      try {
        const r = await this.fetcher(source.url, { signal: AbortSignal.timeout(20_000), headers: { 'User-Agent': 'IntentThesis/1.0 public-source verification' } });
        if (!r.ok) throw Error('Source unavailable'); const text = await r.text();
        if (text.length < 100 || text.length > 3_000_000 || /just a moment|access denied/i.test(text.slice(0,150))) throw Error('Unexpected source document');
        const currentHash = hash(normalizeDocument(text)), reviewedHash = reviewed.hashes[source.id] ?? null;
        await writeFile(resolve(this.cacheDir, 'sources', `document-${source.id}.json`), JSON.stringify({ url: source.url, checkedAt: nowIso(), sha256: currentHash, text }));
        await writeFile(resolve(this.cacheDir, 'sources', `document-${source.id}-${currentHash}.json`), JSON.stringify({ url: source.url, checkedAt: nowIso(), normalizedSha256: currentHash, text }), { flag: 'wx' }).catch((error: NodeJS.ErrnoException) => { if (error.code !== 'EEXIST') throw error; });
        return { ...source, reviewedHash, currentHash, checkedAt: nowIso(), state: reviewedHash === null ? 'unreviewed' as const : currentHash === reviewedHash ? 'unchanged' as const : 'changed' as const };
      } catch { return { ...source, reviewedHash: reviewed.hashes[source.id] ?? null, currentHash: null, checkedAt: nowIso(), state: 'unavailable' as const }; }
    }));
    return { reviewedAt: reviewed.reviewedAt, sources };
  }
  private async collect(force: boolean) {
    const next = structuredClone(this.snapshot);
    const health = (key: keyof LiveSnapshot['health'], error: unknown | null): FeedHealth => ({ checkedAt: nowIso(), successAt: error ? next.health[key].successAt : nowIso(), error: error ? (error instanceof Error ? error.message : 'Source validation failed') : null });
    let final: Block | null = null;
    const tasks: Promise<void>[] = [];
    if (force || !next.health.market.checkedAt || Date.now() - Date.parse(next.health.market.checkedAt) >= CHAIN_INTERVAL - 500) tasks.push(this.market().then(value => { next.market = value; next.health.market = health('market', null); }).catch(e => { next.health.market = health('market', e); }));
    if (force || !next.health.chain.checkedAt || Date.now() - Date.parse(next.health.chain.checkedAt) >= CHAIN_INTERVAL - 500) tasks.push(this.chain().then(result => { next.chain = result.value; final = result.block; next.health.chain = health('chain', null); }).catch(e => { next.health.chain = health('chain', e); }));
    const operatingDue = force || !next.health.operating.checkedAt || Date.now() - Date.parse(next.health.operating.checkedAt) >= OPERATING_INTERVAL;
    const documentsDue = force || !next.health.documents.checkedAt || Date.now() - Date.parse(next.health.documents.checkedAt) >= DOCUMENT_INTERVAL;
    const adapter = next.review.sources.find(source => source.id === 'adapter');
    if (documentsDue) {
      tasks.push(this.documents().then(value => {
        next.review = value;
        next.health.documents = health('documents', value.sources.some(s => s.state === 'unavailable') ? Error('Some policy sources could not be checked') : null);
      }).catch(e => {
        next.health.documents = health('documents', e);
        next.review.sources = next.review.sources.map(source => ({ ...source, currentHash: null, checkedAt: next.health.documents.checkedAt, state: 'unavailable' }));
      }));
    } else if (operatingDue && adapter?.state === 'unavailable' && (!adapter.checkedAt || Date.now() - Date.parse(adapter.checkedAt) >= OPERATING_INTERVAL)) {
      tasks.push(this.documents('adapter').then(({ sources: [checked] }) => {
        // The full-review timestamp still describes the last check of every source.
        next.review.sources = next.review.sources.map(source => source.id === 'adapter' ? checked : source);
      }).catch(() => { adapter.checkedAt = nowIso(); }));
    }
    await Promise.all(tasks);
    if (operatingDue) {
      const previousState = structuredClone(this.state);
      try {
        if (next.review.sources.find(source => source.id === 'adapter')?.state !== 'unchanged') throw Error('Collection methodology awaits source verification');
        const head = final ?? this.validateBlock(await this.rpc('block', { finality: 'final' }));
        next.operating = await this.operating(head);
        next.health.operating = health('operating', null);
      } catch (e) { this.state = previousState; next.health.operating = health('operating', e); }
    }
    // A cold historical backfill can take several minutes; refresh fast observations before publication.
    if (Date.now() - Date.parse(next.health.market.checkedAt || '1970-01-01') > 90_000) {
      await Promise.all([
        this.market().then(value => { next.market = value; next.health.market = health('market', null); }).catch(e => { next.health.market = health('market', e); }),
        this.chain().then(result => { next.chain = result.value; next.health.chain = health('chain', null); }).catch(e => { next.health.chain = health('chain', e); }),
      ]);
    }
    next.generatedAt = nowIso(); next.id = hash(JSON.stringify(next)).slice(0, 20);
    try {
      validateSnapshot(next);
      validateNativeLedger(next, this.state.receipts);
      await this.atomic('collector.json', this.state);
      await this.atomic('snapshot.json', next);
      this.records = this.records.slice(-1500);
      await this.atomic('source-manifest.json', this.records);
      this.snapshot = next; this.remember();
    } catch (e) { console.error('Live thesis validation retained the previous snapshot:', e instanceof Error ? e.message : e); }
  }
  async handle(req: IncomingMessage, res: ServerResponse): Promise<boolean> {
    const url = new URL(req.url || '/', 'http://localhost');
    if (!url.pathname.startsWith('/api/thesis')) return false;
    if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405, { Allow: 'GET, HEAD' }); res.end(); return true; }
    await this.start();
    const send = (status: number, value: string, type = 'application/json; charset=utf-8') => { res.writeHead(status, { 'Content-Type': type, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' }); res.end(req.method === 'HEAD' ? undefined : value); };
    if (url.pathname === '/api/thesis') { send(200, JSON.stringify(this.snapshot)); return true; }
    if (url.pathname === '/api/thesis/daily.csv' || url.pathname === '/api/thesis/native.csv') {
      const version = this.versions.get(url.searchParams.get('snapshot') || this.snapshot.id);
      if (!version) { send(410, JSON.stringify({ error: 'This snapshot expired. Refresh the page for matching data.' })); return true; }
      if (url.pathname.endsWith('daily.csv')) {
        const keys = ['date','volumeUsd','grossFeesUsd','collectionsUsd','nativeReceiptsNear','priceUsd'] as const;
        send(200, [keys.join(','), ...version.snapshot.operating.daily.map(row => keys.map(key => row[key] ?? '').join(','))].join('\n') + '\n', 'text/csv; charset=utf-8');
      } else {
        const end = ns(version.snapshot.operating.endExclusive), start = ns(version.snapshot.operating.nativeCoverage.start);
        send(200, 'utc,block,receipt_id,transaction_hash,predecessor,receiver,amount_near,classification\n' + version.receipts.filter(r => BigInt(r.timestampNs) >= start && BigInt(r.timestampNs) < end).map(r => [new Date(Number(BigInt(r.timestampNs) / 1_000_000n)).toISOString(),r.block,r.id,r.transactionHash,r.predecessor,r.receiver,native(BigInt(r.amountYocto)),WALLETS.includes(r.predecessor as typeof WALLETS[number]) ? 'internal_native_transfer' : 'external_native_inflow'].join(',')).join('\n') + '\n', 'text/csv; charset=utf-8');
      }
      return true;
    }
    send(404, JSON.stringify({ error: 'Not found' })); return true;
  }
}
