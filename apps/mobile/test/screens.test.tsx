import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { ARCH, sleep, until, box, $, text, rowHeightOf, press, mount } from './harness.tsx';

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
});

describe('the permission card', () => {
  it('shows "answering…" and keeps its buttons off while the daemon decides, and gives them back with the reason on a refusal', async () => {
    const m = await mount();
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('tab-terminal'), 'the page');
    await press($('tab-terminal'));
    await act(async () => void (await m.store.client.sendInput(ARCH, 'push the branch')));
    await act(async () => void [1, 2, 3].forEach(() => m.daemon.tick())); // the scripted turn: thinking, a line, then the prompt
    await until(() => $('prompt-card'), 'the card');
    m.daemon.knobs.latency(400);
    m.daemon.knobs.refuse('prompt.answer', 'state', 'that prompt is not open any more');
    await press($('answer-1'));
    await until(() => $('prompt-card')?.textContent?.includes('answering…'), 'the pending mark', 300);
    expect($('answer-1')?.getAttribute('aria-disabled')).toBe('true');
    await until(() => text().includes('that prompt is not open any more'), 'the reason', 3000);
    await until(() => !$('prompt-card')?.textContent?.includes('answering…'), 'the mark to go');
    expect($('answer-1')?.getAttribute('aria-disabled')).not.toBe('true');
    m.store.stop();
  }, 20000);
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

  it('queues a draft and sends a queued task back to draft', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DRAFT);
    expect($('verb-draft')).toBeNull();
    await press($('verb-queue'));
    await until(() => m.daemon.tasks.get({ task: MINE_DRAFT }).state === 'queued', 'the daemon');
    await until(() => $('verb-draft'), 'Back to draft');
    await press($('verb-draft'));
    await until(() => m.daemon.tasks.get({ task: MINE_DRAFT }).state === 'draft', 'the daemon');
    m.store.stop();
  }, 20000);

  it('discard wants a reason, sends nothing when cancelled, and shows the refusal', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DRAFT);
    await press($('verb-discard'));
    expect(text()).toContain(`discard #${MINE_DRAFT}? why:`);
    await press($('sheet-cancel'));
    expect(m.daemon.tasks.get({ task: MINE_DRAFT }).state).toBe('draft');
    await press($('verb-discard'));
    await press($('sheet-submit'));
    expect($('sheet-error')?.textContent).toBe('A reason is required.');
    await fill('sheet-text', 'not wanted');
    m.daemon.knobs.refuse('task.discard', 'state', 'that task is waiting on an audit');
    await press($('sheet-submit'));
    await until(() => text().includes('that task is waiting on an audit'), 'the reason', 3000);
    expect(value('sheet-text')).toBe('not wanted');
    m.daemon.knobs.clear();
    await press($('sheet-submit'));
    await until(() => m.daemon.tasks.get({ task: MINE_DRAFT }).state === 'discarded', 'the daemon');
    m.store.stop();
  }, 20000);

  it('edits a task and sends only what changed', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DRAFT);
    await press($('verb-edit'));
    await fill('et-title', '');
    await press($('sheet-submit'));
    expect($('sheet-error')?.textContent).toBe('A task needs a title.');
    await fill('et-title', 'Pair the phone with the real daemon');
    await press($('et-priority-1'));
    const sent: Record<string, unknown>[] = [];
    const edit = m.daemon.tasks.edit.bind(m.daemon.tasks);
    m.daemon.tasks.edit = (p) => (sent.push(p), edit(p));
    await press($('sheet-submit'));
    await until(() => $('sheet') === null, 'the sheet to close');
    expect(sent.map(({ request_id, ...rest }) => rest)).toEqual([
      { task: MINE_DRAFT, title: 'Pair the phone with the real daemon', priority: 1 },
    ]);
    await until(() => m.daemon.tasks.get({ task: MINE_DRAFT }).title === 'Pair the phone with the real daemon', 'the daemon');
    expect(m.daemon.tasks.get({ task: MINE_DRAFT }).priority).toBe(1);
    expect(m.daemon.tasks.get({ task: MINE_DRAFT }).to).toBe('handline.term-build');
    m.store.stop();
  }, 20000);

  it('keeps the edit sheet and its text when the daemon refuses, and shows the reason', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DRAFT);
    await press($('verb-edit'));
    await fill('et-title', 'A better title');
    m.daemon.knobs.refuse('task.edit', 'state', 'that task is being merged');
    await press($('sheet-submit'));
    await until(() => text().includes('that task is being merged'), 'the reason', 3000);
    expect($('sheet')).not.toBeNull();
    expect(value('et-title')).toBe('A better title');
    expect(m.daemon.tasks.get({ task: MINE_DRAFT }).title).not.toBe('A better title');
    m.store.stop();
  }, 20000);

  it('puts Queue back and shows the reason when the daemon refuses it', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DRAFT);
    m.daemon.knobs.refuse('task.queue', 'state', 'the queue is full');
    await press($('verb-queue'));
    await until(() => text().includes('the queue is full'), 'the reason', 3000);
    expect($('verb-queue')).not.toBeNull();
    expect(m.daemon.tasks.get({ task: MINE_DRAFT }).state).toBe('draft');
    m.store.stop();
  }, 20000);

  it('filters the list: Mine, a state chip and Closed', async () => {
    await mount(verbs, undefined, 'founder');
    await tab('tasks');
    await until(() => $(`task-${MINE_DOING}`) && $('task-1690'), 'the rows');
    expect($('task-1650')).toBeNull(); // done is out of the way
    await press($('state-queued'));
    await until(() => $('task-1690') === null, 'queued hidden');
    expect($(`task-${MINE_DOING}`)).not.toBeNull();
    await press($('state-queued'));
    await until(() => $('task-1690'), 'queued back');
    await press($('scope-mine'));
    await until(() => $(`task-${MINE_DOING}`) === null, 'only tasks owned by me');
    await press($('scope-closed'));
    await until(() => $('task-1650'), 'the closed task');
    expect($(`task-${MINE_DOING}`)).toBeNull();
    await press($('scope-open'));
    await until(() => $(`task-${MINE_DOING}`) && $('task-1650') === null, 'the open list');
  }, 20000);

  it('edits and deletes a memo, asks before deleting, and puts it back with the reason on a refusal', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DOING);
    await fill('memo-text', 'first note');
    await press($('memo-send'));
    await until(() => m.daemon.tasks.get({ task: MINE_DOING }).memos.length === 1, 'the daemon memo');
    const id = m.daemon.tasks.get({ task: MINE_DOING }).memos[0]!.id;
    await until(() => $(`memo-edit-${id}`)?.getAttribute('aria-disabled') !== 'true', 'the numbered memo');
    await press($(`memo-edit-${id}`));
    await fill('memo-edit-text', 'second note');
    m.daemon.knobs.refuse('task.memo.edit', 'state', 'that task is being merged');
    await press($('sheet-submit'));
    await until(() => text().includes('that task is being merged'), 'the reason', 3000);
    expect($('sheet')).not.toBeNull();
    expect(m.daemon.tasks.get({ task: MINE_DOING }).memos[0]!.text).toBe('first note');
    m.daemon.knobs.clear();
    await press($('sheet-submit'));
    await until(() => $('sheet') === null, 'the sheet to close');
    expect(m.daemon.tasks.get({ task: MINE_DOING }).memos[0]!.text).toBe('second note');
    expect($(`memo-${id}`)?.textContent).toContain('second note');
    await press($(`memo-delete-${id}`));
    expect(text()).toContain('Delete this memo?');
    await press($('sheet-cancel'));
    expect(m.daemon.tasks.get({ task: MINE_DOING }).memos.length).toBe(1);
    await press($(`memo-delete-${id}`));
    m.daemon.knobs.refuse('task.memo.delete', 'state', 'that memo is locked');
    await press($('sheet-submit'));
    await until(() => text().includes('that memo is locked'), 'the reason', 3000);
    expect($(`memo-${id}`)).not.toBeNull();
    m.daemon.knobs.clear();
    await press($('sheet-submit'));
    await until(() => m.daemon.tasks.get({ task: MINE_DOING }).memos.length === 0, 'the daemon');
    await until(() => $(`memo-${id}`) === null, 'the memo gone');
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
