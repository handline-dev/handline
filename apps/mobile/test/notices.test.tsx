// The phone's notices: the bell with its unread count, a failure shown under the title bar that a tap closes, and the list behind the bell.
import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { RpcError } from '@handline/client';
import { applySeeds } from '@handline/fake-daemon/seeds';
import { $, ARCH, box, mount, press, until } from '#test/harness.tsx';

const text = (el: Element | null = box) => el?.textContent ?? '';

describe('the phone notices', () => {
  it('shows a failure under the title bar, closes it on a tap, and keeps it in the list read once opened', async () => {
    const m = await mount();
    await until(() => $('bell'), 'the bell');
    expect($('bell-count')).toBeNull();
    act(() => m.store.reportError('start', new RpcError('refused', 'busy')));
    await until(() => text($('toasts')).includes('start: busy'), 'the toast');
    expect(text($('bell-count'))).toBe('1');
    await press($('toasts')!.firstElementChild!);
    expect(text($('toasts'))).toBe('');
    expect(text($('bell-count'))).toBe('1');
    await press($('bell'));
    await until(() => $('notices-page'), 'the list');
    expect(text($('notices-page'))).toContain('start: busy');
    await press($('notices-close'));
    await until(() => $('bell'), 'back');
    expect($('bell-count')).toBeNull();
    m.store.stop();
  });

  it('counts the same failure twice as one line', async () => {
    const m = await mount();
    act(() => m.store.reportError('start', new RpcError('refused', 'busy')));
    act(() => m.store.reportError('start', new RpcError('refused', 'busy')));
    await until(() => text($('toasts')).includes('×2'), 'the count');
    expect(m.store.notices.list()).toHaveLength(1);
    m.store.stop();
  });

  it('says so when there is nothing yet', async () => {
    const m = await mount();
    await press($('bell'));
    await until(() => text($('notices-page')).includes('Nothing yet'), 'the empty list');
    m.store.stop();
  });

  it('draws a finished task as the PC bell does and opens that task on a tap', async () => {
    const m = await mount();
    const done = (cursor: number, data: Record<string, unknown>) =>
      act(() =>
        m.store.notices.takeEvent({
          cursor,
          topic: 'notifications',
          type: 'task_done',
          agent: 'handline.pc.auditor',
          title: `T${data.task} done`,
          data,
        } as never),
      );
    done(9001, { task: 5720, task_title: 'Audit PR #536', result: 'PASS, real Chrome checked' });
    await press($('bell'));
    await until(() => $('notice'), 'the row');
    const row = $('notice')!;
    expect(text(row)).toContain('Audit PR #536');
    expect(text(row)).toContain('T5720 done · PASS, real Chrome checked');
    expect(text(row)).toContain('au');
    expect($('notice-fresh')).not.toBeNull();
    await press(row);
    await until(() => $('task-back') && !$('notices-page'), 'the task');
    m.store.stop();
  });

  it('keeps a notice with no task as one text line, with no dot once read', async () => {
    const m = await mount();
    act(() => void m.store.notices.push('info', 'task 5637 done'));
    await press($('bell'));
    await until(() => $('notice'), 'the row');
    expect(text($('notice'))).toContain('task 5637 done');
    expect(text($('notice'))).not.toContain('·');
    await press($('notices-close'));
    await press($('bell'));
    await until(() => $('notice'), 'the row again');
    expect($('notice-fresh')).toBeNull();
    m.store.stop();
  });
  it('shows the weekly-loss notice with its first three tasks and a Start button; the X closes it', async () => {
    const m = await mount((s) => ((s.daemon.tasks.verbs = true), applySeeds(s.daemon, 'spare-notice')));
    await until(() => $('notice-spare'), 'the notice');
    expect(text($('notice-spare'))).toContain("18% of theta's weekly use will be lost at 02:10.");
    expect(text($('notice-spare'))).toContain('Tidy the gap list');
    expect(text($('notice-spare'))).not.toContain('T7104');
    expect(text($('notice-spare'))).toContain('and 1 more');
    expect(text($('notice-spare'))).not.toContain('handline.architect');
    expect(text($('notice-spare'))).toContain('Start 4 tasks');
    await press($('spare-close'));
    await until(() => !$('notice-spare'), 'the notice gone');
    m.store.stop();
  });

  describe('a finished task as a floating card', () => {
    const done = (m: Awaited<ReturnType<typeof mount>>, cursor: number, agent: string, extra: Record<string, unknown>) =>
      act(() =>
        m.store.notices.takeEvent({
          cursor,
          topic: 'notifications',
          type: 'task_done',
          agent,
          title: 'done',
          data: { task: cursor, task_title: `Task ${cursor}`, ...extra },
        } as never),
      );

    it('shows for a task the founder gave, opens it on a tap, and the top strip stays empty', async () => {
      const m = await mount();
      done(m, 7001, 'handline.pc.auditor', { from_founder: true });
      await until(() => $('done-card'), 'the card');
      expect(text($('done-card'))).toContain('Task 7001');
      expect(text($('done-card'))).toContain('T7001 done · auditor');
      expect(text($('toasts'))).toBe('');
      await press($('done-card')!.firstElementChild as HTMLElement);
      await until(() => $('task-back') && !$('done-card'), 'the task');
      m.store.stop();
    });

    it('does not show for a task between agents or when the daemon says nothing, but both reach the list', async () => {
      const m = await mount();
      done(m, 7002, 'handline.pc.auditor', { from_founder: false });
      done(m, 7003, 'handline.pc.auditor', {});
      await until(() => text($('bell-count')) === '2', 'both in the list');
      expect($('done-card')).toBeNull();
      expect(text($('toasts'))).toBe('');
      m.store.stop();
    });

    it("does not show for the agent whose page is open, and several at once are one 'N tasks done' card", async () => {
      const m = await mount();
      await until(() => $(`agent-${ARCH}`), 'the rows');
      await press($(`agent-${ARCH}`));
      await until(() => $('chat-input'), 'the chat');
      done(m, 7004, ARCH, { from_founder: true });
      done(m, 7005, 'handline.pc.auditor', { from_founder: true });
      await until(() => $('done-card'), 'the card');
      expect(text($('done-card'))).toContain('Task 7005');
      done(m, 7006, 'handline.pc.auditor', { from_founder: true });
      await until(() => text($('done-card')).includes('2 tasks done'), 'one card for both');
      m.store.stop();
    });
  });
});
