// The Requests tab: the PC's four groups with counts, a row that opens its item full screen, the item's own answers held five seconds
// (Undo), the next item opening after an answer, and one count for the badge, the header and the groups.
import { act } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ARCH, $, box, mount, press, text, until } from '#test/harness.tsx';
import { HOLD_MS } from '#src/heldAnswers.ts';

const DECISION = 3; // the fake's seeded decision
const DECISION_TASK = 1684; // the founder's task the seeded decision belongs to
const row = (key: string) => $(`request-${key}`);
const open = async (key: string) => {
  await until(() => row(key), 'the row');
  await press(row(key));
  await until(() => $('request-page'), 'the item');
};
const elapse = () => act(async () => void vi.advanceTimersByTime(HOLD_MS));
type Setup = Parameters<Parameters<typeof mount>[0] & {}>[0];
const withDecisionTask = (s: Setup) => {
  s.daemon.tasks.verbs = true;
  const decision = s.daemon.inbox['items'].find((i) => i.id === DECISION);
  if (decision) decision.task = DECISION_TASK;
};
const timers = () => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'], shouldAdvanceTime: true });

describe('the Requests tab', () => {
  afterEach(() => void vi.useRealTimers());

  it('shows its skeleton while slow, then the groups with counts and rows with their source', async () => {
    let release = () => {};
    const m = await mount((s) => (withDecisionTask(s), (release = s.daemon.knobs.hold('inbox.list'))), undefined, 'founder');
    await press($('nav-requests'));
    await until(() => $('skeleton-inbox'), 'the skeleton');
    release();
    await until(() => row(`item:${DECISION}`), 'the decision');
    expect(text($(`request-group-Decide`))).toMatch(/Decide · [1-9]/);
    expect(text(row(`item:${DECISION}`))).toContain('Task');
    expect($('requests-count')?.textContent).toBe($('requests-badge')?.textContent);
    m.store.stop();
  });

  it('opens an item full screen with n of N, and up and down walk the list in its groups', async () => {
    const m = await mount(withDecisionTask, undefined, 'founder', { opens: 'auto' });
    await until(() => $('requests-badge'), 'the badge');
    const all = Number($('requests-count')?.textContent);
    await press(box.querySelector<HTMLElement>('[data-testid^="request-item:"], [data-testid^="request-task:"]'));
    await until(() => $('request-page'), 'the item');
    expect($('nav-requests')).toBeNull(); // full screen: no tab bar
    expect(text($('request-position'))).toBe(`1 of ${all}`);
    expect($('request-prev')?.getAttribute('aria-disabled')).toBe('true');
    await press($('request-next'));
    expect(text($('request-position'))).toBe(`2 of ${all}`);
    await press($('request-back'));
    await until(() => $('requests-count'), 'the list');
    expect($('nav-requests')).not.toBeNull();
    m.store.stop();
  });

  it('Done is held five seconds, the next item opens, and the daemon is written once the hold is over', async () => {
    timers();
    const m = await mount(withDecisionTask, undefined, 'founder', { opens: 'auto' });
    await open(`item:${DECISION}`);
    const before = Number($('requests-badge')?.textContent ?? text($('request-position')).split(' of ')[1]);
    await press($('request-act-done'));
    expect(m.daemon.tasks.get({ task: DECISION_TASK }).state).not.toBe('done');
    await until(() => $('undo-line'), 'the Undo line');
    expect(text($('undo-line'))).toContain('Sends in 5 s');
    expect(text($('request-position'))).not.toContain(`of ${before}`); // the count dropped with the item gone
    await elapse();
    await until(() => m.daemon.tasks.get({ task: DECISION_TASK }).state === 'done', 'the daemon');
    expect(m.daemon.inbox.get(DECISION).read).toBe(true);
    m.store.stop();
  });

  it('Undo within the hold sends nothing and brings the item back', async () => {
    timers();
    const m = await mount(withDecisionTask, undefined, 'founder', { opens: 'auto' });
    await open(`item:${DECISION}`);
    await press($('request-act-done'));
    await press($('undo'));
    await elapse();
    expect(m.daemon.tasks.get({ task: DECISION_TASK }).state).not.toBe('done');
    expect(m.daemon.inbox.get(DECISION).read).toBe(false);
    m.store.stop();
  });

  it('puts a refused Done back and says why', async () => {
    timers();
    const m = await mount(withDecisionTask, undefined, 'founder', { opens: 'auto' });
    await open(`item:${DECISION}`);
    m.daemon.knobs.refuse('task.done', 'state', 'Already decided by the founder.');
    await press($('request-act-done'));
    await elapse();
    await until(() => m.store.notices.list().some((n) => n.text.includes('Already decided by the founder.')), 'the reason');
    await press($('request-back'));
    await until(() => row(`item:${DECISION}`), 'the item back in the list');
    m.store.stop();
  });

  it('shows a review item with its pictures and sends Approve as Approved once the hold is over', async () => {
    timers();
    const m = await mount(
      (s) => {
        s.daemon.tasks.verbs = true;
        const png = Buffer.from(
          'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
          'base64',
        );
        const pic = s.daemon.uploads.add('after.png', png);
        s.daemon.tasks.seed({ id: 9301, from: ARCH, to: 'me', title: 'Review: margin', state: 'doing', tags: ['review'] });
        s.daemon.tasks.memo({ task: 9301, text: `Margin widened.\n![after](attachment:${pic.id})` }, 'add', ARCH);
        void s.daemon.inbox.add({ kind: 'decision', agent: ARCH, title: 'Review: margin', body: 'Is this right?', task: 9301 });
      },
      undefined,
      'founder',
      { opens: 'auto' },
    );
    await until(() => $('request-group-Review'), 'the Review group');
    await open('item:6');
    await until(() => box.querySelector('[data-testid="pictures"]'), 'the pictures');
    expect($('request-act-done')).toBeNull(); // a review's answers are Approve and Ask for change, not Done
    expect(text($('request-act-ask_change'))).toBe('Ask for change');
    await press($('request-act-approve'));
    await elapse();
    await until(() => m.daemon.tasks.get({ task: 9301 }).state === 'done', 'the daemon');
    expect(m.daemon.tasks.get({ task: 9301 }).evidence).toBe('Approved');
    m.store.stop();
  });

  it("offers Open terminal on an item the daemon could not heal, and it opens that agent's terminal", async () => {
    const m = await mount(
      (s) => {
        withDecisionTask(s);
        const d = s.daemon.inbox['items'].find((i) => i.id === DECISION)!;
        Object.assign(d, { agent: ARCH, fix: 'open_terminal' });
      },
      undefined,
      'founder',
      { opens: 'auto' },
    );
    await open(`item:${DECISION}`);
    await press($('request-act-terminal'));
    await until(() => m.store.page?.agent === ARCH && m.store.page.tab === 'terminal', 'the terminal');
    m.store.stop();
  });

  it('shows the daemon’s sentence when the read is refused', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('inbox.list', 'state', 'the inbox is resting'), undefined, 'founder', {
      opens: 'auto',
    });
    await until(() => $('requests-badge') || $('nav-requests'), 'the tabs');
    await press($('nav-requests'));
    await until(() => $('error'), 'the error block');
    expect(text()).toContain('the inbox is resting');
    m.store.stop();
  });
});
