import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { QueryClient } from '@tanstack/react-query';
import { DaemonProvider } from '@handline/app-react';
import { Store } from '@handline/app-core';
import { METHODS, RpcError, type DaemonClient, type Event as WireEvent } from '@handline/client';
import { DataProvider, makeQueryClient, type BoxComponent } from '../src/index.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

export const DAEMON = 'd1';
export const sleep = (ms: number) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
export async function until(cond: () => unknown, what: string, ms = 3000) {
  for (let t = 0; t < ms; t += 5) {
    if (cond()) return;
    await sleep(5);
  }
  throw new Error(`timed out waiting for ${what}`);
}

type Reply = (method: string, params: Record<string, unknown>) => unknown | Promise<unknown>;

/** A client that answers from a script and records every call: for the cases the fake daemon does not serve or must not be fast at. */
export function stubClient(reply: Reply = () => ({})) {
  const calls: { method: string; params: Record<string, unknown> }[] = [];
  const events = new Set<(e: WireEvent) => void>();
  const gaps = new Set<() => void>();
  const states = new Set<(s: string) => void>();
  const stub = {
    state: 'open',
    hello: null,
    calls,
    reply,
    async call(method: string, params: Record<string, unknown>) {
      calls.push({ method, params });
      return stub.reply(method, params);
    },
    async subscribe() {},
    onEvent: (f: (e: WireEvent) => void) => (events.add(f), () => events.delete(f)),
    onGap: (f: () => void) => (gaps.add(f), () => gaps.delete(f)),
    onState: (f: (s: string) => void) => (states.add(f), () => states.delete(f)),
    close() {},
    emit: (e: Partial<WireEvent> & { type: string; topic: WireEvent['topic'] }) =>
      events.forEach((f) =>
        f({ cursor: 1, at: '2026-01-01T00:00:00Z', actor: null, agent: null, title: null, target: null, data: {}, ...e }),
      ),
    gap: () => gaps.forEach((f) => f()),
    reconnect: () => states.forEach((f) => f('open')),
    count: (method: string) => calls.filter((c) => c.method === method).length,
  };
  return stub;
}
export type Stub = ReturnType<typeof stubClient>;

/** A store whose connection is open and that lists every method; `me` is the person. */
export function openStore(client: DaemonClient | Stub, served: string[] = Object.keys(METHODS)) {
  const store = new Store(client as unknown as DaemonClient);
  store.conn = 'open';
  store.me = 'me';
  store.served = new Set(served);
  return store;
}

export const refuse = (reason = 'no', gate = 'state', hint?: string) =>
  new RpcError('refused', reason, { reason, gate, ...(hint ? { hint } : {}) });

let root: Root | null = null;
let host: HTMLElement | null = null;

/** Renders `children` under a DaemonProvider (not started) and a DataProvider on a fresh query client. */
export async function mount(store: Store, children: ReactNode, client: QueryClient = makeQueryClient(), box?: BoxComponent) {
  host = document.body.appendChild(document.createElement('div'));
  root = createRoot(host);
  await act(async () => {
    root!.render(
      <DaemonProvider store={store} connect={false}>
        <DataProvider daemonId={DAEMON} client={client} box={box}>
          {children}
        </DataProvider>
      </DaemonProvider>,
    );
  });
  return { qc: client, box: host };
}
export async function unmount() {
  await act(async () => root?.unmount());
  root = null;
  host?.remove();
  host = null;
}

/** Calls a hook in a component and keeps its latest value in `out.current`. */
export function Probe<T>(props: { use: () => T; out: { current: T | undefined } }) {
  props.out.current = props.use();
  return null;
}
export const probe = <T,>(use: () => T) => {
  const out: { current: T | undefined } = { current: undefined };
  return { out, node: <Probe use={use} out={out} />, get: () => out.current as T };
};
