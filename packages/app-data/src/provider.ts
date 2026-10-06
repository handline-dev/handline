// The data layer's root: the query client, which daemon the keys belong to, and the event bridge that keeps the cache current.
import { createContext, createElement, useContext, useEffect, useState, type ComponentType, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useSelector, useStore } from '@handline/app-react';
import type { Topic } from '@handline/client';
import { createBridge, resync } from './bridge.ts';
import { retryDelay, retryRead } from './errors.ts';

/** What draws a skeleton box: each app gives its own (a `div` on the web, a `View` on the phone). */
export type BoxComponent = ComponentType<{ style: Record<string, unknown>; children?: ReactNode } & Record<string, unknown>>;

interface Data {
  daemon: string;
  box: BoxComponent | null;
}
const DataContext = createContext<Data | null>(null);

/**
 * `networkMode: 'always'`: the connection is to a daemon, not the internet, and the client waits for `open` and sends a pending call
 * again after a reconnect itself. A write is never retried here; that would double the client's own resend.
 */
export const makeQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { networkMode: 'always', retry: retryRead, retryDelay, refetchOnWindowFocus: false, refetchOnReconnect: false },
      mutations: { networkMode: 'always', retry: false },
    },
  });

/**
 * Gives the tree React Query. It sits inside a `DaemonProvider`; `daemonId` starts every key, so two daemons never share an entry.
 * A gap in the event stream and a reconnect make every active query read again; the events in between are cache edits.
 */
export function DataProvider(props: { daemonId: string; box?: BoxComponent; client?: QueryClient; children?: ReactNode }) {
  const { daemonId, box = null } = props;
  const store = useStore();
  const [queries] = useState(() => props.client ?? makeQueryClient());
  useEffect(() => {
    const bridge = createBridge(queries, daemonId, () => store.me);
    const offs = [
      store.client.onEvent((e) => bridge.push(e)),
      store.client.onGap(() => resync(queries, daemonId)),
      store.client.onState((st) => {
        if (st === 'open') resync(queries, daemonId);
      }),
    ];
    return () => {
      for (const off of offs) off();
      queries.clear();
    };
  }, [store, queries, daemonId]);
  return createElement(
    QueryClientProvider,
    { client: queries },
    createElement(DataContext.Provider, { value: { daemon: daemonId, box } }, props.children),
  );
}

export function useData(): Data {
  const data = useContext(DataContext);
  if (!data) throw new Error('useData needs a DataProvider above it');
  return data;
}

/** Whether a view may read or write `method` now: the connection is open and the daemon lists the method for this caller. */
export function useServes(method: string): { served: boolean; open: boolean } {
  const served = useSelector((s) => s.can(method));
  const open = useSelector((s) => s.conn === 'open');
  return { served, open };
}

/** Streams a topic's events while a view that needs it is mounted (the client keeps a topic once asked for). */
export function useFeed(topic: Topic | undefined, on: boolean): void {
  const store = useStore();
  useEffect(() => {
    if (topic && on) void store.client.subscribe([topic]).catch(() => {});
  }, [store, topic, on]);
}
