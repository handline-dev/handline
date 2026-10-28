// The chat composers on screen. A composer registers itself as it mounts, so what must stay clear of them (the toasts) is told of one that
// comes or goes without watching the page.
const rows = new Set<Element>();
const listeners = new Set<() => void>();
const changed = () => listeners.forEach((l) => l());

/** A ref callback for a composer row; the cleanup runs when it unmounts. */
export function registerComposer(row: Element | null) {
  if (!row) return;
  rows.add(row);
  changed();
  return () => {
    rows.delete(row);
    changed();
  };
}

/** The composers now on screen, and a way to hear when the set changes. */
export const composers = {
  list: (): Element[] => [...rows],
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => void listeners.delete(listener);
  },
};
