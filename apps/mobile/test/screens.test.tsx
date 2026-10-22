import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { ARCH, sleep, until, box, $, text, rowHeightOf, press, mount, pickTab } from '#test/harness.tsx';

describe('the agent list', () => {
  it('shows its skeleton after 150 ms while the daemon is slow, then the rows', async () => {
    const m = await mount((s) => s.daemon.knobs.latency(600));
    await until(() => $('skeleton-agents'), 'the skeleton');
    const skeletonRowHeight = rowHeightOf($('skeleton-agents')!.firstElementChild); // the first skeleton row
    expect($(`agent-${ARCH}`)).toBeNull();
    await until(() => $(`agent-${ARCH}`), 'the rows', 5000);
    await sleep(400); // the skeleton stays at least 300 ms once shown
    expect($('skeleton-agents')).toBeNull();
    expect(getComputedStyle($(`agent-${ARCH}`)!).minHeight).toBe(skeletonRowHeight); // the row may grow with a larger font, never shrink below the skeleton's
    expect(text()).toContain('architect');
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
    await until(() => $('verbs-open') && $('skeleton-agentDetail') === null, 'the page', 5000);
    m.store.stop();
  });
});

describe('the permission card', () => {
  it('shows "answering…" and keeps its buttons off while the daemon decides, and gives them back with the reason on a refusal', async () => {
    const m = await mount();
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await pickTab('terminal');
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
const done = (id: number) => $(`inbox-done-${id}`);
const DECISION_TASK = 1684; // the founder's task the seeded decision belongs to
const withDecisionTask = (s: Parameters<Parameters<typeof mount>[0] & {}>[0]) => {
  s.daemon.tasks.verbs = true;
  const decision = s.daemon.inbox['items'].find((i) => i.id === DECISION);
  if (decision) decision.task = DECISION_TASK;
};

describe('the inbox tab', () => {
  it('shows its skeleton while slow, then the waiting items, and a decision done leaves the list and ends its task at the daemon', async () => {
    const m = await mount((s) => (withDecisionTask(s), s.daemon.knobs.latency(600)));
    await tab('inbox');
    await until(() => $('skeleton-inbox'), 'the skeleton');
    await until(() => item(DECISION), 'the decision', 5000);
    m.daemon.knobs.latency(0);
    expect($('unread-count')?.textContent).toMatch(/^[1-9]/);
    await press($(`inbox-open-${DECISION}`));
    await press(done(DECISION));
    await until(() => item(DECISION) === null, 'the decision to leave');
    await until(() => m.daemon.tasks.get({ task: DECISION_TASK }).state === 'done', 'the daemon');
    expect(m.daemon.inbox.get(DECISION).read).toBe(true);
    m.store.stop();
  });

  it('shows a review card with its pictures, and Approve ends the task with Approved', async () => {
    const m = await mount((s) => {
      s.daemon.tasks.verbs = true;
      const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
      const pic = s.daemon.uploads.add('after.png', png);
      s.daemon.tasks.seed({ id: 9301, from: ARCH, to: 'me', title: 'Review: margin', state: 'doing', tags: ['review'] });
      s.daemon.tasks.memo({ task: 9301, text: `Margin widened.\n![after](attachment:${pic.id})` }, 'add', ARCH);
      void s.daemon.inbox.add({ kind: 'decision', agent: ARCH, title: 'Review: margin', body: 'Is this right?', task: 9301 });
    });
    await tab('inbox');
    await until(() => $('inbox-6'), 'the card');
    await press($('inbox-open-6'));
    await until(() => $('review-card'), 'the review card');
    await until(() => box.querySelector('[data-testid="pictures"]'), 'the pictures');
    expect(box.querySelector('[data-testid^="inbox-done-"]')).toBeNull();
    await press(box.querySelector('[data-testid^="inbox-approve-"]') as HTMLElement);
    await until(() => m.daemon.tasks.get({ task: 9301 }).state === 'done', 'the daemon');
    expect(m.daemon.tasks.get({ task: 9301 }).evidence).toBe('Approved');
    m.store.stop();
  });

  it('offers Open terminal on a card the daemon could not heal, and it opens that agent\'s terminal', async () => {
    const m = await mount((s) => {
      const r = s.daemon.inbox['items'].find((i) => i.id === RESULT)!;
      Object.assign(r, { agent: ARCH, fix: 'open_terminal' });
    });
    await tab('inbox');
    await until(() => item(RESULT), 'the items');
    expect($(`inbox-terminal-${RESULT}`)).toBeNull();
    await press($(`inbox-open-${RESULT}`));
    await press($(`inbox-terminal-${RESULT}`));
    await until(() => m.store.page?.agent === ARCH && m.store.page.tab === 'terminal', 'the terminal');
    m.store.stop();
  });

  it('marks a result read from its card, and Mark all read then the last decision empty the list into the empty sentence', async () => {
    const m = await mount(withDecisionTask);
    await tab('inbox');
    await until(() => item(RESULT), 'the items');
    await press($(`inbox-open-${RESULT}`));
    await press($(`inbox-read-${RESULT}`));
    await until(() => item(RESULT) === null, 'the result to leave');
    await until(() => m.daemon.inbox.get(RESULT).read, 'the daemon');
    await press($('mark-all-read'));
    await until(() => $('unread-count')?.textContent === '0', 'the count to drop');
    await press($(`inbox-open-${DECISION}`));
    await press(done(DECISION));
    await until(() => $('inbox-clear'), 'the empty sentence');
    expect(text()).toContain('Nothing waits on you');
    expect(item(RESULT)).toBeNull(); // what is read waits in the Read group, shut
    await press($('inbox-group-open-read'));
    await until(() => item(RESULT), 'the read item in its group');
    m.store.stop();
  });

  it('shows the task board\'s messages as one row that opens to them', async () => {
    const m = await mount((s) => {
      s.daemon.inbox.add({ kind: 'say', agent: 'task-board', title: 'T1 done by a', created: new Date().toISOString() });
      s.daemon.inbox.add({ kind: 'say', agent: 'task-board', title: 'T2 done by b', created: new Date().toISOString() });
    });
    await tab('inbox');
    await until(() => $('inbox-group-digest'), 'the digest row');
    expect(text($('inbox-group-digest'))).toContain('task-board: 2 messages');
    expect(text()).not.toContain('T1 done by a'); // shut, they are not listed one by one
    await press($('inbox-group-open-digest'));
    await until(() => text($('inbox-group-digest')).includes('T2 done by b'), 'the messages');
    expect(text($('inbox-group-digest'))).toContain('T1 done by a');
    m.store.stop();
  });

  it('puts a refused Done back and says why', async () => {
    const m = await mount(withDecisionTask);
    await tab('inbox');
    await until(() => item(DECISION), 'the items');
    m.daemon.knobs.refuse('task.done', 'state', 'Already decided by the founder.');
    await press($(`inbox-open-${DECISION}`));
    await press(done(DECISION));
    await until(() => text().includes('Already decided by the founder.'), 'the reason', 3000);
    await until(() => item(DECISION) && done(DECISION), 'the decision back with its Done');
    expect(m.daemon.tasks.get({ task: DECISION_TASK }).state).not.toBe('done');
    m.store.stop();
  });

  it('keeps a typed reply through a refusal, and clears it once the daemon accepts', async () => {
    const m = await mount();
    await tab('inbox');
    await until(() => item(RESULT), 'the items');
    m.daemon.knobs.refuse('inbox.reply', 'state', 'That thread is closed.');
    await press($(`inbox-open-${RESULT}`));
    await press($(`inbox-reply-${RESULT}`));
    const type = async (v: string) =>
      act(async () => {
        const f = $('inbox-text') as unknown as HTMLTextAreaElement;
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(f, v);
        f.dispatchEvent(new Event('input', { bubbles: true }));
      });
    await type('Relay it, one socket.');
    await press($('inbox-send'));
    await until(() => text().includes('That thread is closed.'), 'the reason', 3000);
    await until(() => ($('inbox-text') as unknown as HTMLTextAreaElement | null)?.value === 'Relay it, one socket.', 'the text kept');
    m.daemon.knobs.clear();
    await press($('inbox-send'));
    await until(() => item(RESULT) === null, 'the card to leave');
    await until(() => m.daemon.inbox.get(RESULT).read, 'the daemon');
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
    await pickTab('terminal');
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
    await pickTab('terminal');
    await until(() => $('terminal-text')?.textContent?.includes('Claude Code'), 'the screen');
    expect($('terminal-text')!.querySelector('*')).toBeNull();
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
  const tick = (e: HTMLElement | undefined) => e?.querySelector('[data-testid^="landing-"]')?.getAttribute('aria-label');
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

  it('shows the conversation with how far each message got', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await until(() => msgOf('Use the contract fixtures.'), 'the messages');
    expect(tick(msgOf('Use the contract fixtures.')!)).toBe('Answered');
    expect(tick(msgOf('Draft the web chat tab plan.')!)).toBe('Answered');
    expect(text()).not.toContain('Delivered'); // ticks, no word
    expect(msgOf('Use the contract fixtures.')!.textContent).not.toContain('#'); // no message numbers on screen
    expect(text()).toContain('Here is the plan: three units, chat first.'); // the agent's reply
    expect(msgOf('Review is done, see the notes.')).toBeUndefined();
    expect($('scope-all')).toBeNull(); // no Mine/All menu
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
    expect($('working')!.textContent).toContain('now: Read src/main.ts');
    expect(tick(msgOf('run the tests')!)).toBe('Answered');
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
    await until(() => tick(msgOf('please retry me')) === 'Sending', 'the retried message');
    expect(msgOf('please retry me')!.textContent).not.toContain('the daemon is resting');
    m.store.stop();
  });

  it('replies to a picked message with its number', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await until(() => msgOf('Use the contract fixtures.'), 'the messages');
    await press(msgOf('Use the contract fixtures.')!);
    expect($('replying')!.textContent).toContain('Use the contract fixtures.');
    await type('chat-input', 'about that');
    await press($('chat-send'));
    await until(() => msgOf('about that'), 'the reply');
    expect(msgOf('about that')!.textContent).toContain('↩ reply');
    expect($('replying')).toBeNull();
    m.store.stop();
  });

  it('a picked message is a reference: it is sent as a linked message', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    const q = 'Which gap list should come first?';
    await until(() => msgOf(q), 'the question');
    await press(msgOf(q)!);
    await type('chat-input', 'web first');
    await press($('chat-send'));
    await until(() => msgOf('web first'), 'the reply');
    expect(msgOf('web first')!.textContent).toContain('↩ reply');
    m.store.stop();
  });

  it('a question from the agent is an ordinary message: no answer box, no Dismiss, no waiting mark', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await until(() => msgOf('Which gap list should come first?'), 'the question');
    const el = msgOf('Which gap list should come first?')!;
    const id = Number(el.getAttribute('data-testid')!.slice(4));
    expect($(`answer-input-${id}`)).toBeNull();
    expect($(`dismiss-${id}`)).toBeNull();
    expect(el.textContent).not.toContain('Waiting for you');
    m.store.stop();
  });

  it('keeps an unsent draft per agent while the person is on another tab', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await openChat();
    await type('chat-input', 'half a thought');
    await pickTab('terminal');
    await pickTab('chat');
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
