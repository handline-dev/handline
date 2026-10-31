import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { MenuItemConstructorOptions } from 'electron';
import { appMenu, GO_SCREENS } from '#src/app-menu.ts';

const labels = (m: MenuItemConstructorOptions[]) => m.map((i) => i.label);
const sub = (m: MenuItemConstructorOptions[], label: string) =>
  (m.find((i) => i.label === label)?.submenu ?? []) as MenuItemConstructorOptions[];

test('on a Mac the menu bar leads with Handline, then Edit, Go, View and Window', () => {
  const m = appMenu({ mac: true, go: () => undefined });
  assert.deepEqual(labels(m), ['Handline', 'Edit', 'Go', 'View', 'Window']);
  const app = sub(m, 'Handline');
  assert.ok(app.some((i) => i.role === 'about'));
  assert.ok(app.some((i) => i.role === 'quit'));
  assert.ok(app.some((i) => i.label === 'Settings…' && i.accelerator === 'CmdOrCtrl+,'));
});

test('Edit carries the roles that make the text shortcuts work', () => {
  const roles = sub(appMenu({ mac: true, go: () => undefined }), 'Edit').map((i) => i.role);
  for (const r of ['undo', 'redo', 'cut', 'copy', 'paste', 'selectAll']) assert.ok(roles.includes(r as never), r);
});

test('each Go entry opens its screen and has its own shortcut', () => {
  const seen: string[] = [];
  const go = sub(appMenu({ mac: true, go: (s) => seen.push(s) }), 'Go');
  assert.deepEqual(
    go.map((i) => i.label),
    ['Agents', 'Requests', 'Tasks', 'Usage'],
  );
  go.forEach((i) => (i.click as () => void)());
  assert.deepEqual(
    seen,
    GO_SCREENS.map((s) => s.screen),
  );
  assert.equal(new Set(go.map((i) => i.accelerator)).size, go.length);
});

test('off a Mac Settings and Quit live in File', () => {
  const file = sub(appMenu({ mac: false, go: () => undefined }), 'File');
  assert.ok(file.some((i) => i.label === 'Settings…') && file.some((i) => i.role === 'quit'));
});
