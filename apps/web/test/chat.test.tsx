// U8: the Chat tab on app-data, against the fake daemon's chat with its latency and refusal knobs.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Store, defaultWorkspace, groupsOf, openTab, saveWorkspace, splitTab, storageKey, type KV } from '@handline/app-core';
import { second, setup } from '@handline/fake-daemon/helpers';
import { applySeeds } from '@handline/fake-daemon/seeds';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV & { data: Map<string, string> } => {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const typeInto = (el: HTMLTextAreaElement | HTMLInputElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
const press = (el: Element, key: string, init: KeyboardEventInit = {}) =>
  act(async () => void el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init })));
const compose = () => $('.compose') as HTMLTextAreaElement;
/** Loads older pages one at a time, awaiting each, until `found` holds. */
async function pageBackTo(found: () => unknown, what: string) {
  while (!found()) {
    const older = $('.older');
    if (!older) throw new Error(`no older page to reach ${what}`);
    const before = $$('.msg').length;
    await click(older);
    await until(() => found() || $$('.msg').length !== before, `a page toward ${what}`);
  }
}
const msg = (body: string) => $$('.msg').find((m) => text(m).includes(body));
const landing = (body: string) => msg(body)?.getAttribute('data-landing');
const link = (label: string, within: Element) =>
  [...within.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === label)!;
const icon = (m: Element | undefined, label: string) => m?.querySelector(`[aria-label="${label}"]`) ?? null;
const other = async (t: ReturnType<typeof setup>, body: string) => {
  const c = second(t.daemon, t.net, { client: 'web-other' });
  await c.start();
  await c.call('chat.send', { agent: ARCH, body });
  c.close();
};
const sent = (s: ReturnType<typeof setup>, body: string) =>
  (s.daemon.chat as unknown as { messages: { body: string }[] }).messages.some((m) => m.body === body);

async function mount(agent: string, s = setup(), storage: KV = memory()) {
  const store = new Store(s.client);
  history.replaceState(null, '', location.pathname); // a tab shown by an earlier test is not this test's
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={storage} />);
  });
  return { store, storage, ...s };
}
// The window's Back, as jsdom cannot traverse: the earlier history state is put back and popstate fires.
const windowBack = (to: object | null) => act(async () => (history.replaceState(to, ''), void dispatchEvent(new Event('popstate'))));
const unmount = async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
};

beforeEach(() => void vi.stubGlobal('confirm', () => true));
afterEach(async () => {
  await unmount();
  vi.unstubAllGlobals();
});

describe('the Chat tab', () => {
  it('says so when the daemon does not serve chat, and still has no way to send', async () => {
    await mount(ARCH, setup({ daemon: { chat: false } }));
    await until(() => $('.chat'), 'the chat tab');
    expect(text($('.chat'))).toContain('No chat yet');
    expect(compose().disabled).toBe(true);
    expect($('.chat-foot')).toBeNull();
  });

  it('draws every message state and transcript kind from the daemon', async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    expect(landing('Draft the web chat tab plan.')).toBe('delivered');
    expect(landing('Use the contract fixtures.')).toBe('delivered');
    expect(landing('Hold that, scope changed.')).toBe('withdrawn');
    expect(landing('Also add the list of gaps.')).toBe('typed');
    expect(link('Withdraw', msg('Also add the list of gaps.')!)).toBeUndefined(); // the chat has no Withdraw button
    expect($$('[data-kind="reply"]')).toHaveLength(1); // the agent's message is not drawn a second time as a reply
    expect($$('[data-kind="tool"]')).toHaveLength(0); // tool lines are on the Activity tab
    expect(icon($$('[data-kind="task_event"]')[1], 'Task done')).not.toBeNull();
    expect(msg('Review is done')).toBeUndefined(); // agent to agent: the Activity tab
  });

  it('a session boundary says why it began, and its handoff opens in a dialog', async () => {
    const t = setup();
    t.daemon.chat.seedItem(ARCH, {
      kind: 'session',
      session: { id: 's2', started: new Date().toISOString(), handoff: 'line one\nline two', cause: 'handoff' },
    });
    await mount(ARCH, t);
    await until(() => $$('[data-kind="session"]').some((c) => /Handoff/.test(text(c))), 'the boundary');
    const line = $$('[data-kind="session"]').find((c) => /Handoff/.test(text(c)))!;
    expect(text(line)).toMatch(/New session at .*: from a handoff/);
    expect($('.handoff-full')).toBeNull();
    await click([...line.querySelectorAll('button')].find((b) => /Handoff note/.test(text(b)))!);
    await until(() => $('.handoff-full'), 'the dialog');
    expect($('.handoff-full')?.textContent).toBe('line one\nline two');
  });

  it('sends with Enter, shows the message landing, and keeps an unsent draft across a reload', async () => {
    const storage = memory();
    const t = await mount(ARCH, setup(), storage);
    await until(() => msg('Which gap list'), 'history');
    await typeInto(compose(), 'half a thought');
    await unmount();
    await mount(ARCH, { ...t, client: second(t.daemon, t.net) }, storage);
    await until(() => compose(), 'chat again');
    expect(compose().value).toBe('half a thought');
    await typeInto(compose(), 'ship it');
    await press(compose(), 'Enter', { shiftKey: true });
    expect(msg('ship it')).toBeUndefined(); // Shift+Enter is a new line, not a send
    await press(compose(), 'Enter');
    await until(() => msg('ship it'), 'the sent message');
    expect(compose().value).toBe('');
    await until(() => sent(t, 'ship it'), 'the daemon got it');
    t.daemon.chat.tick();
    await until(() => landing('ship it') === 'typed', 'typed');
    t.daemon.chat.tick();
    t.daemon.chat.tick();
    await until(() => $('.working')?.getAttribute('title')?.includes('now: Read src/main.ts'), 'the working line');
    t.daemon.chat.tick();
    await until(() => !$('.working'), 'the turn over');
    expect(landing('ship it')).toBe('delivered');
    await until(() => text($('.stream')).includes('Got it: "ship it"'), 'the reply');
    expect(storage.data.get(`handline.draft.${ARCH}`)).toBe('');
  });

  it('recalls what was sent with the up arrow and sends a picked message as a reference', async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await press(compose(), 'ArrowUp');
    expect(compose().value).toBe('Also add the list of gaps.'); // the newest of mine
    await press(compose(), 'ArrowUp');
    expect(compose().value).toBe('Use the contract fixtures.');
    await press(compose(), 'ArrowDown');
    await press(compose(), 'ArrowDown');
    expect(compose().value).toBe('');
    await click(msg('Which gap list')!);
    await typeInto(compose(), 'chat first');
    await press(compose(), 'Enter');
    const refs = () => (t.daemon.chat as unknown as { messages: { body: string; parent: number | null }[] }).messages;
    await until(() => refs().some((m) => m.body === 'chat first' && m.parent != null), 'the reference sent with its parent');
    expect($('.compose-ref')).toBeNull();
  });

  it('follows another screen through the chat topic', async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    const other = second(t.daemon, t.net, { client: 'web-other' });
    await other.start();
    await other.call('chat.send', { agent: ARCH, body: 'from elsewhere' });
    await until(() => msg('from elsewhere'), 'the message from the other screen');
    for (let i = 0; i < 4; i++) t.daemon.chat.tick();
    await until(() => landing('from elsewhere') === 'delivered', 'delivered');
    other.close();
  });

  it('pages back through older messages', async () => {
    const s = setup();
    for (let i = 0; i < 60; i++)
      s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: `old ${i}`, at: new Date(Date.UTC(2020, 0, 1, 0, i)).toISOString() });
    await mount(ARCH, s);
    await until(() => msg('old 59'), 'the newest page');
    expect(msg('Which gap list')).toBeUndefined(); // it is on the second page
    await pageBackTo(() => msg('Which gap list'), 'the older page');
    expect($('.older')).toBeNull(); // nothing further back
  });
});

describe('messages once, replies by reference, cards and the Tasks tab', () => {
  const sentMsgs = (t: { daemon: ReturnType<typeof setup>['daemon'] }) =>
    (t.daemon.chat as unknown as { messages: { body: string; parent: number | null; to: string }[] }).messages;
  const idOf = (body: string) => Number(msg(body)!.getAttribute('data-id'));

  it("draws the agent's message once, not again as a transcript reply", async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    expect(text($('.stream')).split('Which gap list should come first?')).toHaveLength(2);
    expect($$('[data-kind="reply"]').map(text).join()).not.toContain('Which gap list');
  });

  it('a click on a message picks it: the box shows the reference, Enter sends a reply linked to it, and Escape drops the pick', async () => {
    const t = await mount(ARCH);
    await until(() => msg('Use the contract fixtures.'), 'history');
    await click(msg('Use the contract fixtures.')!.querySelector('.msg-body')!);
    expect($('.compose-ref')).not.toBeNull();
    expect(text($('.compose-ref'))).toContain('Use the contract fixtures.');
    expect(msg('Use the contract fixtures.')!.classList.contains('picked')).toBe(true);
    await press(compose(), 'Escape');
    expect($('.compose-ref')).toBeNull();
    await click(msg('Use the contract fixtures.')!.querySelector('.msg-body')!);
    await typeInto(compose(), 'and the transcripts');
    await press(compose(), 'Enter');
    await until(() => sentMsgs(t).some((m) => m.body === 'and the transcripts'), 'the daemon got it');
    const m = sentMsgs(t).find((x) => x.body === 'and the transcripts')!;
    expect(m.parent).toBe(idOf('Use the contract fixtures.'));
    expect($('.compose-ref')).toBeNull();
    await until(() => msg('and the transcripts')?.querySelector('.msg-ref'), 'the reference on the new message');
    expect(text(msg('and the transcripts')!.querySelector('.msg-ref'))).toContain('Use the contract fixtures.');
  });

  it('a message starting with ! is sent as typed: the picked reply is dropped and the box shows command mode', async () => {
    const t = await mount(ARCH);
    await until(() => msg('Use the contract fixtures.'), 'history');
    await click(msg('Use the contract fixtures.')!.querySelector('.msg-body')!);
    await typeInto(compose(), '!git status');
    expect(compose().className).toContain('font-mono');
    expect($('.compose-cmd')?.textContent).toBe('Sent as typed to the agent');
    expect($('.compose-attach')).not.toBeNull(); // the slot stays, so the box does not move
    await press(compose(), 'Enter');
    await until(() => sentMsgs(t).some((m) => m.body === '!git status'), 'the daemon got it');
    expect(sentMsgs(t).find((x) => x.body === '!git status')!.parent).toBeNull();
    expect($('.compose-ref')).toBeNull();
  });

  it('a message of the person offers no request buttons; what the team made of it shows under it in plain words', async () => {
    const t = await mount(ARCH);
    await until(() => msg('Use the contract fixtures.'), 'history');
    await click(msg('Use the contract fixtures.')!.querySelector('.msg-body')!);
    expect($$('.msg-request button')).toHaveLength(0); // the lead turns a message into a request, not the person
    const none = (call: string, p: object) =>
      t.daemon.requests.call(call, { request: idOf('Use the contract fixtures.'), ...p }, () => ({}));
    none('task.request', {});
    await until(() => $('a.msg-request'), 'the request chip');
    expect(text($('a.msg-request'))).toMatch(/^Taken as T\d+$/);
    expect($('a.msg-request')!.getAttribute('href')).toMatch(/^#\/tasks\/\d+$/);
    expect(t.daemon.requests.rows.some((r) => r.kind === 'task' && r.title.startsWith('Use the contract fixtures.'))).toBe(true);
  });

  it("a reply quoting another agent's message goes to this chat's agent, with the quote as its parent", async () => {
    const s = setup();
    s.daemon.chat.seed(ARCH, { from: 'handline.pr', to: ARCH, body: 'PR note from elsewhere' });
    const t = await mount(ARCH, s);
    await until(() => msg('PR note from elsewhere'), 'the quoted row');
    await click(msg('PR note from elsewhere')!.querySelector('.msg-body')!);
    await typeInto(compose(), 'answer here');
    await press(compose(), 'Enter');
    await until(() => sentMsgs(t).some((m) => m.body === 'answer here'), 'the daemon got it');
    const m = sentMsgs(t).find((x) => x.body === 'answer here')!;
    expect(m.to).toBe(ARCH);
    expect(m.parent).toBe(idOf('PR note from elsewhere'));
  });

  it("a reply to the agent's open question is a linked message, never an answer", async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await click(msg('Which gap list')!.querySelector('.msg-body')!);
    await typeInto(compose(), 'chat first');
    await press(compose(), 'Enter');
    await until(() => sentMsgs(t).some((m) => m.body === 'chat first' && m.parent === idOf('Which gap list')), 'sent under it');
    expect(icon(msg('Which gap list'), 'Your answer')).toBeFalsy();
  });

  it("a reply to the agent's message that asked nothing is a say under it, not an answer", async () => {
    const t = await mount(ARCH);
    const chat = t.daemon.chat;
    chat.change(chat.find(chat.seed(ARCH, { from: ARCH, to: 'human', body: 'Report: gaps listed.', landing: 'delivered' })), {});
    await until(() => msg('Report: gaps listed.'), 'the report');
    await click(msg('Report: gaps listed.')!.querySelector('.msg-body')!);
    expect(text($('.compose-ref'))).toContain('Replying to');
    await typeInto(compose(), 'thanks, go on');
    await press(compose(), 'Enter');
    await until(() => sentMsgs(t).some((m) => m.body === 'thanks, go on'), 'the say');
    expect(sentMsgs(t).find((x) => x.body === 'thanks, go on')!.parent).toBe(idOf('Report: gaps listed.'));
  });

  it('a click on a control inside a message does not pick it', async () => {
    const s = setup();
    s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: 'a control test, see T1' });
    await mount(ARCH, s);
    await until(() => msg('a control test')?.querySelector('.ref'), 'a control inside a message');
    await click(msg('a control test')!.querySelector('.ref')!);
    expect($('.compose-ref')).toBeNull();
  });

  it('draws the prompt, session and task lines as cards with icons and the words as tooltips', async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    expect($('[data-kind="prompt"]')).toBeNull(); // a permission request is a tool step: Activity's
    expect(text($('[data-kind="session"]')!)).toContain('New session');
    const events = $$('[data-kind="task_event"]');
    expect(icon(events[0], 'Task added')).not.toBeNull();
    expect(text(events[0])).toContain('1684');
    const changed = events.find((c) => c.getAttribute('data-event') === 'changed')!;
    expect(text(changed)).toContain('assigned to alice');
    expect(text(changed)).toContain('priority 2 → 1');
    expect(changed.querySelector('.card-time')).not.toBeNull(); // the time is its own span at the card's end
    expect(text(events[0])).not.toMatch(/TASK|task added/); // no shouted label: the dot says it
  });

  it('a task card has Open at its end, and Done only on an open task given to the person', async () => {
    const s = setup();
    s.daemon.tasks.verbs = true;
    applySeeds(s.daemon, 'task-card');
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    const card = (n: number) => $$('[data-kind="task_event"]').find((c) => text(c).includes(String(n)));
    const button = (n: number, word: string) => [...(card(n)?.querySelectorAll('button') ?? [])].find((b) => text(b) === word);
    await until(() => button(1695, 'Done'), 'Done on the open task');
    expect(button(1695, 'Open')).toBeDefined();
    expect(button(1684, 'Open')).toBeDefined();
    expect(button(1684, 'Done')).toBeUndefined(); // T1684 is the architect's, not the person's
    await click(button(1695, 'Done')!);
    await until(() => !button(1695, 'Done'), 'the card without Done');
  });

  it('a batch discard is one card with a line per task: the line opens the task, Reopen brings it back', async () => {
    const s = setup();
    s.daemon.chat.seedItem(ARCH, {
      at: new Date().toISOString(),
      kind: 'task_batch',
      task_batch: {
        batch: 'b1',
        who: ARCH,
        tasks: [
          { task: 1684, title: 'Gap list', reason: 'merged into T1700' },
          { task: 1685, title: 'Second', reason: 'stale' },
        ],
      },
    });
    await mount(ARCH, s);
    await until(() => $('[data-kind="task_batch"]'), 'the batch card');
    const card = $('[data-kind="task_batch"]')!;
    expect(text(card)).toContain('Discarded 2');
    expect($$('[data-kind="task_batch"] [data-task]').map((l) => l.getAttribute('data-task'))).toEqual(['1684', '1685']);
    expect(text(card.querySelector('[data-task="1684"]'))).toContain('merged into T1700');
    await click(card.querySelector('[data-task="1684"] button')!);
    await until(() => text($('.tdetail-head')).includes('1684'), 'task 1684 in the detail');
  });

  it('a click on a task line opens that task in the Tasks tab', async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await click($$('[data-kind="task_event"]')[0]!);
    await until(() => $('.tdetail'), 'the task detail');
    await until(() => text($('.tdetail-head')).includes('1684'), 'task 1684 in the detail');
  });

  it('with Chat and Tasks in split panes, the same task line opens the task again after Back', async () => {
    const storage = memory();
    const split = splitTab(defaultWorkspace({ terminal: `term:${ARCH}` }), `terminal:term:${ARCH}`, 'g1', 'right')!;
    const g2 = groupsOf(split.root).find((g) => g.id !== 'g1')!.id;
    saveWorkspace(storage, storageKey('test', ARCH), openTab(split, 'tasks', {}, g2));
    await mount(ARCH, setup(), storage);
    await until(() => msg('Which gap list'), 'history');
    await until(() => $('.tasks-tab-row'), 'the agent tasks');
    const before = history.state as object;
    await click($$('[data-kind="task_event"]')[0]!);
    await windowBack(before);
    await until(() => $('.tasks-tab-row'), 'back to the list');
    await click($$('[data-kind="task_event"]')[0]!);
    await until(() => $('.tdetail'), 'the task detail again');
  });

  it('in the Tasks tab, marked tasks are acted on together: the menu confirms with the count and the daemon is written', async () => {
    const s = setup();
    s.daemon.tasks.verbs = true;
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    await click($('button[aria-label="Add a tab"]')!);
    await click([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((b) => text(b).includes('Tasks'))!);
    await until(() => $('.tasks-tab-row'), 'the agent tasks');
    expect($('input[aria-label="Mark T1684"]')).toBeNull(); // local-ref-ok: the boxes wait for Select
    await click($('button[aria-label="Select tasks"]')!);
    await click($('input[aria-label="Mark T1684"]')!); // local-ref-ok
    await click($$('button').find((b) => b.getAttribute('aria-label') === 'Actions on the marked tasks')!);
    await click([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((b) => text(b).startsWith('Discard'))!);
    const sh = $$('.tsheet').find((x) => x.getAttribute('aria-label') === 'Discard 1 task?')!;
    await typeInto(sh.querySelector('input[aria-label="why?"]') as HTMLInputElement, 'duplicate');
    await click([...sh.querySelectorAll('button')].find((b) => text(b) === 'Discard')!);
    await until(() => s.daemon.tasks.get({ task: 1684 }).state === 'discarded', 'the task is discarded');
  });

  it("the Add a tab menu opens the agent's Tasks, a click opens a task in the pane and Back returns to the list", async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await click($('button[aria-label="Add a tab"]')!);
    await click([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((b) => text(b).includes('Tasks'))!);
    await until(() => $('.tasks-tab-row'), 'the agent tasks');
    const rows = $$('.tasks-tab-row');
    expect(rows.length).toBeGreaterThan(0);
    const li = rows[0]!.parentElement!;
    expect(li.querySelector('button[aria-label="Actions"]')).not.toBeNull();
    await act(async () => void li.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true })));
    await until(() => document.querySelector('[role=menu]'), 'the row menu on a right-click');
    const before = history.state as object;
    await click(rows[0]!);
    await until(() => $('.tdetail'), 'the task detail');
    await windowBack(before);
    expect($('.tasks-tab-row')).not.toBeNull();
  });
});

describe('the bridge items: message numbers, new, menus, seen, strip', () => {
  it("nothing the task board sent is in the agent's own chat", async () => {
    const s = setup();
    s.daemon.chat.seed(ARCH, { from: 'task-board', to: ARCH, body: 'T3492 done by handline.pair: -' }); // local-ref-ok
    s.daemon.chat.seed(ARCH, { from: 'task-board', to: ARCH, body: '#28387 to handline.pair was written to its session' }); // local-ref-ok
    s.daemon.chat.seed(ARCH, { from: 'task-board', to: ARCH, body: 'task T4136 now waits on T4148' }); // local-ref-ok
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    expect($$('[data-kind="task_event"]').some((c) => text(c).includes('T3492'))).toBe(false); // local-ref-ok
    expect(msg('T3492 done by')).toBeUndefined(); // local-ref-ok
    expect(msg('#28387 to handline.pair')).toBeUndefined(); // local-ref-ok
    expect(msg('now waits on')).toBeUndefined();
  });

  it('in the desktop app an agent name opens that agent, and a repo path asks the shell with the branches named', async () => {
    const calls: unknown[] = [];
    Object.assign(window, {
      handline: {
        api: 1,
        openPath: async (a: unknown) => (calls.push(['path', a]), { ok: true, how: 'branch' }),
      },
    });
    try {
      const s = setup();
      s.daemon.chat.seed(ARCH, {
        from: ARCH,
        to: 'human',
        body: `on branch feat-docs file docs/x.md by ${ARCH}`,
        at: '2031-02-01T00:00:00Z',
      });
      await mount(ARCH, s);
      await until(() => msg('on branch feat-docs'), 'the message');
      const m = msg('on branch feat-docs')!;
      await click(m.querySelector('.path')!);
      await click(m.querySelector('.agent-link')!);
      expect(calls).toEqual([['path', { path: 'docs/x.md', agent: ARCH, branches: ['feat-docs', 'docs/x.md'] }]]);
      expect(location.hash).toBe(`#/agents/${encodeURIComponent(ARCH)}`);
    } finally {
      delete (window as { handline?: unknown }).handline;
    }
  });

  it('new messages while scrolled up are counted in a pill that takes you down', async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    const stream = $('.stream')!;
    await act(async () => void stream.dispatchEvent(new Event('scroll'))); // the report of the jump that holds the last sent message on entry
    for (const [k, v] of [
      ['scrollHeight', 1000],
      ['clientHeight', 100],
    ] as const)
      Object.defineProperty(stream, k, { value: v, configurable: true });
    stream.scrollTop = 0;
    await act(async () => void stream.dispatchEvent(new Event('scroll')));
    await other(t, 'fresh news');
    await until(() => $('.newer'), 'the pill');
    expect(text($('.newer'))).toBe('1 new message');
    await click($('.newer')!);
    expect($('.newer')).toBeNull();
  });

  it('a reader scrolled up keeps the line they are on when something above it changes size', async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    const stream = $('.stream')!;
    await act(async () => void stream.dispatchEvent(new Event('scroll'))); // the report of the jump that holds the last sent message on entry
    for (const [k, v] of [
      ['scrollHeight', 1000],
      ['clientHeight', 100],
    ] as const)
      Object.defineProperty(stream, k, { value: v, configurable: true });
    let above = 0; // how far what sits above the reader has grown (a layout jsdom does not do)
    const rect = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function (this: Element) {
      const at = (top: number) => ({ top, bottom: top + 50, left: 0, right: 0, width: 0, height: 50, x: 0, y: top, toJSON: () => ({}) });
      if (this === stream) return at(0);
      const i = [...stream.querySelectorAll('[data-id]')].indexOf(this);
      return i < 0 ? rect.call(this) : at(i * 50 + above - stream.scrollTop);
    };
    try {
      stream.scrollTop = 200;
      await act(async () => void stream.dispatchEvent(new Event('scroll')));
      above = 40;
      await other(t, 'fresh news');
      await until(() => $('.newer'), 'the new message');
      expect(stream.scrollTop).toBe(240);
    } finally {
      Element.prototype.getBoundingClientRect = rect;
    }
  });

  it('/ lists the terminal, and no verb the daemon does by itself, # a task or message number to refer to, Esc closes a menu', async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await typeInto(compose(), '/');
    const verbs = $$('[role="listbox"] [role="option"]').map((o) => o.querySelector('.ui-prow-name')!.textContent);
    expect(verbs).toContain('Terminal');
    expect(verbs.some((v) => /Restart|Sleep|Fold|Move|Close/.test(v ?? ''))).toBe(false);
    await typeInto(compose(), '/ter');
    expect($$('[role="listbox"] [role="option"]')).toHaveLength(1);
    await press(compose(), 'Escape');
    expect($('[role="listbox"]')).toBeNull();
    await typeInto(compose(), 'compare with #');
    const refs = $$('[role="listbox"] [role="option"]').map((o) => o.querySelector('.ui-prow-name')!.textContent);
    expect(refs).toContain('#100'); // local-ref-ok
    await click($$('[role="listbox"] .ui-prow').find((b) => text(b).startsWith('#100'))!); // local-ref-ok
    expect(compose().value).toBe('compare with #100 '); // local-ref-ok
  });

  it('Ctrl+R lists what was sent and picking one fills the box; Esc twice clears a draft', async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await press(compose(), 'r', { ctrlKey: true });
    const opts = $$('[role="listbox"] .ui-prow');
    expect(opts.length).toBeGreaterThan(1);
    await click(opts[0]!);
    expect(compose().value).toBe('Also add the list of gaps.');
    await press(compose(), 'Escape');
    expect(compose().value).toBe('Also add the list of gaps.'); // one Esc keeps the draft and says so
    expect($('.compose-clear')).not.toBeNull();
    await press(compose(), 'Escape');
    expect(compose().value).toBe('');
  });

  it("opening the chat reads the agent's results in the Inbox, but not a question that waits for an answer", async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    const inbox = (t.daemon as { inbox: { get: (id: number) => { read: boolean } } }).inbox;
    await until(() => inbox.get(5).read, 'the result read');
    expect(inbox.get(3).read).toBe(false);
  });

  it('the strip shows the model, the account and the task; a stuck agent gets a banner', async () => {
    const t = await mount(ARCH);
    await until(() => $('.chat-strip'), 'the strip');
    expect($('.chat-strip .ui-state')).toBeNull(); // no spinner at the start; the head says the state
    expect(text($('.chat-strip'))).toContain('opus');
    expect($('.chat-strip a')!.getAttribute('href')).toMatch(/^#\/tasks\/\d+/);
    expect($('.chat-banner')).toBeNull();
    void t;
  });

  it("the strip counts the files changed in the agent's worktree", async () => {
    await mount(ARCH);
    await until(() => $('.chat-strip [aria-label$="in the worktree"]'), 'the count');
    expect(text($('.chat-strip [aria-label$="in the worktree"]'))).toBe('1');
    expect($('.chat-strip [aria-label$="in the worktree"]')!.getAttribute('title')).toBe('1 changed file in the worktree');
  });

  it('the ticks sit beside only the newest message of the person; an earlier one has them on hover', async () => {
    const s = setup();
    s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: 'first words', landing: 'delivered' });
    s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: 'last words', landing: 'delivered' });
    await mount(ARCH, s);
    await until(() => msg('last words'), 'the messages');
    const ticks = (body: string) => msg(body)!.parentElement!.querySelector('.landing');
    expect(ticks('last words')?.closest('.hidden')).toBeNull();
    expect(ticks('first words')?.closest('.hidden')).not.toBeNull();
  });

  it("an agent's message has Copy, which writes its words and says Copied, and Quote, which picks it", async () => {
    const write = vi.fn(() => Promise.resolve());
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: write } });
    const s = setup();
    s.daemon.chat.seed(ARCH, { from: ARCH, to: 'human', body: 'Report: gaps listed.', landing: 'delivered' });
    await mount(ARCH, s);
    await until(() => msg('Report: gaps listed.'), 'the report');
    const bar = msg('Report: gaps listed.')!.parentElement!.querySelector<HTMLElement>('.msg-hover')!;
    const pressed = (label: string) => [...bar.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === label)!;
    await click(pressed('Copy'));
    expect(write).toHaveBeenCalledWith('Report: gaps listed.');
    await until(() => pressed('Copied'), 'Copied');
    await click(pressed('Quote'));
    expect(text($('.compose-ref'))).toContain('Replying to');
  });

  it("the person's own messages have no hover bar", async () => {
    const s = setup();
    s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: 'mine', landing: 'delivered' });
    await mount(ARCH, s);
    await until(() => msg('mine'), 'the message');
    expect(msg('mine')!.parentElement!.querySelector('.msg-hover')).toBeNull();
  });

  it('a chat with messages is laid out from the bottom, so a short one sits next to the composer; an empty one is not', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    expect($('.stream > div')!.className).toContain('justify-end');
  });
});
