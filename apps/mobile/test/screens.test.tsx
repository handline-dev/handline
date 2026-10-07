import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { DataProvider } from '@handline/app-data';
import { DaemonProvider } from '@handline/app-react';
import { setup } from '@handline/fake-daemon/helpers';
import { Home } from '../src/screens/Home.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect'; // idle with a session
const sleep = (ms: number) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, ms = 4000) {
  for (let t = 0; t < ms; t += 10) {
    if (cond()) return;
    await sleep(10);
  }
  throw new Error(`timed out waiting for ${what}`);
}

let root: Root | null = null;
let box: HTMLElement;
const $ = (id: string) => box.querySelector<HTMLElement>(`[data-testid="${id}"]`);
const text = () => box.textContent ?? '';
/** The height a row is styled with (the DOM has no layout here, so this reads the style both rows are given from the tokens). */
const rowHeightOf = (el: Element | null) => (el ? getComputedStyle(el).height : undefined);
const press = (el: HTMLElement | null) => act(async () => void el?.click());

async function mount(prepare: (s: ReturnType<typeof setup>) => void = () => {}, daemon?: { bytes?: boolean }, client?: string) {
  const s = setup({ daemon, client });
  prepare(s);
  const store = new Store(s.client);
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(
      <DaemonProvider store={store}>
        <DataProvider daemonId="test">
          <Home store={store} onUnpair={() => {}} />
        </DataProvider>
      </DaemonProvider>,
    );
  });
  return { ...s, store };
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('the agent list', () => {
  it('shows its skeleton after 150 ms while the daemon is slow, then the rows', async () => {
    const m = await mount((s) => s.daemon.knobs.latency(600));
    await until(() => $('skeleton-agents'), 'the skeleton');
    const skeletonRowHeight = rowHeightOf($('skeleton-agents')!.firstElementChild); // the first skeleton row
    expect($(`agent-${ARCH}`)).toBeNull();
    await until(() => $(`agent-${ARCH}`), 'the rows', 5000);
    await sleep(400); // the skeleton stays at least 300 ms once shown
    expect($('skeleton-agents')).toBeNull();
    expect(rowHeightOf($(`agent-${ARCH}`))).toBe(skeletonRowHeight);
    expect(text()).toContain(ARCH);
    m.store.stop();
  });

  it('shows the daemon’s sentence when the read is refused, and Retry reads again', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('agents.list', 'state', 'the daemon is resting', 'try again later'));
    await until(() => $('error'), 'the error block');
    expect(text()).toContain('the daemon is resting (try again later)');
    expect($(`agent-${ARCH}`)).toBeNull();
    m.daemon.knobs.clear();
    await press([...box.querySelectorAll<HTMLElement>('[role="button"]')].find((b) => b.textContent === 'Retry') ?? null);
    await until(() => $(`agent-${ARCH}`), 'the rows after Retry');
    expect($('error')).toBeNull();
    m.store.stop();
  });
});

describe('the agent page', () => {
  it('shows its skeleton, then the controls', async () => {
    const m = await mount();
    await until(() => $(`agent-${ARCH}`), 'the rows');
    m.daemon.knobs.latency(500);
    await press($(`agent-${ARCH}`));
    await until(() => $('skeleton-agentDetail'), 'the detail skeleton');
    expect($('back')).not.toBeNull(); // the way out stays while it loads
    await until(() => $('tab-terminal') && $('skeleton-agentDetail') === null, 'the page', 5000);
    m.store.stop();
  });

  it('shows a pending mark at once for a decided write and drops it, with the reason, on a refusal', async () => {
    const m = await mount();
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('tab-terminal'), 'the page');
    await press($('tab-terminal'));
    await until(() => $('take'), 'Take control');
    const before = $('holder')?.textContent;
    m.daemon.knobs.latency(400);
    m.daemon.knobs.refuse('input.take', 'input_holder', 'someone else holds the input', 'ask them to release');
    await press($('take'));
    await until(() => $('holder')?.textContent?.includes('taking…'), 'the pending mark', 300); // well before the daemon answers (400 ms)
    await until(() => text().includes('someone else holds the input'), 'the reason', 3000);
    await until(() => !$('holder')?.textContent?.includes('taking…'), 'the mark to go');
    expect($('holder')?.textContent).toBe(before); // nothing was faked, so nothing to put back
    m.store.stop();
  });
});

describe('the permission card', () => {
  it('shows "answering…" and keeps its buttons off while the daemon decides, and gives them back with the reason on a refusal', async () => {
    const m = await mount();
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('tab-terminal'), 'the page');
    await press($('tab-terminal'));
    await until(() => $('take'), 'Take control');
    await press($('take'));
    await until(() => $('holder')?.textContent?.includes('YOU'), 'the hold');
    await act(async () => void (await m.store.client.sendInput(ARCH, 'push the branch')));
    await act(async () => void [1, 2, 3].forEach(() => m.daemon.tick())); // the scripted turn: thinking, a line, then the prompt
    await until(() => $('permission-card'), 'the card');
    m.daemon.knobs.latency(400);
    m.daemon.knobs.refuse('prompt.answer', 'state', 'that prompt is not open any more');
    await press($('answer-1'));
    await until(() => $('permission-card')?.textContent?.includes('answering…'), 'the pending mark', 300);
    expect($('answer-1')?.getAttribute('aria-disabled')).toBe('true');
    await until(() => text().includes('that prompt is not open any more'), 'the reason', 3000);
    await until(() => !$('permission-card')?.textContent?.includes('answering…'), 'the mark to go');
    expect($('answer-1')?.getAttribute('aria-disabled')).not.toBe('true');
    m.store.stop();
  }, 20000);
});

describe('typing in the terminal', () => {
  const REFUSAL = 'its screen could not be read';
  async function typing(m: Awaited<ReturnType<typeof mount>>) {
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('tab-terminal'), 'the page');
    await press($('tab-terminal'));
    await until(() => $('take'), 'Take control');
    await press($('take'));
    await until(() => $('holder')?.textContent?.includes('YOU'), 'the hold');
    m.daemon.knobs.refuse('session.input', 'screen_blocker', REFUSAL, 'use session.keys or prompt.answer');
    const composer = $('composer') as HTMLInputElement;
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    await act(async () => {
      set.call(composer, 'run the tests');
      composer.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await press([...box.querySelectorAll<HTMLElement>('[role="button"]')].find((b) => b.textContent === 'Send') ?? null);
    return composer;
  }

  it('types as bytes when the daemon serves them, so a refused session.input does not stop it', async () => {
    const m = await mount();
    const composer = await typing(m);
    await until(
      () => m.daemon.sessions.get(ARCH)!.received.some((r) => r.kind === 'text' && r.value === 'run the tests'),
      'the typed line',
    );
    expect($('composer-refused')).toBeNull();
    expect(composer.value).toBe('');
    m.store.stop();
  });

  it('shows the daemon’s refusal where it typed and keeps the text when only session.input is offered', async () => {
    const m = await mount(() => {}, { bytes: false });
    const composer = await typing(m);
    await until(() => $('composer-refused'), 'the refusal');
    expect($('composer-refused')!.textContent).toContain(REFUSAL);
    expect(composer.value).toBe('run the tests');
    m.store.stop();
  });
});

const tab = (name: string) => press($(`nav-${name}`));

const DECISION = 3; // the fake's seeded decision
const RESULT = 5; // its unread result
const item = (id: number) => $(`inbox-${id}`);
const option = (id: number, which: string) => $(`inbox-option-${id}-${which}`);

describe('the inbox tab', () => {
  it('shows its skeleton while slow, then the waiting items, and an answered decision leaves the list and is answered at the daemon', async () => {
    const m = await mount((s) => s.daemon.knobs.latency(600));
    await tab('inbox');
    await until(() => $('skeleton-inbox'), 'the skeleton');
    await until(() => item(DECISION), 'the decision', 5000);
    m.daemon.knobs.latency(0);
    expect($('unread-count')?.textContent).toMatch(/^[1-9]/);
    await press($(`inbox-open-${DECISION}`));
    await press(option(DECISION, 'yes'));
    await until(() => item(DECISION) === null, 'the answered question to leave');
    await until(() => m.daemon.inbox.get(DECISION).answered, 'the daemon');
    m.store.stop();
  });

  it('marks a result read from its card, and Mark all read then the last answer empty the list into the empty sentence', async () => {
    const m = await mount();
    await tab('inbox');
    await until(() => item(RESULT), 'the items');
    await press($(`inbox-open-${RESULT}`));
    await press($(`inbox-read-${RESULT}`));
    await until(() => item(RESULT) === null, 'the result to leave');
    await until(() => m.daemon.inbox.get(RESULT).read, 'the daemon');
    await press($('mark-all-read'));
    await until(() => $('unread-count')?.textContent === '0', 'the count to drop');
    await press($(`inbox-open-${DECISION}`));
    await press(option(DECISION, 'yes'));
    await until(() => $('empty'), 'the empty sentence');
    expect(text()).toContain('Nothing waits on you');
    m.store.stop();
  });

  it('puts a refused answer back with its options and says why', async () => {
    const m = await mount();
    await tab('inbox');
    await until(() => item(DECISION), 'the items');
    m.daemon.knobs.refuse('inbox.answer', 'state', 'Already answered by the founder.');
    await press($(`inbox-open-${DECISION}`));
    await press(option(DECISION, 'no'));
    await until(() => text().includes('Already answered by the founder.'), 'the reason', 3000);
    await until(() => item(DECISION) && option(DECISION, 'no'), 'the decision back with its options');
    expect(m.daemon.inbox.get(DECISION).answered).toBe(false);
    m.store.stop();
  });

  it('keeps a typed answer through a refusal, and clears it once the daemon accepts', async () => {
    const m = await mount();
    await tab('inbox');
    await until(() => item(DECISION), 'the items');
    m.daemon.knobs.refuse('inbox.answer', 'state', 'Already answered by the founder.');
    await press($(`inbox-open-${DECISION}`));
    await press($(`inbox-answer-${DECISION}`));
    const type = async (v: string) =>
      act(async () => {
        const f = $('inbox-text') as unknown as HTMLTextAreaElement;
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(f, v);
        f.dispatchEvent(new Event('input', { bubbles: true }));
      });
    await type('Relay it, one socket.');
    await press($('inbox-send'));
    await until(() => text().includes('Already answered by the founder.'), 'the reason', 3000);
    await until(() => ($('inbox-text') as unknown as HTMLTextAreaElement | null)?.value === 'Relay it, one socket.', 'the text kept');
    m.daemon.knobs.clear();
    await press($('inbox-send'));
    await until(() => item(DECISION) === null, 'the card to leave');
    await until(() => m.daemon.inbox.get(DECISION).answered, 'the daemon');
    m.store.stop();
  });

  it('puts the unread items back and says why when Mark all read is refused', async () => {
    const m = await mount();
    await tab('inbox');
    await until(() => item(RESULT), 'the items');
    const before = $('unread-count')!.textContent;
    m.daemon.knobs.latency(300);
    m.daemon.knobs.refuse('inbox.mark_read', 'state', 'the inbox is read-only right now');
    await press($('mark-all-read'));
    await until(() => $('unread-count')?.textContent === '0', 'the optimistic count', 250);
    await until(() => text().includes('the inbox is read-only right now'), 'the reason', 3000);
    await until(() => $('unread-count')?.textContent === before && item(RESULT), 'the items back');
    expect(m.daemon.inbox.get(RESULT).read).toBe(false);
    m.store.stop();
  });

  it('shows the daemon’s sentence when the read is refused', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('inbox.list', 'state', 'the inbox is resting'));
    await tab('inbox');
    await until(() => $('error'), 'the error block');
    expect(text()).toContain('the inbox is resting');
    m.store.stop();
  });
});

describe('the tasks tab', () => {
  it('lists the open tasks and opens one with its brief, and Back returns to the list', async () => {
    const m = await mount();
    await tab('tasks');
    await until(() => box.querySelector('[data-testid^="task-"]:not([data-testid="task-back"])'), 'the rows');
    const row = box.querySelector<HTMLElement>('[data-testid^="task-"]')!;
    const title = row.textContent!;
    await press(row);
    await until(() => $('task-detail'), 'the detail');
    expect(text()).toContain(title.split('#')[0]!.trim().slice(0, 10));
    await press($('task-back'));
    await until(() => box.querySelector('[data-testid^="task-"]') && !$('task-detail'), 'the list');
    m.store.stop();
  });

  it('shows its skeleton while slow', async () => {
    const m = await mount((s) => s.daemon.knobs.latency(600));
    await tab('tasks');
    await until(() => $('skeleton-tasks'), 'the skeleton');
    m.store.stop();
  });
});

describe('the styled terminal', () => {
  it('draws the prompt marker in its colour when the daemon serves styled, with the row text unchanged', async () => {
    const m = await mount(() => {}, { bytes: true });
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('tab-terminal'), 'the page');
    await press($('tab-terminal'));
    await until(() => $('take'), 'Take control');
    await press($('take'));
    await until(() => $('holder')?.textContent?.includes('YOU'), 'the hold');
    await act(async () => void (await m.store.client.sendInput(ARCH, 'push the branch')));
    await act(async () => void [1, 2, 3].forEach(() => m.daemon.tick()));
    await until(() => $('terminal-text')?.textContent?.includes('> push the branch'), 'the typed line');
    const run = [...$('terminal-text')!.querySelectorAll<HTMLElement>('*')].find((e) => e.textContent === '>');
    expect(run?.style.color).toBe('rgba(92, 191, 201, 1.00)'); // palette 6
    m.store.stop();
  }, 20000);

  it('falls back to plain text when the daemon does not serve styled', async () => {
    const m = await mount();
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('tab-terminal'), 'the page');
    await press($('tab-terminal'));
    await until(() => $('terminal-text')?.textContent?.includes('Claude Code'), 'the screen');
    expect($('terminal-text')!.querySelector('*')).toBeNull();
    m.store.stop();
  });
});

/** Types into a field the way a keyboard does (react-native-web draws TextInput as an input or a textarea). */
const fill = (id: string, v: string) =>
  act(async () => {
    const f = $(id) as unknown as HTMLInputElement | HTMLTextAreaElement;
    const proto = f.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(f, v);
    f.dispatchEvent(new Event('input', { bubbles: true }));
  });
const value = (id: string) => ($(id) as unknown as HTMLInputElement | null)?.value;
const MINE_DOING = 1684; // asked by me, running
const MINE_DRAFT = 1700; // asked by me, not started

async function openTask(id: number) {
  await tab('tasks');
  await until(() => $(`task-${id}`), 'the task row');
  await press($(`task-${id}`));
  await until(() => $('task-detail'), 'the detail');
}

const verbs = (s: ReturnType<typeof setup>) => void (s.daemon.tasks.verbs = true); // the daemon's plan D3

describe('the task verbs', () => {
  it('asks a new task: it is in the list at once, the daemon has it, and a refusal keeps the text and says why', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await tab('tasks');
    await until(() => $(`task-${MINE_DOING}`), 'the rows');
    await press($('new-task-open'));
    await press($('nt-send'));
    expect($('nt-error')?.textContent).toBe('Pick who it is for.');
    await press($(`nt-owner-${ARCH}`));
    await press($('nt-send'));
    expect($('nt-error')?.textContent).toBe('A task needs a title.');
    await fill('nt-title', 'Ship the phone tasks');
    m.daemon.knobs.refuse('task.new', 'state', 'the board is full');
    await press($('nt-send'));
    await until(() => text().includes('the board is full'), 'the reason', 3000);
    expect(value('nt-title')).toBe('Ship the phone tasks');
    m.daemon.knobs.clear();
    await press($('nt-send'));
    await until(() => $('new-task') === null, 'the form to close');
    await until(() => text().includes('Ship the phone tasks'), 'the row');
    await until(() => m.daemon.tasks.list({}).items.some((t) => t.title === 'Ship the phone tasks'), 'the daemon');
    m.store.stop();
  });

  it('done wants a reason, closes the task at once, and a refusal puts it back with the sentence', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DOING);
    await press($('verb-done'));
    await press($('sheet-submit'));
    expect($('sheet-error')?.textContent).toBe('A reason is required.');
    await fill('sheet-text', 'shipped');
    m.daemon.knobs.latency(300);
    m.daemon.knobs.refuse('task.done', 'state', 'that task is waiting on an audit');
    await press($('sheet-submit'));
    await until(() => text().includes('done ·') || text().includes('· done ·'), 'the optimistic state', 250);
    await until(() => text().includes('that task is waiting on an audit'), 'the reason', 3000);
    await until(() => text().includes('· doing ·') && $('sheet'), 'the task back, the sheet open');
    expect(value('sheet-text')).toBe('shipped');
    m.daemon.knobs.clear();
    await press($('sheet-submit'));
    await until(() => $('sheet') === null, 'the sheet to close');
    await until(() => m.daemon.tasks.get({ task: MINE_DOING }).state === 'done', 'the daemon');
    m.store.stop();
  }, 20000);

  it('reopens a done task of mine with a reason', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DOING);
    await press($('verb-done'));
    await fill('sheet-text', 'shipped');
    await press($('sheet-submit'));
    await until(() => $('verb-reopen'), 'Reopen');
    await press($('verb-reopen'));
    await fill('sheet-text', 'one more thing');
    await press($('sheet-submit'));
    await until(() => m.daemon.tasks.get({ task: MINE_DOING }).state !== 'done', 'the daemon');
    m.store.stop();
  }, 20000);

  it('withdraws a draft after its confirmation, and cancels a running task with an optional reason', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DRAFT);
    expect($('verb-cancel')?.textContent).toBe('Cancel');
    await press($('verb-withdraw'));
    expect(text()).toContain(`withdraw #${MINE_DRAFT}?`);
    await press($('sheet-cancel'));
    expect($('sheet')).toBeNull(); // nothing sent
    expect(m.daemon.tasks.get({ task: MINE_DRAFT }).state).toBe('draft');
    await press($('verb-withdraw'));
    await press($('sheet-submit'));
    await until(() => m.daemon.tasks.get({ task: MINE_DRAFT }).state !== 'draft', 'the daemon');
    await press($('task-back'));
    await openTask(MINE_DOING);
    expect($('verb-cancel')?.textContent).toBe('Stop');
    await press($('verb-cancel'));
    expect(text()).toContain(`stop #${MINE_DOING}? why (optional):`);
    await press($('sheet-submit')); // no reason is fine
    await until(() => m.daemon.tasks.get({ task: MINE_DOING }).state !== 'doing', 'the daemon');
    m.store.stop();
  }, 20000);

  it('disables a verb the daemon does not serve and says why', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openTask(MINE_DOING);
    expect($('verb-done')?.getAttribute('aria-disabled')).toBe('true');
    expect(text()).toContain('this daemon does not serve task.done');
    expect($('memo-send')?.getAttribute('aria-disabled')).toBe('true'); // the fake serves no memos
    expect(text()).toContain('this daemon does not serve task.memo.add');
    m.store.stop();
  });
});

describe('the chat tab', () => {
  const type = (id: string, value: string) =>
    act(async () => {
      const el = $(id) as HTMLTextAreaElement | HTMLInputElement;
      Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')!.set!.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  const msgOf = (body: string) =>
    [...box.querySelectorAll<HTMLElement>('[data-testid^="msg-"]')].find((e) => e.textContent?.includes(body));
  async function openChat() {
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('chat-input'), 'the chat');
  }

  it('shows its skeleton while the chat read is slow, then the rows', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    m.daemon.knobs.latency(500);
    await press($(`agent-${ARCH}`));
    await until(() => $('skeleton-chat'), 'the chat skeleton', 5000);
    expect(msgOf('Use the contract fixtures.')).toBeUndefined();
    await until(() => msgOf('Use the contract fixtures.'), 'the messages', 5000);
    await sleep(400); // the skeleton stays at least 300 ms once shown
    expect($('skeleton-chat')).toBeNull();
    m.store.stop();
  });

  it('shows the daemon’s sentence when the chat read is refused, and Retry reads again', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    m.daemon.knobs.refuse('chat.messages', 'state', 'the daemon is resting', 'try again later');
    await press($(`agent-${ARCH}`));
    await until(() => $('error'), 'the error block');
    expect($('error')!.textContent).toContain('the daemon is resting (try again later)');
    expect(msgOf('Use the contract fixtures.')).toBeUndefined();
    m.daemon.knobs.clear();
    await press([...box.querySelectorAll<HTMLElement>('[role="button"]')].find((b) => b.textContent === 'Retry') ?? null);
    await until(() => msgOf('Use the contract fixtures.'), 'the messages after Retry');
    expect($('error')).toBeNull();
    m.store.stop();
  });

  it('shows the conversation with how far each message got, and Mine hides what agents tell each other', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await until(() => msgOf('Use the contract fixtures.'), 'the messages');
    expect(msgOf('Use the contract fixtures.')!.textContent).toContain('Not answered');
    expect(msgOf('Draft the web chat tab plan.')!.textContent).toContain('✓✓ Answered');
    expect(text()).toContain('Here is the plan: three units, chat first.'); // the agent's reply
    expect(msgOf('Review is done, see the notes.')).toBeUndefined();
    await press($('scope-all'));
    await until(() => msgOf('Review is done, see the notes.'), 'the agents’ messages');
    expect(msgOf('Review is done, see the notes.')!.textContent).toContain('handline.auditor → handline.architect');
    m.store.stop();
  });

  it('sends at once, follows the message to delivered, and shows the working line', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await type('chat-input', 'run the tests');
    await press($('chat-send'));
    await until(() => msgOf('run the tests'), 'the sent message');
    expect(($('chat-input') as HTMLTextAreaElement).value).toBe('');
    await act(async () => void [1, 2, 3].forEach(() => m.daemon.tick())); // typed, delivered, thinking
    await until(() => $('working'), 'the working line');
    expect($('working')!.textContent).toContain('Reading the code');
    expect(msgOf('run the tests')!.textContent).toContain('Delivered');
    m.store.stop();
  });

  it('keeps a refused message as a failed row with the reason and Retry, and sends it again when the daemon allows', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('chat.send', 'state', 'the daemon is resting', undefined, 1), undefined, 'founder');
    await openChat();
    await type('chat-input', 'please retry me');
    await press($('chat-send'));
    await until(() => msgOf('please retry me')?.textContent?.includes('the daemon is resting'), 'the failed row');
    const row = msgOf('please retry me')!;
    await press(row.querySelector<HTMLElement>('[data-testid^="retry-"]'));
    await until(() => msgOf('please retry me')?.textContent?.includes('Sending'), 'the retried message');
    expect(msgOf('please retry me')!.textContent).not.toContain('the daemon is resting');
    m.store.stop();
  });

  it('replies to a picked message with its number, and takes a queued message back into the box', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await until(() => msgOf('Use the contract fixtures.'), 'the messages');
    await press(msgOf('Use the contract fixtures.')!);
    expect($('replying')!.textContent).toContain('Use the contract fixtures.');
    await type('chat-input', 'about that');
    await press($('chat-send'));
    await until(() => msgOf('about that'), 'the reply');
    expect(msgOf('about that')!.textContent).toContain('↩ #');
    expect($('replying')).toBeNull();
    const id = Number(msgOf('about that')!.getAttribute('data-testid')!.slice(4));
    await press($(`takeback-${id}`));
    await until(() => ($('chat-input') as HTMLTextAreaElement).value === 'about that', 'the words back in the box');
    expect(msgOf('about that')!.textContent).toContain('Withdrawn');
    m.store.stop();
  });

  it('answers a question from the agent', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await until(() => msgOf('Which gap list should come first?'), 'the question');
    const id = Number(msgOf('Which gap list should come first?')!.getAttribute('data-testid')!.slice(4));
    await type(`answer-input-${id}`, 'the web one');
    await press($(`answer-send-${id}`));
    await until(() => msgOf('Which gap list should come first?')?.textContent?.includes('You: the web one'), 'the answer');
    m.store.stop();
  });

  it('keeps an unsent draft per agent while the person is on another tab', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await type('chat-input', 'half a thought');
    await press($('tab-terminal'));
    await press($('tab-chat'));
    await until(() => $('chat-input'), 'the chat again');
    expect(($('chat-input') as HTMLTextAreaElement).value).toBe('half a thought');
    m.store.stop();
  });

  it('says so when the daemon does not serve chat', async () => {
    const m = await mount(() => {}, { chat: false }, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('chat-off'), 'the empty state');
    expect($('chat-input')).toBeNull();
    m.store.stop();
  });
});

describe('the agent verbs menu', () => {
  async function openMenu() {
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('verbs-open'), 'the page');
    await press($('verbs-open'));
  }

  it('lists what applies to the row, without Close and Clear stall', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openMenu();
    for (const id of ['restart', 'restart_fresh', 'park', 'fold', 'move']) expect($(`verb-${id}`)).not.toBeNull();
    expect($('verb-close')).toBeNull();
    expect($('verb-clear_stall')).toBeNull();
    expect($('verb-wake')).toBeNull(); // it has an open session
    m.store.stop();
  });

  it('asks before parking, sends nothing on Cancel, shows the gerund while the daemon decides, then the daemon has parked it', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openMenu();
    await press($('verb-park'));
    expect(text()).toContain(`Park ${ARCH}?`);
    expect(text()).toContain('Its session ends; it wakes on the next task.');
    await press($('sheet-cancel'));
    expect($('sheet')).toBeNull();
    expect(m.daemon.sessions.get(ARCH)!.state).not.toBe('parked');
    m.daemon.knobs.latency(400);
    await press($('verbs-open'));
    await press($('verb-park'));
    await press($('sheet-submit'));
    await until(() => $('pending-mark')?.textContent === 'parking…', 'the pending mark', 300);
    await until(() => m.daemon.sessions.get(ARCH)!.state === 'parked', 'the daemon', 3000);
    m.store.stop();
  });

  it('puts the reason on the page when the daemon refuses, and the mark goes', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('agent.fold', 'state', 'it is mid-turn'), undefined, 'founder');
    await openMenu();
    await press($('verb-fold'));
    await press($('sheet-submit'));
    await until(() => text().includes('it is mid-turn'), 'the reason');
    await until(() => !$('pending-mark'), 'the mark to go');
    m.store.stop();
  });

  it('moves the account after choosing one, and restarts fresh', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openMenu();
    await press($('verb-move'));
    expect($('sheet-submit')?.getAttribute('aria-disabled')).toBe('true'); // no account chosen yet
    await press($('account-beta'));
    await press($('sheet-submit'));
    await until(() => m.daemon.sessions.get(ARCH)!.fx.account === 'beta', 'the daemon');
    await press($('verbs-open'));
    await press($('verb-restart_fresh'));
    expect(text()).toContain(`Restart fresh ${ARCH}?`);
    await press($('sheet-submit'));
    await until(() => !$('sheet'), 'the sheet to close');
    m.store.stop();
  });
});
