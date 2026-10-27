import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Event } from '@handline/client';
import { setup, until } from '@handline/fake-daemon/helpers';
import { Desk, type Feed, type Ports } from '#src/desk.ts';
import { badgeText, noticeFor, suppressed, type Notice } from '#src/notices.ts';
import { serveDir } from '#src/serve.ts';

const ev = (over: Partial<Event>): Event => ({
  cursor: 1,
  topic: 'notifications',
  type: 'stall',
  at: '2026-01-01T00:00:00Z',
  actor: null,
  agent: 'handline.architect',
  title: null,
  target: null,
  data: {},
  ...over,
});

/** The daemon's answers to the three lists My turn is built from, with `n` unread decisions waiting. */
const lists = (n: number, m: string) =>
  m === 'inbox.list'
    ? {
        items: Array.from({ length: n }, (_, i) => ({
          id: i + 1,
          kind: 'decision',
          agent: 'a',
          title: 't',
          body: '',
          task: null,
          read: false,
          created: '2026-10-15T00:00:00Z',
        })),
      }
    : m === 'agents.list'
      ? { agents: [] }
      : { items: [] };

test('the badge shows the My turn count, nothing for none, and 99+ past two digits', () => {
  assert.deepEqual([0, 1, 12, 99, 100, -1].map(badgeText), ['', '1', '12', '99', '99+', '']);
});

test('the four notifications events raise a notice naming what happened; other events raise none', () => {
  assert.equal(noticeFor(ev({ type: 'prompt_asked', title: 'Permission: Bash' }))?.title, 'handline.architect is asking');
  assert.equal(noticeFor(ev({ type: 'task_done', data: { task: 12 }, title: 'Chat tab' }))?.title, 'Task 12 done');
  assert.equal(noticeFor(ev({ type: 'stall' }))?.title, 'handline.architect is stalled');
  assert.equal(noticeFor(ev({ type: 'account_out', agent: null, data: { account: 'alpha' } }))?.title, 'Account alpha is out');
  assert.equal(noticeFor(ev({ type: 'inbox.added', topic: 'inbox' })), null);
  assert.equal(noticeFor(ev({ type: 'something_new' })), null);
});

test('a notice is held back only while the window is focused on that agent', () => {
  const n = noticeFor(ev({ type: 'stall' }))!;
  assert.equal(suppressed(n, { focused: true, agent: 'handline.architect' }), true);
  assert.equal(suppressed(n, { focused: false, agent: 'handline.architect' }), false);
  assert.equal(suppressed(n, { focused: true, agent: 'handline.auditor' }), false);
  assert.equal(suppressed(n, { focused: true, agent: null }), false); // before the page has called screen
  assert.equal(
    suppressed(noticeFor(ev({ type: 'account_out', agent: null, data: { account: 'alpha' } }))!, { focused: true, agent: null }),
    false,
  );
});

const hr = (over: Partial<Event> = {}): Event =>
  ev({ topic: 'hr', type: 'hr.raised', title: 'Add a reviewer', target: { screen: 'hr', id: '5' }, ...over });

test('hr.raised raises a notice with the proposal title as plain text; hr.settled and other hr events raise none', () => {
  const n = noticeFor(hr())!;
  assert.deepEqual([n.title, n.body, n.target, n.event], ['HR proposal', 'Add a reviewer', { screen: 'hr', id: '5' }, 'hr.raised']);
  assert.equal(noticeFor(hr({ title: 'a\u0007b\nc\u0000' }))!.body, 'abc');
  assert.equal(noticeFor(hr({ title: 'x'.repeat(300) }))!.body.length, 200);
  assert.equal(noticeFor(hr({ title: null }))!.body, 'A proposal is waiting');
  assert.equal(noticeFor(hr({ type: 'hr.settled' })), null);
});

test('an HR notice is not held back by the agent on screen, even for a proposal of that agent', () => {
  const n = noticeFor(hr({ agent: 'handline.architect' }))!;
  assert.equal(suppressed(n, { focused: true, agent: 'handline.architect' }), false);
});

test('the desk subscribes to hr and raises a notice for hr.raised only', async () => {
  const handlers: { event?: (e: Event) => void } = {};
  const asked: string[][] = [];
  const feed = {
    onEvent: (f: (e: Event) => void) => {
      handlers.event = f;
      return () => {};
    },
    onGap: () => () => {},
    onState: () => () => {},
    subscribe: async (t: string[]) => void asked.push(t),
    call: async (m: string) => lists(0, m),
  } as unknown as Feed;
  const { log, p } = ports();
  await new Desk(feed, p).start();
  assert.deepEqual(asked, [['inbox', 'status', 'notifications', 'agents', 'tasks'], ['hr']]);
  handlers.event!(hr({ cursor: 2 }));
  handlers.event!(hr({ cursor: 3, type: 'hr.settled' }));
  handlers.event!(hr({ cursor: 2 })); // a replayed cursor is not raised twice
  assert.deepEqual(log.notices, ['HR proposal']);
  log.clicks[0]!();
  assert.deepEqual(
    log.opened.map((n) => n.target),
    [{ screen: 'hr', id: '5' }],
  );
});

test('a daemon that refuses the hr topic still gives the badge and the other notices', async () => {
  const { daemon, client } = setup();
  daemon.unservedTopics.add('hr'); // a daemon that does not serve hr
  const { log, p } = ports();
  await client.start();
  const desk = new Desk(client, p);
  await desk.start();
  daemon.emit('notifications', 'stall', { agent: 'handline.architect' });
  await until(() => log.notices.length === 1, 'a notice');
  assert.equal(log.badge.length, 1); // the badge was set from the My turn count
  // The refused topic is not kept: a later subscribe or a reconnect would send it again and be refused whole.
  await client.subscribe(['agents']);
  desk.stop();
  client.close();
});

test('a daemon that serves hr raises the notice for hr.raised', async () => {
  const { daemon, client } = setup();
  const { log, p } = ports();
  await client.start();
  const desk = new Desk(client, p);
  await desk.start();
  daemon.emit('hr', 'hr.raised', { title: 'Add a reviewer' });
  await until(() => log.notices.length === 1, 'an HR notice');
  assert.deepEqual(log.notices, ['HR proposal']);
  desk.stop();
  client.close();
});

function ports() {
  const log = {
    badge: [] as string[],
    notices: [] as string[],
    clicks: [] as (() => void)[],
    opened: [] as Notice[],
    screen: { focused: false, agent: null as string | null },
  };
  const p: Ports = {
    setBadge: (t, n) => log.badge.push(n === 0 || t === String(n) ? t : `${t}/${n}`),
    notify: (n, click) => {
      log.notices.push(n.title);
      log.clicks.push(click);
    },
    screen: () => log.screen,
    open: (n) => log.opened.push(n),
  };
  return { log, p };
}

test('the desk raises one notice per event, none on screen, and a click opens that agent', async () => {
  const { daemon, client } = setup();
  const { log, p } = ports();
  await client.start();
  const desk = new Desk(client, p);
  await desk.start();
  daemon.emit('notifications', 'stall', { agent: 'handline.architect' });
  await until(() => log.notices.length === 1, 'a notice');
  log.screen = { focused: true, agent: 'handline.auditor' };
  daemon.emit('notifications', 'stall', { agent: 'handline.auditor' });
  daemon.emit('notifications', 'task_done', { agent: 'handline.pc-build', data: { task: 7 } });
  await until(() => log.notices.length === 2, 'the second notice');
  assert.deepEqual(log.notices, ['handline.architect is stalled', 'Task 7 done']);
  log.clicks[0]!();
  assert.deepEqual(
    log.opened.map((n) => [n.agent, n.target, n.event]),
    [['handline.architect', { screen: 'agent', id: 'handline.architect' }, 'stall']],
  );
  desk.stop();
  client.close();
});

test('the badge follows the My turn count on start, on inbox events and on reconnect', async () => {
  let unread = 3;
  const handlers: { event?: (e: Event) => void; state?: (s: string) => void } = {};
  const feed = {
    onEvent: (f: (e: Event) => void) => {
      handlers.event = f;
      return () => {};
    },
    onGap: () => () => {},
    onState: (f: (s: string) => void) => {
      handlers.state = f;
      return () => {};
    },
    subscribe: async () => {},
    call: async (m: string) => lists(unread, m),
  } as unknown as Feed;
  const { log, p } = ports();
  await new Desk(feed, p).start();
  assert.deepEqual(log.badge, ['3']);
  unread = 5;
  handlers.event!(ev({ topic: 'inbox', type: 'inbox.added', cursor: 2 }));
  await until(() => log.badge.length === 2);
  unread = 0;
  handlers.state!('open');
  await until(() => log.badge.length === 3);
  assert.deepEqual(log.badge, ['3', '5', '']);
});

test('a desk whose subscribe failed once subscribes on the next start, with its handlers added once', async () => {
  let fails = 1,
    listeners = 0;
  const feed = {
    onEvent: () => {
      listeners++;
      return () => {};
    },
    onGap: () => () => {},
    onState: () => () => {},
    subscribe: async () => {
      if (fails-- > 0) throw new Error('refused');
    },
    call: async (m: string) => lists(2, m),
  } as unknown as Feed;
  const { log, p } = ports();
  const desk = new Desk(feed, p);
  await assert.rejects(desk.start());
  assert.deepEqual(log.badge, []);
  await desk.start();
  assert.deepEqual(log.badge, ['2']);
  assert.equal(listeners, 1);
});

test('the built web app is served from a loopback port, any route falling back to the page', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hl-electron-'));
  fs.mkdirSync(path.join(dir, 'assets'));
  fs.writeFileSync(path.join(dir, 'index.html'), '<div id="root"></div>');
  fs.writeFileSync(path.join(dir, 'assets', 'a.js'), 'x');
  const web = await serveDir(dir);
  try {
    assert.match(web.url, /^http:\/\/127\.0\.0\.1:\d+\/$/);
    const page = await fetch(web.url);
    assert.equal(await page.text(), '<div id="root"></div>');
    assert.match(page.headers.get('content-type') ?? '', /text\/html/);
    const js = await fetch(`${web.url}assets/a.js`);
    assert.equal(await js.text(), 'x');
    assert.match(js.headers.get('content-type') ?? '', /javascript/);
    assert.equal(await (await fetch(`${web.url}agents/x`)).text(), '<div id="root"></div>');
    assert.equal(await (await fetch(`${web.url}..%2f..%2fetc/passwd`)).text(), '<div id="root"></div>');
    assert.equal(await (await fetch(`${web.url}assets/`)).text(), '<div id="root"></div>'); // a directory is the page, not a listing
    // A malformed escape is answered, not thrown: the server keeps serving.
    assert.equal((await fetch(`${web.url}%E0%A4%A`)).status, 400);
    assert.equal((await fetch(web.url)).status, 200);
  } finally {
    web.close();
    fs.rmSync(dir, { recursive: true });
  }
});

test('the page keeps its port from launch to launch, and takes a free one when that port is held', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hl-electron-'));
  fs.writeFileSync(path.join(dir, 'index.html'), 'page');
  const first = await serveDir(dir);
  const port = new URL(first.url).port;
  const second = await serveDir(dir, Number(port)); // held: it still comes up, elsewhere
  try {
    assert.notEqual(new URL(second.url).port, port);
    assert.equal(await (await fetch(second.url)).text(), 'page');
    first.close();
    const again = await serveDir(dir, Number(port));
    assert.equal(new URL(again.url).port, port);
    again.close();
  } finally {
    second.close();
    first.close();
    fs.rmSync(dir, { recursive: true });
  }
});

test('a push switch that is off holds back that notice only, from the next event on, and a daemon without the key raises all', async () => {
  const { daemon, client } = setup();
  const { log, p } = ports();
  await client.start();
  const desk = new Desk(client, p);
  await desk.start();
  daemon.emit('notifications', 'task_done', { agent: 'handline.pc', data: { task: 1 } });
  await until(() => log.notices.length === 1, 'a notice with every switch on');
  await client.call('settings.set', { changes: { push_task_done: false } });
  daemon.emit('notifications', 'task_done', { agent: 'handline.pc', data: { task: 2 } });
  daemon.emit('notifications', 'stall', { agent: 'handline.pc' });
  await until(() => log.notices.length === 2, 'the stall');
  assert.deepEqual(log.notices, ['Task 1 done', 'handline.pc is stalled']);
  await client.call('settings.set', { changes: { push_task_done: true } });
  daemon.emit('notifications', 'task_done', { agent: 'handline.pc', data: { task: 3 } });
  await until(() => log.notices.length === 3, 'the switch back on');
  desk.stop();
  client.close();
});

test('a daemon that refuses settings.get raises every notice', async () => {
  const handlers: { event?: (e: Event) => void } = {};
  const feed = {
    onEvent: (f: (e: Event) => void) => {
      handlers.event = f;
      return () => {};
    },
    onGap: () => () => {},
    onState: () => () => {},
    subscribe: async () => undefined,
    call: async (m: string) => {
      if (m === 'settings.get') throw new Error('refused');
      return lists(0, m);
    },
  } as unknown as Feed;
  const { log, p } = ports();
  await new Desk(feed, p).start();
  handlers.event!(ev({ cursor: 9, type: 'task_done', data: { task: 4 } }));
  await until(() => log.notices.length === 1, 'the notice');
});

test('past 99 the badge and the tray line say 99+ while the exact count goes to the tray', async () => {
  const feed = {
    onEvent: () => () => {},
    onGap: () => () => {},
    onState: () => () => {},
    subscribe: async () => undefined,
    call: async (m: string) => lists(120, m),
  } as unknown as Feed;
  const { log, p } = ports();
  await new Desk(feed, p).start();
  assert.deepEqual(log.badge, ['99+/120']);
});

test('a burst of agents and tasks events refreshes the badge once, after the burst', async () => {
  const handlers: { event?: (e: Event) => void } = {};
  let calls = 0;
  const feed = {
    onEvent: (f: (e: Event) => void) => {
      handlers.event = f;
      return () => {};
    },
    onGap: () => () => {},
    onState: () => () => {},
    subscribe: async () => undefined,
    call: async (m: string) => (m === 'inbox.list' && calls++, lists(1, m)),
  } as unknown as Feed;
  const { log, p } = ports();
  const desk = new Desk(feed, p);
  await desk.start();
  for (const topic of ['agents', 'tasks', 'agents'] as const) handlers.event!(ev({ topic, type: `${topic}.changed`, cursor: 9 }));
  assert.equal(calls, 1);
  await until(() => calls === 2);
  await new Promise((r) => setTimeout(r, 450));
  assert.equal(calls, 2);
  assert.equal(log.badge.length, 2);
  desk.stop();
});
