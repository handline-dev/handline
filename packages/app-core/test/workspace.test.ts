import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  CHAT,
  TRANSCRIPT,
  MAX_GROUPS,
  activate,
  applyPreset,
  arrange,
  closeTab,
  addTab,
  defaultWorkspace,
  groupOf,
  groupsOf,
  loadWorkspace,
  moveTab,
  moveTabBy,
  openTab,
  openTask,
  parseWorkspace,
  saveWorkspace,
  serialize,
  setSplit,
  splitRoot,
  splitTab,
  storageKey,
  type Edge,
  type KV,
  type Workspace,
} from '../src/index.ts';

/** Chat beside the terminal, each in its own pane: the default split by hand. */
const two = () => splitTab(defaultWorkspace({ terminal: 'term:a' }), 'terminal:term:a', 'g1', 'right')!;
const ids = (ws: Workspace) => groupsOf(ws.root).map((g) => g.tabs.join('+'));
/** A workspace with n groups, one tab each beside Chat's: chat | md:1 | md:2 | md:3 */
const many = (n: number) => {
  let ws = defaultWorkspace();
  for (let i = 1; i < n; i++) ws = splitRoot(openTab(ws, 'markdown', { path: `f${i}.md` }), `markdown:f${i}.md`, 'right')!;
  return ws;
};

/** What must hold for every workspace, whatever was done to it. */
function invariants(ws: Workspace): void {
  const gs = groupsOf(ws.root);
  assert.ok(gs.length >= 1 && gs.length <= MAX_GROUPS, 'group count');
  const all = gs.flatMap((g) => g.tabs);
  assert.equal(new Set(all).size, all.length, 'a tab sits in one group');
  assert.deepEqual([...all].sort(), Object.keys(ws.tabs).sort(), 'every tab is in a group');
  assert.equal(Object.values(ws.tabs).filter((t) => t.kind === 'chat').length, 1, 'exactly one Chat');
  for (const g of gs) {
    assert.ok(g.tabs.length > 0, 'no empty group');
    assert.ok(g.tabs.includes(g.activeTab), 'active tab is in its group');
  }
  assert.ok(
    gs.some((g) => g.id === ws.active),
    'the focus names a group',
  );
  assert.equal(new Set(gs.map((g) => g.id)).size, gs.length, 'group ids are unique');
  const checkAt = (n: typeof ws.root): void => {
    if (n.type === 'split') {
      assert.ok(n.at >= 0.15 && n.at <= 0.85);
      checkAt(n.a);
      checkAt(n.b);
    }
  };
  checkAt(ws.root);
}

test('rule 1: at most 4 panes; a drag past that is refused, a drop on a strip never is', () => {
  const four = many(4);
  assert.equal(groupsOf(four.root).length, 4);
  let extra = openTab(four, 'markdown', { path: 'x.md' }, 'g1');
  assert.equal(splitTab(extra, 'markdown:x.md', 'g2', 'bottom'), null);
  assert.equal(splitRoot(extra, 'markdown:x.md', 'left'), null);
  extra = moveTab(extra, 'markdown:x.md', 'g2');
  assert.deepEqual(groupOf(extra, 'markdown:x.md')!.id, 'g2');
  // a lone tab dragged to another pane's edge frees its own pane, so the count stays 4
  assert.equal(groupsOf(splitTab(four, 'markdown:f1.md', 'g3', 'bottom')!.root).length, 4);
  invariants(four);
  invariants(extra);
});

test('rule 1: any shape is allowed: 2x2, and one wide pane over two narrow ones', () => {
  const grid = applyPreset(many(4), 'grid');
  assert.equal(grid.root.type === 'split' && grid.root.dir, 'col');
  let ws = defaultWorkspace();
  ws = splitRoot(openTab(ws, 'markdown', { path: 'a' }), 'markdown:a', 'bottom')!;
  ws = splitTab(openTab(ws, 'markdown', { path: 'b' }), 'markdown:b', groupOf(ws, 'markdown:a')!.id, 'right')!;
  assert.equal(ws.root.type === 'split' && ws.root.dir, 'col');
  assert.equal(groupsOf(ws.root).length, 3);
  invariants(grid);
  invariants(ws);
});

test('rule 2: an edge splits the pane, the strip merges, a free edge splits the root, presets rearrange', () => {
  let ws = openTab(two(), 'markdown', { path: 'n.md' }, 'g2');
  const right = splitTab(ws, 'markdown:n.md', 'g1', 'right')!;
  assert.deepEqual(ids(right), [CHAT, 'markdown:n.md', 'terminal:term:a']);
  const left = splitTab(ws, 'markdown:n.md', 'g1', 'left')!;
  assert.deepEqual(ids(left), ['markdown:n.md', CHAT, 'terminal:term:a']);
  const bottom = splitTab(ws, 'markdown:n.md', 'g2', 'bottom')!;
  assert.equal(bottom.root.type === 'split' && bottom.root.b.type === 'split' && bottom.root.b.dir, 'col');
  ws = moveTab(ws, 'markdown:n.md', 'g1', 0);
  assert.deepEqual(ids(ws), ['markdown:n.md+chat', 'terminal:term:a']);
  const rooted = splitRoot(ws, 'terminal:term:a', 'top')!;
  assert.equal(rooted.root.type === 'split' && rooted.root.dir, 'col');
  assert.deepEqual(ids(rooted)[0], 'terminal:term:a');
  const rows = applyPreset(two(), 'rows'),
    single = applyPreset(ws, 'single');
  assert.equal(rows.root.type === 'split' && rows.root.dir, 'col');
  assert.deepEqual(ids(single), ['markdown:n.md+chat+terminal:term:a']); // 3 tabs merge into one
  assert.deepEqual(ids(applyPreset(openTab(two(), 'markdown', { path: 'n.md' }), 'cols')), [CHAT, 'terminal:term:a+markdown:n.md']);
  assert.equal(groupsOf(applyPreset(defaultWorkspace(), 'grid').root).length, 1); // never more groups than tabs
  for (const w of [right, left, bottom, ws, rooted, rows, single]) invariants(w);
});

test('rule 3: Chat has no close; closeTab on it is a no-op; it can move; a saved layout without it gets one', () => {
  const ws = two();
  assert.equal(closeTab(ws, CHAT), ws);
  const moved = moveTab(ws, CHAT, 'g2');
  assert.deepEqual(ids(moved), ['terminal:term:a+chat']);
  assert.equal(closeTab(moved, CHAT), moved);
  const saved = JSON.parse(serialize(two()));
  delete saved.tabs.chat;
  saved.root.a.tabs = ['terminal:x'];
  saved.root.a.activeTab = 'terminal:x';
  saved.tabs['terminal:x'] = { id: 'terminal:x', kind: 'terminal', params: { handle: 'x' } };
  saved.active = 'g1';
  const fixed = parseWorkspace(saved)!;
  assert.ok(fixed.tabs[CHAT]);
  assert.equal(groupOf(fixed, CHAT)!.id, 'g1');
  invariants(fixed);
});

test("rule 4: closing a group's last tab removes the group and the sibling takes the space; the focus moves with it", () => {
  let ws = openTab(two(), 'markdown', { path: 'n.md' }, 'g2');
  ws = closeTab(ws, 'terminal:term:a');
  assert.deepEqual(ids(ws), [CHAT, 'markdown:n.md']);
  assert.equal(groupOf(ws, 'markdown:n.md')!.activeTab, 'markdown:n.md');
  ws = closeTab(ws, 'markdown:n.md');
  assert.equal(ws.root.type, 'group');
  assert.equal(ws.active, 'g1');
  assert.equal(closeTab(ws, 'nope'), ws);
  invariants(ws);
});

test('rule 5: dividers clamp, double-click resets, a small window or a phone shows one group and keeps the layout', () => {
  let ws = two();
  assert.equal((setSplit(ws, [], 0.01).root as { at: number }).at, 0.15);
  assert.equal((setSplit(ws, [], 2).root as { at: number }).at, 0.85);
  ws = setSplit(ws, [], 0.3);
  assert.equal((setSplit(ws, [], 0.5).root as { at: number }).at, 0.5);
  const nested = splitTab(openTab(ws, 'markdown', { path: 'n' }), 'markdown:n', 'g2', 'bottom')!;
  assert.equal((setSplit(nested, ['b'], 0.7).root as unknown as { b: { at: number } }).b.at, 0.7);
  const wide = arrange(ws, 1280, 800);
  assert.equal(wide.merged, false);
  assert.deepEqual(wide.merged === false && wide.rects.map((r) => Math.round(r.rect.w)), [384, 896]);
  assert.deepEqual(arrange(ws, 600, 800), { merged: true, tabs: [CHAT, 'terminal:term:a'], activeTab: 'terminal:term:a' }); // phone
  assert.equal(arrange(ws, 1000, 800).merged, true); // 0.3 * 1000 < 320
  assert.equal(arrange(ws, 1280, 300).merged, false);
  assert.equal(arrange(setSplit(ws, [], 0.5), 1000, 300).merged, false); // 500 x 300 each
  assert.equal(arrange(applyPreset(ws, 'rows'), 1280, 300).merged, true); // 150 high panes
  assert.equal(arrange(defaultWorkspace(), 300, 100).merged, false); // one group is never merged
  assert.deepEqual(ids(ws), [CHAT, 'terminal:term:a']); // arranging did not touch the layout
});

test('rule 6: opening the same thing twice focuses its tab', () => {
  let ws = openTab(two(), 'markdown', { path: 'n.md' }, 'g1');
  ws = openTab(ws, 'terminal', { handle: 'term:a' });
  assert.equal(ws.active, 'g2');
  const again = openTab(ws, 'markdown', { path: 'n.md' }, 'g2');
  assert.equal(Object.keys(again.tabs).length, 3);
  assert.equal(again.active, 'g1');
  assert.equal(groupOf(again, 'markdown:n.md')!.activeTab, 'markdown:n.md');
  assert.equal(openTab(ws, 'web', { port: 3000 }).tabs['web:3000']!.kind, 'web');
  assert.deepEqual(
    Object.keys(openTab(openTab(ws, 'shell'), 'shell').tabs).filter((t) => t.startsWith('shell')),
    ['shell:1', 'shell:2'],
  );
  invariants(again);
});

test('rule 7: closing the session tab leaves a way back: opening the terminal again restores it', () => {
  let ws = closeTab(two(), 'terminal:term:a');
  assert.deepEqual(ids(ws), [CHAT]);
  ws = openTab(ws, 'terminal', { handle: 'term:a' });
  assert.deepEqual(ids(ws), [CHAT + '+terminal:term:a']);
});

test('keyboard: a tab moves to the neighbouring group and stops at the ends', () => {
  const ws = openTab(two(), 'markdown', { path: 'n.md' }, 'g1');
  const r = moveTabBy(ws, 'markdown:n.md', 1);
  assert.deepEqual(ids(r), [CHAT, 'terminal:term:a+markdown:n.md']);
  assert.equal(moveTabBy(r, 'markdown:n.md', 1), r);
  assert.equal(moveTabBy(ws, 'markdown:n.md', -1), ws);
  assert.equal(activate(ws, 'terminal:term:a').active, 'g2');
});

test('save and load round-trip; a newer version, bad shapes and blocked storage give the default', () => {
  const ws = setSplit(splitTab(openTab(two(), 'markdown', { path: 'n.md' }), 'markdown:n.md', 'g2', 'bottom')!, [], 0.4);
  assert.deepEqual(parseWorkspace(JSON.parse(serialize(ws))), ws);
  const mem = new Map<string, string>();
  const kv: KV = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => void mem.set(k, v) };
  const key = storageKey('d1', 'handline.architect');
  assert.equal(key, 'handline.workspace.v1/d1/handline.architect');
  saveWorkspace(kv, key, ws);
  assert.deepEqual(loadWorkspace(kv, key, two), ws);
  const dflt = () => defaultWorkspace();
  mem.set(key, JSON.stringify({ ...ws, v: 2 }));
  assert.deepEqual(loadWorkspace(kv, key, dflt), dflt());
  for (const bad of ['{', '"x"', '{"v":1}', JSON.stringify({ ...ws, active: 5, root: 5 })]) {
    mem.set(key, bad);
    assert.deepEqual(loadWorkspace(kv, key, dflt), dflt());
  }
  const five = JSON.parse(serialize(many(4)));
  five.root = { type: 'split', dir: 'row', a: five.root, b: { type: 'group', id: 'g9', tabs: ['x'], activeTab: 'x' }, at: 0.5 };
  five.tabs.x = { id: 'x', kind: 'shell', params: {} };
  assert.equal(parseWorkspace(five), null); // 5 groups
  const dup = JSON.parse(serialize(two()));
  dup.root.b.tabs.push('chat');
  assert.equal(parseWorkspace(dup), null); // a tab in two places
  const blocked: KV = {
    getItem: () => {
      throw new Error('blocked');
    },
    setItem: () => {
      throw new Error('blocked');
    },
  };
  assert.deepEqual(loadWorkspace(blocked, key, dflt), dflt());
  saveWorkspace(blocked, key, ws);
  assert.deepEqual(loadWorkspace(null, key, dflt), dflt());
});

test('property: after any run of random operations, Chat is there and every invariant holds', () => {
  let seed = 7;
  const rnd = (n: number) => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed % n;
  };
  const edges: Edge[] = ['left', 'right', 'top', 'bottom'];
  for (let run = 0; run < 60; run++) {
    let ws = rnd(2) ? two() : defaultWorkspace();
    for (let step = 0; step < 60; step++) {
      const tabs = Object.keys(ws.tabs),
        gs = groupsOf(ws.root);
      const t = tabs[rnd(tabs.length)]!,
        g = gs[rnd(gs.length)]!.id;
      switch (rnd(10)) {
        case 0:
          ws = openTab(ws, ['markdown', 'terminal', 'web', 'shell'][rnd(4)] as 'web', { path: `p${rnd(4)}`, port: rnd(3) }, g);
          break;
        case 1:
        case 2:
          ws = closeTab(ws, t);
          break;
        case 3:
          ws = moveTab(ws, t, g, rnd(3));
          break;
        case 4:
        case 5:
          ws = splitTab(ws, t, g, edges[rnd(4)]!) ?? ws;
          break;
        case 6:
          ws = splitRoot(ws, t, edges[rnd(4)]!) ?? ws;
          break;
        case 7:
          ws = applyPreset(ws, (['single', 'cols', 'rows', 'grid'] as const)[rnd(4)]!);
          break;
        case 8:
          ws = moveTabBy(ws, t, rnd(2) ? 1 : -1);
          break;
        default:
          ws = setSplit(ws, [rnd(2) ? 'a' : 'b'], rnd(100) / 50 - 0.5);
          break;
      }
      invariants(ws);
      assert.deepEqual(parseWorkspace(JSON.parse(serialize(ws))), ws);
    }
  }
});

test('the default layout is one pane: Chat shown, the session terminal second behind it; no session, Chat alone', () => {
  const ws = defaultWorkspace({ terminal: 'term:a' });
  assert.equal(ws.root.type, 'group');
  if (ws.root.type !== 'group') return;
  assert.deepEqual(ws.root.tabs, [CHAT, 'terminal:term:a']);
  assert.equal(ws.root.activeTab, CHAT);
  assert.equal(ws.tabs['terminal:term:a']!.params.handle, 'term:a');
  assert.equal(ws.active, 'g1');
  const later = addTab(defaultWorkspace(), 'terminal', { handle: 'term:b' });
  assert.deepEqual(groupsOf(later.root)[0]!.tabs, [CHAT, 'terminal:term:b']);
  assert.equal(groupsOf(later.root)[0]!.activeTab, CHAT);
  assert.equal(addTab(later, 'terminal', { handle: 'term:b' }), later);
  const none = defaultWorkspace();
  assert.equal(none.root.type, 'group');
  assert.deepEqual(ids(none), [CHAT]);
  invariants(ws);
  invariants(none);
});

test('a structured agent opens with Chat and Transcript, and the transcript tab is one per workspace', () => {
  const ws = defaultWorkspace({ structured: true });
  assert.deepEqual(ids(ws), [`${CHAT}+${TRANSCRIPT}`]);
  assert.deepEqual(openTab(ws, 'transcript'), activate(ws, TRANSCRIPT));
  assert.deepEqual(parseWorkspace(JSON.parse(serialize(ws))), ws);
  invariants(ws);
});

test('openTask opens the Tasks tab on that task, and moves an open one to another task', () => {
  const ws = defaultWorkspace();
  const a = openTask(ws, 3);
  assert.equal(a.tabs.tasks!.params.task, 3);
  assert.equal(a.active, ws.active);
  const b = openTask(a, 9);
  assert.equal(b.tabs.tasks!.params.task, 9);
  assert.equal(Object.keys(b.tabs).filter((k) => k === 'tasks').length, 1);
});
