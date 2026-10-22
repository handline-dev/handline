// The phone's notices: the bell with its unread count, a failure shown under the title bar that a tap closes, and the list behind the bell.
import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { RpcError } from '@handline/client';
import { $, box, mount, press, until } from '#test/harness.tsx';

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
});
