import type { Plugin } from 'vite';
import { LiveService } from '../live/service.ts';

export function liveThesis(): Plugin {
  return {
    name: 'live-thesis-data',
    configureServer(server) {
      const service = new LiveService();
      void service.start().catch(error => console.error('Live data initialization:', error.message));
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith('/api/thesis')) return next();
        void service.handle(req, res).then(handled => { if (!handled) next(); }).catch(() => { if (!res.headersSent) res.writeHead(503, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify({ error: 'Live data is temporarily unavailable' })); });
      });
      server.httpServer?.once('close', () => service.stop());
    },
    configurePreviewServer(server) {
      const service = new LiveService();
      void service.start().catch(error => console.error('Live data initialization:', error.message));
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith('/api/thesis')) return next();
        void service.handle(req, res).then(handled => { if (!handled) next(); }).catch(() => { res.writeHead(503, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ error: 'Live data is temporarily unavailable' })); });
      });
      server.httpServer.once('close', () => service.stop());
    },
  };
}
