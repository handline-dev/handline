// One window of a kind: a second request while it is being made waits for that one, and while it is open and its content still good
// focuses it. A window past its expiry is closed and made anew (the pairing code in it works once and lapses).
export interface Win {
  focus(): void;
  close(): void;
  isDestroyed(): boolean;
  once(event: 'closed', f: () => void): unknown;
}

export function singleWindow(make: () => Promise<{ win: Win; expiresAt: number }>, now: () => number = Date.now): () => Promise<void> {
  let open: { win: Win; expiresAt: number } | null = null;
  let making: Promise<void> | null = null;
  return () => {
    if (open && !open.win.isDestroyed()) {
      if (now() < open.expiresAt) {
        open.win.focus();
        return Promise.resolve();
      }
      open.win.close();
    }
    making ??= make()
      .then((o) => {
        open = o;
        o.win.once('closed', () => void (open === o && (open = null)));
      })
      .finally(() => void (making = null));
    return making;
  };
}
