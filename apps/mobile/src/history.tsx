// Back and forward across the moves inside the app. The phone has no browser history, so Home owns a list of
// snapshots: every move that goes through useHistoryValue is a new entry, back and forward walk the list.
import { createContext, useContext, useState, useSyncExternalStore, type ReactNode } from 'react';

type Snap = Readonly<Record<string, unknown>>;

export class History {
  private entries: Snap[] = [{}];
  private at = 0;
  private subs = new Set<() => void>();
  get = <T,>(key: string, initial: T): T => (key in this.entries[this.at]! ? (this.entries[this.at]![key] as T) : initial);
  /** One new entry with `key` changed; whatever lay ahead of the current entry is dropped. */
  push = (key: string, value: unknown) => {
    if (Object.is(this.entries[this.at]![key], value)) return;
    this.entries = [...this.entries.slice(0, this.at + 1), { ...this.entries[this.at], [key]: value }];
    this.at++;
    this.emit();
  };
  back = () => this.go(-1);
  forward = () => this.go(1);
  /** True when there was an entry to walk to. */
  private go(d: number) {
    const to = this.at + d;
    if (to < 0 || to >= this.entries.length) return false;
    this.at = to;
    this.emit();
    return true;
  }
  subscribe = (f: () => void) => (this.subs.add(f), () => void this.subs.delete(f));
  private emit() {
    this.version++;
    this.subs.forEach((f) => f());
  }
  version = 0;
}

const Ctx = createContext<History | null>(null);

export function HistoryProvider(props: { history: History; children: ReactNode }) {
  return <Ctx.Provider value={props.history}>{props.children}</Ctx.Provider>;
}

export const useHistory = () => useContext(Ctx);

/** Like useState, but a change is an entry that back undoes. Outside a HistoryProvider it is plain state. */
export function useHistoryValue<T>(key: string, initial: T): [T, (v: T) => void] {
  const h = useHistory();
  const [own, setOwn] = useState(initial);
  useSyncExternalStore(h?.subscribe ?? noSubscribe, () => h?.version ?? 0);
  if (!h) return [own, setOwn];
  return [h.get(key, initial), (v) => h.push(key, v)];
}

const noSubscribe = () => () => {};
