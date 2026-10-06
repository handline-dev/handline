// React bindings for the shared store. No JSX, so the package is plain .ts that Vite and Metro compile as it is.
import { createContext, createElement, useContext, useEffect, useRef, useSyncExternalStore, type ReactNode } from 'react';
import type { Store } from '@handline/app-core';
import { RpcError, type AgentDetail, type AgentView } from '@handline/client';

const StoreContext = createContext<Store | null>(null);

/**
 * Gives the tree one store, and starts it. The caller builds the store (and its client), so each app picks its own transport.
 * `connect` false leaves it unstarted (the app has already said why). A daemon that cannot be reached is said so in `error` and tried again; one that refuses this client (protocol) is not.
 */
export function DaemonProvider(props: { store: Store; where?: string; retryMs?: number; connect?: boolean; children?: ReactNode }) {
  const { store, where, retryMs = 2000, connect = true } = props;
  useEffect(() => {
    if (!connect) return;
    let done = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const boot = () =>
      store.start().then(
        () => store.dismissError(),
        (e) => {
          if (done) return;
          const fatal = e instanceof RpcError && e.code === 'protocol';
          store.fail(fatal ? (e.reason ?? e.message) : `Cannot reach the daemon${where ? ` at ${where}` : ''}; trying again.`);
          if (!fatal) timer = setTimeout(boot, retryMs);
        },
      );
    void boot();
    return () => {
      done = true;
      clearTimeout(timer);
      store.stop();
    };
  }, [store, where, retryMs, connect]);
  return createElement(StoreContext.Provider, { value: store }, props.children);
}

export function useStore(): Store {
  const store = useContext(StoreContext);
  if (!store) throw new Error('useStore needs a DaemonProvider above it');
  return store;
}

/**
 * Reads something from the store and draws again when the store changes. `select` runs on every change and should be cheap;
 * the result is kept while the store's `version` is the same, so it may build new objects.
 */
export function useSelector<T>(select: (s: Store) => T): T {
  const store = useStore();
  const version = useSyncExternalStore(
    (cb) => store.subscribe(cb),
    () => store.version,
  );
  const last = useRef<{ store: Store; version: number; value: T } | null>(null);
  if (!last.current || last.current.store !== store || last.current.version !== version)
    last.current = { store, version, value: select(store) };
  return last.current.value;
}

export const useConnection = () => useSelector((s) => s.conn);
export const useAgents = (): AgentView[] => useSelector((s) => [...s.agents.values()]);
export const useError = () => useSelector((s) => s.error);

/** An agent's detail, kept current while the component is mounted. */
export function useAgentDetail(name: string): AgentDetail | undefined {
  const store = useStore();
  useEffect(() => {
    void store.watch(name).catch(() => {});
    return () => store.unwatch(name);
  }, [store, name]);
  return useSelector((s) => s.details.get(name));
}
