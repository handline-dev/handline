import { describe, expect, it } from 'vitest';
import { ARCH, until, $, text, press, mount, fill, value, verb } from './harness.tsx';
import type { setup } from '@handline/fake-daemon/helpers';

const tab = (name: string) => press($(`nav-${name}`));

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
    await press(await verb('done'));
    await press($('sheet-submit'));
    expect($('sheet-error')?.textContent).toBe('A reason is required.');
    await fill('sheet-text', 'shipped');
    m.daemon.knobs.latency(300);
    m.daemon.knobs.refuse('task.done', 'state', 'that task is waiting on an audit');
    await press($('sheet-submit'));
    await until(() => text().includes('done ▾'), 'the optimistic state', 250);
    await until(() => text().includes('that task is waiting on an audit'), 'the reason', 3000);
    await until(() => text().includes('doing ▾') && $('sheet'), 'the task back, the sheet open');
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
    await press(await verb('done'));
    await fill('sheet-text', 'shipped');
    await press($('sheet-submit'));
    await verb('reopen');
    await press(await verb('reopen'));
    await fill('sheet-text', 'one more thing');
    await press($('sheet-submit'));
    await until(() => m.daemon.tasks.get({ task: MINE_DOING }).state !== 'done', 'the daemon');
    m.store.stop();
  }, 20000);

  it('withdraws a draft after its confirmation, and cancels a running task with an optional reason', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DRAFT);
    expect((await verb('cancel'))?.textContent).toBe('Cancel');
    await press(await verb('withdraw'));
    expect(text()).toContain(`withdraw T${MINE_DRAFT}?`);
    await press($('sheet-cancel'));
    expect($('sheet')).toBeNull(); // nothing sent
    expect(m.daemon.tasks.get({ task: MINE_DRAFT }).state).toBe('draft');
    await press(await verb('withdraw'));
    await press($('sheet-submit'));
    await until(() => m.daemon.tasks.get({ task: MINE_DRAFT }).state !== 'draft', 'the daemon');
    await press($('task-back'));
    await openTask(MINE_DOING);
    expect((await verb('cancel'))?.textContent).toBe('Stop');
    await press(await verb('cancel'));
    expect(text()).toContain(`stop T${MINE_DOING}? why (optional):`);
    await press($('sheet-submit')); // no reason is fine
    await until(() => m.daemon.tasks.get({ task: MINE_DOING }).state !== 'doing', 'the daemon');
    m.store.stop();
  }, 20000);

  it('queues a draft and sends a queued task back to draft', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DRAFT);
    expect($('verb-draft')).toBeNull();
    await press(await verb('queue'));
    await until(() => m.daemon.tasks.get({ task: MINE_DRAFT }).state === 'queued', 'the daemon');
    await verb('draft');
    await press(await verb('draft'));
    await until(() => m.daemon.tasks.get({ task: MINE_DRAFT }).state === 'draft', 'the daemon');
    m.store.stop();
  }, 20000);

  it('discard wants a reason, sends nothing when cancelled, and shows the refusal', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DRAFT);
    await press(await verb('discard'));
    expect(text()).toContain(`discard T${MINE_DRAFT}? why:`);
    await press($('sheet-cancel'));
    expect(m.daemon.tasks.get({ task: MINE_DRAFT }).state).toBe('draft');
    await press(await verb('discard'));
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
    await press(await verb('edit'));
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

  it('hands the task to anyone from the assignee menu', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DRAFT);
    await press($('assignee-menu'));
    await until(() => $(`assign-${ARCH}`), 'the picker');
    await press($(`assign-${ARCH}`));
    await until(() => $('sheet') === null, 'the picker to close');
    await until(() => m.daemon.tasks.get({ task: MINE_DRAFT }).to === ARCH, 'the daemon');
    m.store.stop();
  }, 20000);

  it('keeps the edit sheet and its text when the daemon refuses, and shows the reason', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DRAFT);
    await press(await verb('edit'));
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
    await press(await verb('queue'));
    await until(() => text().includes('the queue is full'), 'the reason', 3000);
    expect(await verb('queue')).not.toBeNull();
    expect(m.daemon.tasks.get({ task: MINE_DRAFT }).state).toBe('draft');
    m.store.stop();
  }, 20000);

  it('filters the list: Mine, a state chip and Closed', async () => {
    await mount(verbs, undefined, 'founder');
    await tab('tasks');
    await until(() => $(`task-${MINE_DOING}`) && $('task-1690'), 'the rows');
    expect($('task-1650')).toBeNull(); // done is out of the way
    await press($('filter-toggle'));
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

  it('draws Markdown in a memo', async () => {
    const m = await mount(verbs, undefined, 'founder');
    await openTask(MINE_DOING);
    await fill('memo-text', '**bold** and `code`\n- one');
    await press($('memo-send'));
    await until(() => m.daemon.tasks.get({ task: MINE_DOING }).memos.length === 1, 'the daemon memo');
    const id = m.daemon.tasks.get({ task: MINE_DOING }).memos[0]!.id;
    await until(() => $(`memo-${id}`)?.textContent?.includes('•'), 'the drawn memo');
    expect($(`memo-${id}`)?.textContent).not.toContain('**');
  });

  it('shows a memo\'s image inline (a tap opens it full size) and its Markdown rendered, and says so for a file no longer kept', async () => {
    const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
    let ids: { img: string; md: string; gone: string } | undefined;
    const m = await mount((s) => {
      s.daemon.tasks.verbs = true;
      const up = s.daemon.uploads;
      const img = up.addMemoFile('shot.png', 'image/png', Buffer.from(PNG, 'base64'));
      const md = up.addMemoFile('plan.md', 'text/markdown', Buffer.from('# Plan\n\nA **bold** step and `code`.\n\n- first\n- second\n'));
      const gone = up.addMemoFile('old.md', 'text/markdown', Buffer.from('# Old'));
      up.expire(gone.id);
      s.daemon.tasks.memoWithFiles(MINE_DOING, 'handline.mobile', 'the mock', [img, md, { ...gone, gone: true }]);
      ids = { img: img.id, md: md.id, gone: gone.id };
    }, undefined, 'founder');
    await openTask(MINE_DOING);
    await until(() => $('memo-files')?.querySelector('div[aria-label="shot.png"]'), 'the image');
    await until(() => $(`memo-markdown-${ids!.md}`), 'the Markdown');
    const md = $(`memo-markdown-${ids!.md}`)!.textContent!;
    expect(md).toContain('A bold step and code.');
    expect(md).toContain('first');
    expect(md).not.toMatch(/#|\*\*|`/);
    expect(text($('memo-files'))).toContain('old.md: no longer kept.');
    await press($(`picture-${ids!.img}`));
    await until(() => document.body.querySelector(`[data-testid="picture-full-${ids!.img}"]`), 'the full view');
    await press(document.body.querySelector<HTMLElement>('[data-testid="picture-close"]'));
    m.store.stop();
  });

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
    expect(text()).toContain('Delete this comment?');
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
    expect((await verb('done'))?.getAttribute('aria-disabled')).toBe('true');
    expect(text()).toContain('this daemon does not serve task.done');
    expect($('memo-send')?.getAttribute('aria-disabled')).toBe('true'); // the fake serves no memos
    expect(text()).toContain('this daemon does not serve task.memo.add');
    m.store.stop();
  });
});

