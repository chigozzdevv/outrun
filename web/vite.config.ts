import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { liveThesis } from '../server/src/vite/live-plugin.ts';

const cataloguePath = fileURLToPath(new URL('../data/evidence/2026-11-02/catalog.json', import.meta.url));

export default defineConfig({
  root: fileURLToPath(new URL('./', import.meta.url)),
  plugins: [react(), liveThesis(), {
    name: 'public-dataset-metadata',
    enforce: 'pre',
    transform(source: string, id: string) {
      if (id.split('?')[0] !== cataloguePath) return;
      const catalogue = JSON.parse(source) as { datasets: { source: string; download: unknown }[] };
      return {
        code: JSON.stringify({ datasets: catalogue.datasets.filter(item => item.download !== null).map(({ source, download }) => ({ source, download })) }),
        map: null,
      };
    },
  }],
  server: { host: '127.0.0.1', port: 5174, strictPort: true },
  build: { target: 'es2022' },
});
