// The keys a screen answers, told by the screen itself while it is mounted; the `?` sheet lists whatever is registered, so it has no copy.
import { useEffect, useSyncExternalStore } from 'react';

export type KeyRows = readonly (readonly [keys: string, what: string])[];
export type KeyGroup = { title: string; rows: KeyRows };

let groups: readonly KeyGroup[] = [];
const subs = new Set<() => void>();
const set = (next: readonly KeyGroup[]) => {
  groups = next;
  subs.forEach((f) => f());
};

/** Lists `rows` under `title` for as long as the calling screen is mounted. `rows` must be a stable table. */
export function useScreenKeys(title: string, rows: KeyRows) {
  useEffect(() => {
    const group = { title, rows };
    set([...groups, group]);
    return () => set(groups.filter((g) => g !== group));
  }, [title, rows]);
}

export function useRegisteredKeys(): readonly KeyGroup[] {
  return useSyncExternalStore(
    (f) => (subs.add(f), () => void subs.delete(f)),
    () => groups,
  );
}
