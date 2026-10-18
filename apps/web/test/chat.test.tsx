// U8: the Chat tab on app-data, against the fake daemon's chat with its latency and refusal knobs.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Store, defaultWorkspace, groupsOf, openTab, saveWorkspace, splitTab, storageKey, type KV } from '@handline/app-core';
import { RpcError } from '@handline/client';
import { second, setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const REFUSAL = 'the agent has no open session';
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
const tabEl = async (id: string) => {
  await until(() => $(`[data-tab="${id}"]`), `the ${id} tab`);
  return $(`[data-tab="${id}"]`)!;
};
const msg = (body: string) => $$('.msg').find((m) => text(m).includes(body));
const landing = (body: string) => msg(body)?.getAttribute('data-landing');
const link = (label: string, within: Element) =>
  [...within.querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === label)!;
const icon = (m: Element | undefined, label: string) => m?.querySelector(`[aria-label="${label}"]`) ?? null;
const idOfBody = (t: { daemon: unknown }, body: string) =>
  (t.daemon as { chat: { messages: { id: number; body: string }[] } }).chat.messages.find((m) => m.body === body)!.id;
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
    expect($('.chat-head')).toBeNull();
  });

  it('draws every message state and transcript kind from the daemon', async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    expect(landing('Draft the web chat tab plan.')).toBe('answered');
    expect(landing('Use the contract fixtures.')).toBe('delivered_unanswered');
    expect(landing('Hold that, scope changed.')).toBe('withdrawn');
    expect(landing('Also add the list of gaps.')).toBe('typed');
    expect(link('Withdraw', msg('Also add the list of gaps.')!)).toBeUndefined(); // the chat has no Withdraw button
    expect($$('[data-kind="reply"]')).toHaveLength(1); // the agent's message is not drawn a second time as a reply
    expect($$('[data-kind="tool"]')).toHaveLength(0); // tool lines are on the Activity tab
    expect(icon($$('[data-kind="task_event"]')[1], 'Task done')).not.toBeNull();
    expect(msg('Review is done')).toBeUndefined(); // agent to agent: the Activity tab
    expect($$('.chat-head [role="radio"]')).toHaveLength(0);
  });

  it('the Activity tab lists a tool line cut to nothing with how many lines were let go', async () => {
    const t = setup();
    t.daemon.chat.seedItem(ARCH, { kind: 'tool', tool: 'Bash', text: 'ls', output: '', dropped_lines: 5 });
    await mount(ARCH, t);
    await click(await tabEl('activity'));
    const bash = () => $$('.activity [data-kind="tools"]').find((r) => text(r).includes('Bash ls'));
    await until(bash, 'the tool row');
    await click(bash()!.querySelector('button')!);
    expect(text(bash())).toContain('5 more lines let go');
  });

  it('the Activity tab shows what a tool line printed on request', async () => {
    const t = setup();
    t.daemon.chat.seedItem(ARCH, { kind: 'tool', tool: 'Bash', text: 'ls', output: 'a\nb', dropped_lines: 3 });
    await mount(ARCH, t);
    await click(await tabEl('activity'));
    const bash = () => $$('.activity [data-kind="tools"]').find((r) => text(r).includes('Bash ls'));
    await until(bash, 'the tool row');
    const row = bash()!;
    expect(row.querySelector('.tool-output')).toBeNull();
    await click(row.querySelector('button')!);
    expect(row.querySelector('.tool-output')?.textContent).toBe('a\nb');
    expect(text(row)).toContain('3 more lines let go');
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
    expect(text(line)).toContain('Started again from a handoff');
    expect($('.handoff-full')).toBeNull();
    await click([...line.querySelectorAll('button')].find((b) => /Handoff/.test(text(b)))!);
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
    await until(() => $('.working')?.getAttribute('title')?.includes('Reading the code'), 'the working line');
    t.daemon.chat.tick();
    await until(() => landing('ship it') === 'answered', 'answered');
    expect($('.working')).toBeNull();
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
    await until(() => landing('from elsewhere') === 'answered', 'answered');
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
    s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: 'a control test, see #5' }); // local-ref-ok
    await mount(ARCH, s);
    await until(() => msg('a control test')?.querySelector('.ref'), 'a control inside a message');
    await click(msg('a control test')!.querySelector('.ref')!);
    expect($('.compose-ref')).toBeNull();
  });

  it('draws the prompt, session and task lines as cards with icons and the words as tooltips', async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    const prompt = $('[data-kind="prompt"]')!;
    expect(icon(prompt, 'Asked permission: Bash')).not.toBeNull();
    expect(text(prompt)).toContain('git status');
    expect(icon(prompt, 'Answered')).not.toBeNull();
    expect(text($('[data-kind="session"]')!)).toContain('New session');
    const events = $$('[data-kind="task_event"]');
    expect(icon(events[0], 'Task added')).not.toBeNull();
    expect(text(events[0])).toContain('1684');
    expect(events[0]!.tagName).toBe('BUTTON');
    const changed = events.find((c) => c.getAttribute('data-event') === 'changed')!;
    expect(text(changed)).toContain('assigned to alice');
    expect(text(changed.querySelector('s'))).toBe('2'); // the old priority struck through, the new one after it
    expect(text(changed)).toContain('priority 2 1');
    expect(changed.className).toContain('w-fit');
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
    await click($$('[data-kind="task_event"]')[0]!);
    await until(() => $('.tdetail'), 'the task detail');
    await click($('.tdetail .back')!);
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
    await click(rows[0]!);
    await until(() => $('.tdetail'), 'the task detail');
    await click($('.tdetail .back')!);
    expect($('.tasks-tab-row')).not.toBeNull();
  });
});

describe('the bridge items: message numbers, new, menus, seen, strip, HR', () => {
  const chatOf = (t: { daemon: unknown }) =>
    (t.daemon as { chat: { messages: { id: number; body: string; landing: string }[]; seenUpTo: Map<string, number> } }).chat;
  const hrOf = (t: { daemon: unknown }) => (t.daemon as { script: { hr: { raise: (p: object) => unknown } } }).script.hr;
  const labelled = (within: Element | null | undefined, label: string) =>
    [...(within ?? box).querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === label);

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

  it('a message number in a message is a link that scrolls to that message, also one on an older page', async () => {
    const s = setup();
    const first = Number(
      s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: 'see #100 for the plan', at: '2030-01-01T00:00:00Z' }), // local-ref-ok
    );
    for (let i = 0; i < 60; i++)
      s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: `filler ${i}`, at: new Date(Date.UTC(2031, 0, 1, 0, i)).toISOString() });
    expect(first).toBeGreaterThan(100);
    await mount(ARCH, s);
    await until(() => msg('filler 59'), 'the newest page');
    expect(msg('Draft the web chat tab plan.')).toBeUndefined(); // the plan message is on an older page
    await until(() => msg('see #100') || $('.older'), 'the page'); // local-ref-ok
    await pageBackTo(() => $('.ref'), 'the page with the reference');
    await click($('.ref')!);
    await until(() => msg('Draft the web chat tab plan.')?.hasAttribute('data-flash'), 'the message flashes');
  });

  it('a link to an old message opens the stretch around it, not every page up to it; Back to latest returns to the tail', async () => {
    const s = setup();
    for (let i = 0; i < 120; i++)
      s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: `filler ${i}`, at: new Date(Date.UTC(2031, 0, 1, 0, i)).toISOString() });
    s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: 'see #100 for the plan', at: '2032-01-01T00:00:00Z' }); // local-ref-ok
    await mount(ARCH, s);
    await until(() => $('.ref'), 'the reference on the newest page');
    expect(msg('Draft the web chat tab plan.')).toBeUndefined();
    await click($('.ref')!);
    await until(() => $('.latest'), 'Back to latest');
    await until(() => msg('Draft the web chat tab plan.')?.hasAttribute('data-flash'), 'the message flashes');
    expect(msg('see #100')).toBeUndefined(); // the tail is not drawn while the window is open // local-ref-ok
    await click($('.latest')!);
    await until(() => msg('see #100'), 'the tail again'); // local-ref-ok
    expect($('.latest')).toBeNull();
  });

  it('the working indicator follows the agent being busy, also on a message from another agent', async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    expect($('.working')).toBeNull();
    t.daemon.chat.seed(ARCH, { from: 'handline.auditor', to: ARCH, body: 'rebase the pane branch' });
    t.daemon.chat.setWorking(ARCH, true, 'Finding large disk users');
    await until(() => $('.working')?.getAttribute('title') === 'Finding large disk users', 'the indicator for a busy agent');
    t.daemon.chat.setWorking(ARCH, false);
    await until(() => $('.working') === null, 'the indicator gone');
  });

  it('new messages while scrolled up are counted in a pill that takes you down', async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    const stream = $('.stream')!;
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

  it("the agent's messages leave FOR YOU once they are drawn: chat.seen goes up to the newest", async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await until(() => (chatOf(t).seenUpTo.get(ARCH) ?? 0) === idOfBody(t, 'Which gap list should come first?'), 'chat.seen');
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

  it('the strip shows the state as a glyph, the model, the account and the task; a stuck agent gets a banner', async () => {
    const t = await mount(ARCH);
    await until(() => $('.chat-strip'), 'the strip');
    expect($('.chat-strip .ui-state')!.getAttribute('data-state')).toBeTruthy();
    expect(text($('.chat-strip'))).toContain('opus');
    expect($('.chat-strip a')!.getAttribute('href')).toMatch(/^#\/tasks\/\d+/);
    expect($('.chat-banner')).toBeNull();
    void t;
  });

  it('an open HR proposal about the agent is a card in the chat with approve and reject as icons', async () => {
    const s = setup();
    hrOf(s).raise({ id: 41, title: 'link handline.architect -> handline.auditor', diff: 'link', agent: ARCH });
    await mount(ARCH, s);
    await until(() => $('.card-hr'), 'the HR card');
    expect(text($('.card-hr'))).toContain('link handline.architect');
    expect(labelled($('.card-hr'), 'Approve')).toBeDefined();
    expect(labelled($('.card-hr'), 'Reject')).toBeDefined();
    await click(labelled($('.card-hr'), 'Reject')!);
    await typeInto($('.ui-dialog input') as HTMLInputElement, 'not now');
    await click($('.ui-dialog .ui-btn.solid')!);
    await until(() => $('.card-hr')?.getAttribute('data-state') === 'rejected', 'the same card showing the outcome');
    expect(text($('.card-hr'))).toContain('Rejected');
    expect(labelled($('.card-hr'), 'Reject')!.getAttribute('aria-pressed')).toBe('true');
    expect((labelled($('.card-hr'), 'Approve') as HTMLButtonElement).disabled).toBe(true);
  });

  it('an approved HR proposal stays as the same card saying Applied, with what changed, the Approve button pressed and a banner', async () => {
    const s = setup();
    hrOf(s).raise({ id: 42, title: 'link handline.architect -> handline.auditor', diff: 'link architect to auditor', agent: ARCH });
    await mount(ARCH, s);
    await until(() => $('.card-hr'), 'the HR card');
    await click(labelled($('.card-hr'), 'Approve')!);
    await until(() => $('.card-hr')?.getAttribute('data-state') === 'approved', 'the card showing Applied');
    expect(text($('.card-hr'))).toContain('Applied');
    expect(text($('.card-hr'))).toContain('link architect to auditor');
    await until(() => $('.card-hr .banner'), 'the banner');
    expect(text($('.card-hr .banner'))).toBe('Applied');
    expect(labelled($('.card-hr'), 'Approve')!.getAttribute('aria-pressed')).toBe('true');
    expect((labelled($('.card-hr'), 'Reject') as HTMLButtonElement).disabled).toBe(true);
    expect($$('.msg').some((m) => /applied/i.test(text(m)))).toBe(false); // the app posts nothing in the chat
  });
});

describe('loading and writes with latency and refusals', () => {
  it('draws the conversation skeleton after the delay and no spinner, then the conversation', async () => {
    const s = setup();
    s.daemon.knobs.latency(400);
    await mount(ARCH, s);
    await until(() => $('.chat .skeleton'), 'the chat skeleton');
    expect($('.chat .skeleton')!.getAttribute('aria-busy')).toBe('true');
    expect($('.spinner, [role=progressbar]')).toBeNull();
    expect($$('.chat .sk').length).toBeGreaterThan(3);
    await until(() => msg('Which gap list'), 'the conversation', 600);
    await until(() => !$('.chat .skeleton'), 'the skeleton gone');
  });

  it('shows a sent message at once, marked pending, and clears the box before the daemon answers', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    s.daemon.knobs.latency(500);
    await typeInto(compose(), 'right now');
    await press(compose(), 'Enter');
    await until(() => msg('right now'), 'the message');
    expect(sent(s, 'right now')).toBe(false); // the daemon has not answered yet
    expect(landing('right now')).toBe('queued');
    expect(msg('right now')!.getAttribute('data-pending')).toBe('true');
    expect(icon(msg('right now')?.parentElement ?? undefined, 'Sending')).not.toBeNull();
    expect(compose().value).toBe('');
    await until(() => sent(s, 'right now'), 'the daemon got it', 600);
    await until(() => $$('.msg').filter((m) => text(m).includes('right now')).length === 1, 'the real message replaced the temporary one');
    expect(msg('right now')!.getAttribute('data-pending')).toBeNull();
  });

  it('keeps a refused message as a failed row with its words and the reason; Retry sends it again', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    s.daemon.knobs.refuse('chat.send', 'state', REFUSAL, undefined, 1);
    await typeInto(compose(), 'will not land');
    await press(compose(), 'Enter');
    await until(() => $('.msg.failed'), 'the failed row');
    expect(text($('.msg.failed'))).toContain('will not land');
    expect(text($('.msg.failed'))).toContain(REFUSAL);
    expect(compose().value).toBe('');
    await click(link('Retry', $('.msg.failed')!));
    await until(() => sent(s, 'will not land'), 'the retry reached the daemon');
    await until(() => !$('.msg.failed'), 'the failed row gone');
    expect($$('.msg').filter((m) => text(m).includes('will not land'))).toHaveLength(1);
  });

  it('Discard drops a failed row', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    s.daemon.knobs.refuse('chat.send', 'state', REFUSAL);
    await typeInto(compose(), 'drop me');
    await press(compose(), 'Enter');
    await until(() => $('.msg.failed'), 'the failed row');
    await click(link('Discard', $('.msg.failed')!));
    await until(() => !msg('drop me'), 'the row gone');
    expect(sent(s, 'drop me')).toBe(false);
  });
});

describe('errors', () => {
  it('says so when the conversation cannot be read, with Retry, and not a skeleton forever', async () => {
    const s = setup();
    s.daemon.knobs.refuse('chat.messages', 'state', REFUSAL);
    await mount(ARCH, s);
    await until(() => $('.chat .banner'), 'the error state');
    expect(text($('.chat .banner'))).toContain(REFUSAL);
    expect($('.chat .skeleton')).toBeNull();
    s.daemon.knobs.clear();
    await click($$('.chat .banner .ui-btn').find((b) => text(b) === 'Retry')!);
    await until(() => msg('Which gap list'), 'the conversation after Retry');
    expect($('.chat .banner')).toBeNull();
  });

  it('keeps what it had when a later read fails', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    s.daemon.knobs.refuse('chat.messages', 'state', REFUSAL);
    s.daemon.dropConnections(); // the reconnect reads every active query again, and the read is refused
    await until(() => $('.chat .banner'), 'the error after a failed read');
    expect(msg('Which gap list')).toBeDefined(); // the last data stays
  });

  it("shows the daemon's sentence and its hint when a read is refused", async () => {
    const s = setup();
    s.daemon.knobs.refuse('chat.transcript', 'state', REFUSAL, 'start the agent first');
    await mount(ARCH, s);
    await until(() => $('.chat .banner'), 'the error state');
    expect(text($('.chat .banner'))).toContain(`${REFUSAL} (start the agent first)`);
  });

  it('draws no block when the read failed only because the connection is gone', async () => {
    const s = setup();
    const { store } = await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    const call = store.client.call.bind(store.client);
    vi.spyOn(store.client, 'call').mockImplementation(((method: string, params: never) =>
      method.startsWith('chat.') ? Promise.reject(new RpcError('transport', 'closed')) : call(method as never, params)) as never);
    s.daemon.dropConnections(); // the reconnect reads again and the read cannot reach the daemon
    await wait(300);
    expect($('.chat .banner')).toBeNull();
    expect(msg('Which gap list')).toBeDefined();
  });
});
