import assert from 'node:assert/strict';
import test from 'node:test';
import { singleWindow, type Win } from '../src/single-window.ts';

function fake() {
  const w = { focused: 0, destroyed: false, closed: null as (() => void) | null } as {
    focused: number;
    destroyed: boolean;
    closed: (() => void) | null;
  } & Win;
  w.focus = () => void w.focused++;
  w.isDestroyed = () => w.destroyed;
  w.once = (_e, f) => void (w.closed = f);
  return w;
}

test('a second click while the window is being made or open makes no second window and focuses the open one', async () => {
  let made = 0;
  let release!: () => void;
  const w = fake();
  const open = singleWindow(async () => {
    made++;
    await new Promise<void>((r) => (release = r));
    return w;
  });
  const a = open();
  const b = open();
  release();
  await Promise.all([a, b]);
  assert.equal(made, 1);
  await open();
  assert.equal(made, 1);
  assert.equal(w.focused, 1);
  w.closed!();
  const w2 = fake();
  const again = singleWindow(async () => (made++, w2));
  await again();
  assert.equal(made, 2);
});

test('a failed make can be tried again', async () => {
  let n = 0;
  const open = singleWindow(async () => {
    if (!n++) throw new Error('no key');
    return fake();
  });
  await assert.rejects(open(), /no key/);
  await open();
  assert.equal(n, 2);
});
