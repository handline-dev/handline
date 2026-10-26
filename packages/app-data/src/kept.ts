// State that outlives the component that shows it, kept per daemon and key (the agent): an unsent draft's pictures stay when the person leaves
// the chat and comes back. A daemon's own store holds them, so another daemon, or the same one after pairing again, starts empty.
// Without a key the state lives and dies with the component, as plain state does. Whoever sent or removed what is kept clears it.
import { useRef, useSyncExternalStore } from 'react';
import { useStore } from '@handline/app-react';

export interface Kept<S> {
  get(): S;
  set(next: Partial<S> | ((s: S) => Partial<S>)): void;
  subscribe(listener: () => void): () => void;
}

function hold<S>(make: () => S): Kept<S> {
  let state = make();
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    set(next) {
      state = { ...state, ...(typeof next === 'function' ? next(state) : next) };
      listeners.forEach((l) => l());
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
  };
}

/** A hook for state made by `make`, shared by every component of one daemon that passes the same key. Parts that must not be replaced (a map of running uploads) are made once. */
export function keptState<S>(make: () => S) {
  const byStore = new WeakMap<object, Map<string, Kept<S>>>();
  return function useKept(key?: string): Kept<S> & { state: S } {
    const store = useStore();
    const local = useRef<Kept<S>>(undefined);
    let kept: Kept<S>;
    if (key === undefined) kept = local.current ??= hold(make);
    else {
      const keys = byStore.get(store) ?? new Map<string, Kept<S>>();
      byStore.set(store, keys);
      kept = keys.get(key) ?? hold(make);
      keys.set(key, kept);
    }
    const state = useSyncExternalStore(kept.subscribe, kept.get);
    return { ...kept, state };
  };
}
