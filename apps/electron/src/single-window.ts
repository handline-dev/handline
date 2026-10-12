// One window of a kind: a second request while it is being made waits for that one, and while it is open focuses it.
export interface Win {
  focus(): void;
  isDestroyed(): boolean;
  once(event: 'closed', f: () => void): unknown;
}

export function singleWindow(make: () => Promise<Win>): () => Promise<void> {
  let open: Win | null = null;
  let making: Promise<void> | null = null;
  return () => {
    if (open && !open.isDestroyed()) {
      open.focus();
      return Promise.resolve();
    }
    making ??= make()
      .then((w) => {
        open = w;
        w.once('closed', () => void (open = null));
      })
      .finally(() => void (making = null));
    return making;
  };
}
