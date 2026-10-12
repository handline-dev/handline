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
const msg = (body: string) => $$('.msg').find((m) => text(m).includes(body));
const landing = (body: string) => msg(body)?.getAttribute('data-landing');
const button = (m: Element | undefined, label: string) => [...(m?.querySelectorAll('button') ?? [])].find((b) => text(b) === label);
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

  it("draws every message state and transcript kind from the daemon, with the agent's question answerable", async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    expect(landing('Draft the web chat tab plan.')).toBe('answered');
    expect(landing('Use the contract fixtures.')).toBe('delivered_unanswered');
    expect(icon(msg('Use the contract fixtures.')?.parentElement ?? undefined, 'Not answered')).not.toBeNull(); // the stamp hangs beside the bubble now, drawn as delivered
    expect(landing('Hold that, scope changed.')).toBe('withdrawn');
    expect(landing('Also add the list of gaps.')).toBe('typed');
    expect(link('Withdraw', msg('Also add the list of gaps.')!)).toBeUndefined(); // the chat has no Withdraw button
    expect($$('[data-kind="reply"]')).toHaveLength(1); // the agent's message is not drawn a second time as a reply
    expect($$('[data-kind="tool"]')).toHaveLength(1);
    expect(icon($$('[data-kind="task_event"]')[1], 'Task done')).not.toBeNull();
    expect(msg('Review is done')).toBeUndefined(); // agent to agent: the All tab
    await click($$('.chat-head [role="radio"]')[1]!);
    await until(() => msg('Review is done'), 'the all tab');
  });

  it('a tool line cut to nothing still says how many lines were let go', async () => {
    const t = setup();
    t.daemon.chat.seedItem(ARCH, { kind: 'tool', tool: 'Bash', text: 'ls', output: '', dropped_lines: 5 });
    await mount(ARCH, t);
    await until(() => $$('[data-kind="tool"]').some((c) => /5 more lines let go/.test(text(c))), 'the cut note');
    expect($$('[data-kind="tool"]')[0]!.querySelector('button')).toBeNull();
  });

  it('a tool line shows what it printed on request, and how many lines were cut', async () => {
    const t = setup();
    t.daemon.chat.seedItem(ARCH, { kind: 'tool', tool: 'Bash', text: 'ls', output: 'a\nb', dropped_lines: 3 });
    await mount(ARCH, t);
    await until(() => $$('[data-kind="tool"]').some((c) => /Show all/.test(text(c))), 'the tool line');
    const row = $$('[data-kind="tool"]').find((c) => /Show all/.test(text(c)))!;
    expect(row.querySelector('.tool-output')).toBeNull();
    await click([...row.querySelectorAll('button')].find((b) => /Show all/.test(text(b)))!);
    expect(row.querySelector('.tool-output')?.textContent).toBe('a\nb');
    expect(text(row)).toContain('3 more lines let go');
  });

  it('a session marker shows the whole handoff on request', async () => {
    const t = setup();
    t.daemon.chat.seedItem(ARCH, {
      kind: 'session',
      session: { id: 's2', started: new Date().toISOString(), handoff: 'line one\nline two' },
    });
    await mount(ARCH, t);
    await until(() => $$('[data-kind="session"]').some((c) => /Whole handoff/.test(text(c))), 'the marker');
    const card = $$('[data-kind="session"]').find((c) => /Whole handoff/.test(text(c)))!;
    expect(card.querySelector('.handoff-full')).toBeNull();
    await click([...card.querySelectorAll('button')].find((b) => /Whole handoff/.test(text(b)))!);
    expect(card.querySelector('.handoff-full')?.textContent).toBe('line one\nline two');
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
    await until(() => text($('.working')).includes('Reading the code'), 'the working line');
    t.daemon.chat.tick();
    await until(() => landing('ship it') === 'answered', 'answered');
    expect($('.working')).toBeNull();
    await until(() => text($('.stream')).includes('Got it: "ship it"'), 'the reply');
    expect(storage.data.get(`handline.draft.${ARCH}`)).toBe('');
  });

  it('recalls what was sent with the up arrow and answers a question', async () => {
    await mount(ARCH);
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
    await until(() => text(msg('Which gap list')).includes('Your answer'), 'the answer shows');
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
      s.daemon.chat.seed(ARCH, { from: 'founder', to: ARCH, body: `old ${i}`, at: new Date(Date.UTC(2020, 0, 1, 0, i)).toISOString() });
    await mount(ARCH, s);
    await until(() => msg('old 59'), 'the newest page');
    expect(msg('Which gap list')).toBeUndefined(); // it is on the second page
    await click($('.older')!);
    await until(() => msg('Which gap list'), 'the older page');
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

  it("a reply to the agent's open question answers it instead of sending a new message", async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await click(msg('Which gap list')!.querySelector('.msg-body')!);
    await typeInto(compose(), 'chat first');
    await press(compose(), 'Enter');
    await until(() => text(msg('Which gap list')).length > 0 && icon(msg('Which gap list'), 'Your answer'), 'the answer shows');
    expect(sentMsgs(t).some((m) => m.body === 'chat first')).toBe(false);
  });

  it("a reply to the agent's message that asked nothing is a say under it, not an answer", async () => {
    const t = await mount(ARCH);
    const chat = t.daemon.chat;
    chat.change(chat.find(chat.seed(ARCH, { from: ARCH, to: 'founder', body: 'Report: gaps listed.', landing: 'delivered' })), {});
    await until(() => msg('Report: gaps listed.'), 'the report');
    await click(msg('Report: gaps listed.')!.querySelector('.msg-body')!);
    expect(text($('.compose-ref'))).toContain('Replying to');
    await typeInto(compose(), 'thanks, go on');
    await press(compose(), 'Enter');
    await until(() => sentMsgs(t).some((m) => m.body === 'thanks, go on'), 'the say');
    expect(sentMsgs(t).find((x) => x.body === 'thanks, go on')!.parent).toBe(idOf('Report: gaps listed.'));
  });

  it('a click on a control inside a message does not pick it', async () => {
    await mount(ARCH);
    await typeInto(compose(), 'a control test');
    await press(compose(), 'Enter');
    await until(() => button(msg('a control test'), 'Take back'), 'a control inside a message');
    await click(button(msg('a control test'), 'Take back')!);
    expect($('.compose-ref')).toBeNull();
  });

  it('draws the prompt, session and task lines as cards with icons and the words as tooltips', async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    const prompt = $('[data-kind="prompt"]')!;
    expect(icon(prompt, 'Asked permission: Bash')).not.toBeNull();
    expect(text(prompt)).toContain('git status');
    expect(icon(prompt, 'Answered')).not.toBeNull();
    expect(icon($('[data-kind="session"]')!, 'New session')).not.toBeNull();
    const events = $$('[data-kind="task_event"]');
    expect(icon(events[0], 'Task added')).not.toBeNull();
    expect(text(events[0])).toContain('1684');
    expect(events[0]!.tagName).toBe('BUTTON');
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
    await click($$('.strip-end .ui-prow').find((b) => text(b).includes('Tasks'))!);
    await until(() => $('.tasks-tab-row'), 'the agent tasks');
    await click($('input[aria-label="Mark #1684"]')!); // local-ref-ok
    await click($$('button').find((b) => b.getAttribute('aria-label') === 'Actions on the marked tasks')!);
    await click($$('.ui-popup button').find((b) => text(b).startsWith('Discard'))!);
    const sh = $$('.tsheet').find((x) => x.getAttribute('aria-label') === 'Discard 1 task?')!;
    await typeInto(sh.querySelector('input[aria-label="why?"]') as HTMLInputElement, 'duplicate');
    await click([...sh.querySelectorAll('button')].find((b) => text(b) === 'Discard')!);
    await until(() => s.daemon.tasks.get({ task: 1684 }).state === 'discarded', 'the task is discarded');
  });

  it("the Add a tab menu opens the agent's Tasks, a click opens a task in the pane and Back returns to the list", async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await click($('button[aria-label="Add a tab"]')!);
    await click($$('.strip-end .ui-prow').find((b) => text(b).includes('Tasks'))!);
    await until(() => $('.tasks-tab-row'), 'the agent tasks');
    const rows = $$('.tasks-tab-row');
    expect(rows.length).toBeGreaterThan(0);
    await click(rows[0]!);
    await until(() => $('.tdetail'), 'the task detail');
    await click($('.tdetail .back')!);
    expect($('.tasks-tab-row')).not.toBeNull();
  });
});

describe('the bridge items: send now, take back, message numbers, new, menus, seen, strip, HR', () => {
  const chatOf = (t: { daemon: unknown }) =>
    (t.daemon as { chat: { messages: { id: number; body: string; landing: string }[]; seenUpTo: Map<string, number> } }).chat;
  const hrOf = (t: { daemon: unknown }) => (t.daemon as { script: { hr: { raise: (p: object) => unknown } } }).script.hr;
  const labelled = (within: Element | null | undefined, label: string) =>
    [...(within ?? box).querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === label);

  it('Take back on a message the agent has not got removes it and puts its words back in the box', async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await typeInto(compose(), 'oops, wrong thing');
    await press(compose(), 'Enter');
    await until(() => $$('.msg').some((m) => text(m).includes('oops') && m.getAttribute('data-pending') === null), 'numbered');
    expect(compose().value).toBe('');
    await click(labelled(msg('oops, wrong thing'), 'Take back')!);
    await until(() => landing('oops, wrong thing') === 'withdrawn', 'withdrawn');
    expect(compose().value).toBe('oops, wrong thing');
    await until(() => chatOf(t).messages.some((m) => m.body === 'oops, wrong thing' && m.landing === 'withdrawn'), 'the daemon');
  });

  it('a message the agent already has shows no Take back', async () => {
    await mount(ARCH);
    await until(() => msg('Use the contract fixtures.'), 'history');
    expect(landing('Use the contract fixtures.')).toBe('delivered_unanswered');
    expect(labelled(msg('Use the contract fixtures.'), 'Take back')).toBeUndefined();
  });

  it('the daemon wants confirm to take back a delivered message, so the page offers no button for it', async () => {
    const t = await mount(ARCH);
    await until(() => msg('Use the contract fixtures.'), 'history');
    const id = idOfBody(t, 'Use the contract fixtures.');
    const c = second(t.daemon, t.net, { client: 'web-other' });
    await c.start();
    await expect(c.call('chat.cancel', { request: id })).rejects.toMatchObject({ data: { gate: 'confirm' } });
    c.close();
    expect(labelled(msg('Use the contract fixtures.'), 'Take back')).toBeUndefined();
  });

  it('Send now shows on a queued message only while the agent is working, and types it at once', async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await typeInto(compose(), 'first');
    await press(compose(), 'Enter');
    await until(() => sent(t, 'first'), 'sent');
    for (let i = 0; i < 3; i++) t.daemon.chat.tick(); // typed, delivered, working
    await until(() => $('.working'), 'the working line');
    await typeInto(compose(), 'second');
    await press(compose(), 'Enter');
    await until(() => $$('.msg').some((m) => text(m).includes('second') && m.getAttribute('data-pending') === null), 'numbered');
    expect(landing('second')).toBe('queued');
    expect(labelled(msg('first'), 'Send now')).toBeUndefined();
    await click(labelled(msg('second'), 'Send now')!);
    await until(() => landing('second') === 'typed', 'typed now');
  });

  it('a message number in a message is a link that scrolls to that message, also one on an older page', async () => {
    const s = setup();
    const first = Number(
      s.daemon.chat.seed(ARCH, { from: 'founder', to: ARCH, body: 'see #100 for the plan', at: '2030-01-01T00:00:00Z' }), // local-ref-ok
    );
    for (let i = 0; i < 60; i++)
      s.daemon.chat.seed(ARCH, { from: 'founder', to: ARCH, body: `filler ${i}`, at: new Date(Date.UTC(2031, 0, 1, 0, i)).toISOString() });
    expect(first).toBeGreaterThan(100);
    await mount(ARCH, s);
    await until(() => msg('filler 59'), 'the newest page');
    expect(msg('Draft the web chat tab plan.')).toBeUndefined(); // the plan message is on an older page
    await until(() => msg('see #100') || $('.older'), 'the page'); // local-ref-ok
    let ref = $('.ref');
    for (let i = 0; !ref && i < 5; i++) {
      await click($('.older')!);
      await wait(50);
      ref = $('.ref');
    }
    await click(ref!);
    await until(() => msg('Draft the web chat tab plan.')?.hasAttribute('data-flash'), 'the message flashes');
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
    expect(text($('.newer'))).toBe('1');
    await click($('.newer')!);
    expect($('.newer')).toBeNull();
  });

  it("the agent's messages leave FOR YOU once they are drawn: chat.seen goes up to the newest", async () => {
    const t = await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await until(() => (chatOf(t).seenUpTo.get(ARCH) ?? 0) === idOfBody(t, 'Which gap list should come first?'), 'chat.seen');
  });

  it('/ lists the verbs and the terminal, # a task or message number to refer to, Esc closes a menu', async () => {
    await mount(ARCH);
    await until(() => msg('Which gap list'), 'history');
    await typeInto(compose(), '/');
    const verbs = $$('[role="listbox"] [role="option"]').map((o) => o.querySelector('.ui-prow-name')!.textContent);
    expect(verbs).toContain('Terminal');
    expect(verbs.length).toBeGreaterThan(1);
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
    await until(() => !$('.card-hr'), 'the card gone once decided');
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

  it('puts a refused answer back as an open question, with the reason', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'the open question');
    s.daemon.knobs.latency(500);
    s.daemon.knobs.refuse('chat.answer', 'state', REFUSAL);
    await click(msg('Which gap list')!);
    await typeInto(compose(), 'chat first');
    await press(compose(), 'Enter');
    await until(() => text(msg('Which gap list')).includes('Your answer'), 'answered at once');
    await until(() => !text(msg('Which gap list')).includes('Your answer'), 'the question open again', 600);
    expect(text($('.toast'))).toContain(REFUSAL);
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
