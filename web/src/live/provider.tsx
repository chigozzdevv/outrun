import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { fallbackSnapshot, validateSnapshot } from '../../../compute/src/live/snapshot';
import { type LiveSnapshot } from '../../../compute/src/live/schema';
import { apiUrl } from './api';

const initial = fallbackSnapshot();
const Context = createContext<{ snapshot: LiveSnapshot; connection: 'connecting' | 'connected' | 'offline' }>({ snapshot: initial, connection: 'connecting' });
export function LiveDataProvider({ children, snapshot: fixed }: { children: ReactNode; snapshot?: LiveSnapshot }) {
  const [state, setState] = useState({ snapshot: fixed ?? initial, connection: fixed ? 'connected' as const : 'connecting' as 'connecting' | 'connected' | 'offline' });
  useEffect(() => {
    if (fixed) return;
    let stopped = false, fetching = false, timeout: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      if (fetching || stopped) return;
      fetching = true;
      let delay = 60_000;
      try {
        const response = await fetch(apiUrl('/api/thesis'), { cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]) });
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw Error('Live service unavailable');
        const next: unknown = await response.json(); validateSnapshot(next);
        if (Date.parse(next.generatedAt) > Date.now() + 5 * 60_000) throw Error('Snapshot timestamp is in the future');
        if (!stopped) setState(current => Date.parse(next.generatedAt) >= Date.parse(current.snapshot.generatedAt) ? { snapshot: next, connection: 'connected' } : { ...current, connection: 'offline' });
        delay = Object.values(next.health).some(feed => !feed.checkedAt) ? 5000 : next.refreshSeconds * 1000;
      } catch { if (!stopped) setState(current => ({ ...current, connection: 'offline' })); }
      fetching = false;
      if (!stopped) timeout = setTimeout(poll, delay);
    }
    void poll();
    const visible = () => { if (document.visibilityState === 'visible') { clearTimeout(timeout); void poll(); } };
    document.addEventListener('visibilitychange', visible);
    return () => { stopped = true; controller.abort(); clearTimeout(timeout); document.removeEventListener('visibilitychange', visible); };
  }, [fixed]);
  return <Context.Provider value={state}>{children}</Context.Provider>;
}
export function useLiveData() { return useContext(Context); }
