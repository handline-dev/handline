// React bindings for the shared store. No JSX, so the package is plain .ts that Vite and Metro compile as it is.
import { createContext, createElement, useContext, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import type { Store } from '@handline/app-core';
import { RpcError } from '@handline/client';

const StoreContext = createContext<Store | null>(null);

/**
 * Gives the tree one store, and starts it. The caller builds the store (and its client), so each app picks its own transport.
 * `connect` false leaves it unstarted (the app has already said why). A daemon that cannot be reached is said so in `error` and tried again; one that refuses this client (protocol) is not.
 */
export function DaemonProvider(props: {
  store: Store;
  where?: string;
  retryMs?: number;
  connect?: boolean;
  /** False when the screen draws the unreachable daemon itself (from `store.unreachable`) and a notice would say it twice. */
  notify?: boolean;
  children?: ReactNode;
}) {
  const { store, where, retryMs = 2000, connect = true, notify = true } = props;
  useEffect(() => {
    if (!connect) return;
    let done = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const boot = () =>
      store.start().then(
        () => (store.setUnreachable(false), store.dismissError()),
        (e) => {
          if (done) return;
          const fatal = e instanceof RpcError && e.code === 'protocol';
          if (fatal || notify)
            store.fail(fatal ? (e.reason ?? e.message) : `Cannot reach the daemon${where ? ` at ${where}` : ''}; trying again.`);
          store.setUnreachable(!fatal);
          if (!fatal) timer = setTimeout(boot, retryMs);
        },
      );
    void boot();
    return () => {
      done = true;
      clearTimeout(timer);
      store.stop();
    };
  }, [store, where, retryMs, connect, notify]);
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
export const useError = () => useSelector((s) => s.error);

/** Runs one async job at a time: a call while it runs does nothing. `going` is true meanwhile, for the buttons to disable. */
export function useOnce(): [going: boolean, once: (job: () => Promise<void>) => Promise<void>] {
  const [going, setGoing] = useState(false);
  const busy = useRef(false);
  const once = async (job: () => Promise<void>) => {
    if (busy.current) return;
    busy.current = true;
    setGoing(true);
    try {
      await job();
    } finally {
      busy.current = false;
      setGoing(false);
    }
  };
  return [going, once];
}

/**
 * Calls `more` when the sentinel element comes within `margin` px of the scroll box `root` (the top of a chat, the end of a list; `null` for the window): put the
 * returned ref on an empty element at that end. Where there is no IntersectionObserver the box's own scroll is watched for the same distance.
 * `more` is called again only after the sentinel has left the margin and come back, or after `enabled` turns false and true.
 * With `dwell` ms, the sentinel must stay inside the margin that long first: a scroll that only passes by loads nothing.
 */
export function useNearEnd<R extends HTMLElement, S extends HTMLElement>(
  root: { current: R | null } | null,
  more: () => void,
  enabled: boolean,
  { margin = 200, atTop = false, dwell = 0 }: { margin?: number; atTop?: boolean; dwell?: number } = {},
): { current: S | null } {
  const sentinel = useRef<S | null>(null);
  const call = useRef(more);
  call.current = more;
  useEffect(() => {
    const box = root?.current ?? null; // none: the window scrolls
    const el = sentinel.current;
    if (!enabled || !el || (root && !box)) return;
    if (typeof IntersectionObserver === 'function') {
      let wait: ReturnType<typeof setTimeout> | undefined;
      const io = new IntersectionObserver(
        (e) => {
          clearTimeout(wait);
          if (!e.at(-1)?.isIntersecting) return;
          if (dwell) wait = setTimeout(() => call.current(), dwell);
          else call.current();
        },
        { root: box, rootMargin: `${margin}px` },
      );
      io.observe(el);
      return () => (clearTimeout(wait), io.disconnect());
    }
    const target: HTMLElement | Window = box ?? window;
    const onScroll = () => {
      const d = box ?? document.documentElement;
      const top = box ? box.scrollTop : window.scrollY;
      if (atTop ? top < margin : d.scrollHeight - top - d.clientHeight < margin) call.current();
    };
    target.addEventListener('scroll', onScroll);
    return () => target.removeEventListener('scroll', onScroll);
  }, [root, enabled, margin, atTop, dwell]);
  return sentinel;
}
