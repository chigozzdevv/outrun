import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { LiveService } from './live/service.ts';

const root = resolve(fileURLToPath(new URL('../../web/dist/', import.meta.url)));
const service = new LiveService();
const types: Record<string, string> = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.csv': 'text/csv; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.ico': 'image/x-icon', '.woff2': 'font/woff2' };
const server = createServer(async (req, res) => {
  try {
    if (await service.handle(req, res)) return;
    if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405, { Allow: 'GET, HEAD' }); res.end(); return; }
    const url = new URL(req.url || '/', 'http://localhost');
    let path = resolve(root, '.' + decodeURIComponent(url.pathname));
    if (path !== root && !path.startsWith(root + sep)) { res.writeHead(403); res.end(); return; }
    try { if ((await stat(path)).isDirectory()) path = resolve(path, 'index.html'); } catch { if (extname(path)) { res.writeHead(404); res.end(); return; } path = resolve(root, 'index.html'); }
    const body = await readFile(path);
    res.writeHead(200, { 'Content-Type': types[extname(path)] || 'application/octet-stream', 'Cache-Control': path.includes(`${sep}assets${sep}`) ? 'public, max-age=31536000, immutable' : 'no-cache', 'X-Content-Type-Options': 'nosniff' });
    res.end(req.method === 'HEAD' ? undefined : body);
  } catch { if (!res.headersSent) res.writeHead(503); res.end('Service temporarily unavailable'); }
});
server.listen(Number(process.env.PORT || 5174), process.env.HOST || '127.0.0.1', () => { console.log(`Live thesis: http://${process.env.HOST || '127.0.0.1'}:${process.env.PORT || 5174}`); void service.start(); });
for (const signal of ['SIGINT','SIGTERM'] as const) process.once(signal, () => { service.stop(); server.close(); });
