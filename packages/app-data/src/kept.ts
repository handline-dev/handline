// State that outlives the component that shows it, kept per key (the agent): an unsent draft's pictures stay when the person leaves the chat and
// comes back. Without a key the state lives and dies with the component, as plain state does. What is kept is cleared by whoever sent or removed it.
import { useRef, useSyncExternalStore } from 'react';

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

const stores = new Set<Map<string, unknown>>();
/** Lets go of everything kept, for every key: a test starts clean, and so does a person who signs out. */
export const forgetKept = () => stores.forEach((m) => m.clear());

/** A hook for state made by `make`, shared by every component that passes the same key. Parts that must not be replaced (a map of running uploads) are made once. */
export function keptState<S>(make: () => S) {
  const byKey = new Map<string, Kept<S>>();
  stores.add(byKey);
  return function useKept(key?: string): Kept<S> & { state: S } {
    const local = useRef<Kept<S>>(undefined);
    let kept: Kept<S>;
    if (key === undefined) kept = local.current ??= hold(make);
    else {
      kept = byKey.get(key) ?? hold(make);
      byKey.set(key, kept);
    }
    const state = useSyncExternalStore(kept.subscribe, kept.get);
    return { ...kept, state };
  };
}
