import { act } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setup } from '@handline/fake-daemon/helpers';
import { Store } from '@handline/app-core';
import { useChat, useChatActions } from '../src/index.ts';
import { mount, probe, unmount, until } from './harness.tsx';

afterEach(unmount);
const AUD = 'handline.auditor'; // a structured session in the fake daemon

async function connected(seed?: (s: ReturnType<typeof setup>) => void) {
  const s = setup();
  seed?.(s);
  const store = new Store(s.client);
  await store.start();
  const v = probe(() => ({ chat: useChat(AUD, 'mine'), act: useChatActions(AUD) }));
  await mount(store, v.node);
  await until(() => v.get().chat.status === 'ready', 'the conversation');
  const rows = (body: string) => v.get().chat.messages.filter((m) => m.body === body);
  const sends = (spy: { mock: { calls: unknown[][] } }) =>
    spy.mock.calls.filter((c) => c[0] === 'chat.send').map((c) => (c[1] as { request_id: string }).request_id);
  return { ...s, store, v, rows, sends };
}

describe('send as one operation with a receipt', () => {
  it('a refused send is retried under the same operation id; the next message gets another', async () => {
    const { store, daemon, v, rows, sends } = await connected();
    const spy = vi.spyOn(store.client, 'call');
    daemon.knobs.refuse('chat.send', 'state', 'no');
    act(() => v.get().act.send('again'));
    await until(() => rows('again')[0]?.state === 'failed', 'the failed row');
    daemon.knobs.clear();
    act(() => v.get().act.retry(rows('again')[0]!.id));
    await until(() => rows('again').length === 1 && rows('again')[0]!.id > 0, 'the numbered row');
    act(() => v.get().act.send('another'));
    await until(() => (rows('another')[0]?.id ?? 0) > 0, 'the second message');
    const [first, retry, other] = sends(spy);
    expect(retry).toBe(first);
    expect(other).not.toBe(first);
  });

  it('a reply to an agent reply shows its quote at once, and a retry keeps parent_item', async () => {
    const { store, daemon, v, rows } = await connected((s) =>
      s.daemon.chat.seedItem(AUD, { kind: 'reply', text: 'I would cut the third gap.' }),
    );
    const item = v.get().chat.items.find((i) => i.kind === 'reply')!;
    const spy = vi.spyOn(store.client, 'call');
    daemon.knobs.refuse('chat.send', 'state', 'no', undefined, 1);
    act(() => v.get().act.send('why', { parent_item: item.id }));
    await until(() => rows('why')[0]?.state === 'failed', 'the failed row');
    expect(rows('why')[0]!.parent_item).toEqual({ id: item.id, text: item.text });
    act(() => v.get().act.retry(rows('why')[0]!.id));
    await until(() => (rows('why')[0]?.id ?? 0) > 0, 'sent');
    const calls = spy.mock.calls.filter((c) => c[0] === 'chat.send').map((c) => c[1] as { parent_item?: number });
    expect(calls.map((c) => c.parent_item)).toEqual([item.id, item.id]);
    expect(rows('why')[0]!.parent_item?.text).toBe(item.text);
  });

  it('delivery goes into chat.send, and a retry repeats it under the same id', async () => {
    const { store, daemon, v, rows } = await connected();
    const spy = vi.spyOn(store.client, 'call');
    daemon.knobs.refuse('chat.send', 'state', 'no', undefined, 1);
    act(() => v.get().act.send('now please', { delivery: 'now' }));
    await until(() => rows('now please')[0]?.state === 'failed', 'the failed row');
    act(() => v.get().act.retry(rows('now please')[0]!.id));
    await until(() => (rows('now please')[0]?.id ?? 0) > 0, 'sent');
    const calls = spy.mock.calls.filter((c) => c[0] === 'chat.send').map((c) => c[1] as { delivery?: string; request_id: string });
    expect(calls.map((c) => c.delivery)).toEqual(['now', 'now']);
    expect(calls[1]!.request_id).toBe(calls[0]!.request_id);
  });

  it('the row is queued at once and follows the receipt to sent, then confirmed', async () => {
    const { daemon, v, rows } = await connected();
    daemon.knobs.latency(300);
    act(() => v.get().act.send('hello'));
    await until(() => rows('hello').length === 1, 'the row at once');
    expect(rows('hello')[0]).toMatchObject({ landing: 'queued' });
    await until(() => rows('hello')[0]?.receipt?.state === 'sent', 'sent');
    expect(rows('hello')[0]!.id).toBeGreaterThan(0);
    daemon.knobs.latency(0);
    act(() => void daemon.chat.tick());
    await until(() => rows('hello')[0]?.receipt?.state === 'confirmed', 'confirmed');
    expect(rows('hello')[0]!.landing).toBe('delivered');
    expect(rows('hello')).toHaveLength(1);
  });

  it('two messages with the same text are two operations, even while the first is in flight', async () => {
    const { store, daemon, v, rows, sends } = await connected();
    const spy = vi.spyOn(store.client, 'call');
    daemon.knobs.latency(200);
    act(() => v.get().act.send('yes'));
    act(() => v.get().act.send('yes'));
    await until(() => rows('yes').length === 2 && rows('yes').every((m) => m.id > 0), 'both numbered');
    expect(new Set(sends(spy)).size).toBe(2);
  });

  it('a discarded failed send leaves nothing behind: the same text sent again is a new operation', async () => {
    const { store, daemon, v, rows, sends } = await connected();
    const spy = vi.spyOn(store.client, 'call');
    daemon.knobs.refuse('chat.send', 'state', 'no', undefined, 1);
    act(() => v.get().act.send('nope'));
    await until(() => rows('nope')[0]?.state === 'failed', 'the failed row');
    act(() => v.get().act.discard(rows('nope')[0]!.id));
    act(() => v.get().act.send('nope'));
    await until(() => (rows('nope')[0]?.id ?? 0) > 0, 'sent');
    const [first, second] = sends(spy);
    expect(second).not.toBe(first);
  });

  it('a message lost after it was written is unknown, is not sent again, and a new send is a new operation', async () => {
    const { store, daemon, v, rows, sends } = await connected();
    act(() => v.get().act.send('in flight'));
    await until(() => rows('in flight')[0]?.receipt?.state === 'sent', 'sent');
    const spy = vi.spyOn(store.client, 'call');
    act(() => daemon.chat.lose(AUD));
    act(() => void daemon.chat.tick());
    await until(() => rows('in flight')[0]?.receipt?.state === 'unknown', 'unknown');
    expect(rows('in flight')[0]).toMatchObject({ landing: 'typed' });
    await new Promise((r) => setTimeout(r, 50));
    expect(sends(spy)).toEqual([]);
    act(() => v.get().act.send('in flight'));
    await until(() => rows('in flight').length === 2, 'the second, new message');
    expect(new Set(sends(spy)).size).toBe(1);
  });

  it('a queued message is withdrawn without confirm and leaves the conversation', async () => {
    const { daemon, v, rows } = await connected();
    act(() => daemon.chat.setTurn(AUD, true));
    act(() => v.get().act.send('oops'));
    await until(() => rows('oops')[0]?.receipt?.state === 'queued', 'queued');
    await act(() => v.get().act.cancel(rows('oops')[0]!.id));
    await until(() => rows('oops').length === 0, 'withdrawn');
  });

  it('send now writes a queued message: its receipt goes to sent', async () => {
    const { daemon, v, rows } = await connected();
    act(() => daemon.chat.setTurn(AUD, true));
    act(() => v.get().act.send('hurry'));
    await until(() => rows('hurry')[0]?.receipt?.state === 'queued', 'queued');
    act(() => v.get().act.sendNow(rows('hurry')[0]!.id));
    await until(() => rows('hurry')[0]?.receipt?.state === 'sent', 'sent');
    expect(rows('hurry')[0]!.landing).toBe('typed');
  });
});
