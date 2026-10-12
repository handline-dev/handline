import assert from 'node:assert/strict';
import test from 'node:test';
import { singleWindow, type Win } from '../src/single-window.ts';

type Fake = Win & { focused: number; closedCalls: number; destroyed: boolean; shut(): void };
function fake(): Fake {
  let onClosed: (() => void) | null = null;
  const w: Fake = {
    focused: 0,
    closedCalls: 0,
    destroyed: false,
    focus: () => void w.focused++,
    close: () => void (w.closedCalls++, w.shut()),
    isDestroyed: () => w.destroyed,
    once: (_e, f) => void (onClosed = f),
    shut: () => ((w.destroyed = true), onClosed?.()),
  };
  return w;
}

test('a second click while the window is being made waits for it; while open and unexpired it focuses it', async () => {
  let made = 0;
  let release!: () => void;
  const w = fake();
  const open = singleWindow(
    async () => {
      made++;
      await new Promise<void>((r) => (release = r));
      return { win: w, expiresAt: 1000 };
    },
    () => 0,
  );
  const a = open();
  const b = open();
  release();
  await Promise.all([a, b]);
  assert.equal(made, 1);
  await open();
  assert.equal(made, 1);
  assert.equal(w.focused, 1);
});

test('after the window is closed, or its code has expired, the same guard makes a fresh one', async () => {
  let t = 0;
  const wins: Fake[] = [];
  const open = singleWindow(
    async () => {
      const win = fake();
      wins.push(win);
      return { win, expiresAt: t + 300 };
    },
    () => t,
  );
  await open();
  wins[0]!.shut();
  await open();
  assert.equal(wins.length, 2);
  t = 301;
  await open();
  assert.equal(wins.length, 3);
  assert.equal(wins[1]!.closedCalls, 1);
  assert.equal(wins[2]!.focused, 0);
});

test('a failed make can be tried again', async () => {
  let n = 0;
  const open = singleWindow(async () => {
    if (!n++) throw new Error('no key');
    return { win: fake(), expiresAt: Infinity };
  });
  await assert.rejects(open(), /no key/);
  await open();
  assert.equal(n, 2);
});
