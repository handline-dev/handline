import { afterEach, describe, expect, it, vi } from 'vitest';
import { setup } from '@handline/fake-daemon/helpers';
import { Store } from '@handline/app-core';
import { RpcError } from '@handline/client';
import { SKELETON_DELAY_MS, Skeleton, useAgentDetail, useAgents, useChat, useServes, viewState } from '../src/index.ts';
import { openStore, mount, probe, sleep, stubClient, unmount, until } from './harness.tsx';

afterEach(unmount);
const ARCH = 'handline.architect';

async function connected(opts: Parameters<typeof setup>[0] = {}) {
  const s = setup(opts);
  const store = new Store(s.client);
  await store.start();
  return { ...s, store };
}

describe('a read', () => {
  it("is loading, then has the daemon's answer", async () => {
    const { store } = await connected();
    const p = probe(() => useAgents());
    await mount(store, p.node);
    expect(p.get().isPending).toBe(true);
    await until(() => p.get().data, 'the agents');
    expect(p.get().data!.agents.map((a) => a.name)).toContain(ARCH);
  });

  it('is kept current by events, with no second read', async () => {
    const { store, client, daemon } = await connected();
    const spy = vi.spyOn(client, 'call');
    const p = probe(() => useAgents());
    await mount(store, p.node);
    await until(() => p.get().data, 'the agents');
    const reads = spy.mock.calls.filter((c) => c[0] === 'agents.list').length;
    daemon.emit('agents', 'agent.changed', {
      agent: ARCH,
      data: { ...p.get().data!.agents.find((a) => a.name === ARCH)!, state: 'blocked' },
    });
    await until(() => p.get().data!.agents.find((a) => a.name === ARCH)!.state === 'blocked', 'the event in the cache');
    expect(spy.mock.calls.filter((c) => c[0] === 'agents.list').length).toBe(reads);
  });

  it('is read again after a reconnect and after a gap, while mounted', async () => {
    const stub = stubClient((m) => (m === 'agents.list' ? { agents: [] } : {}));
    const p = probe(() => useAgents());
    await mount(openStore(stub), p.node);
    await until(() => p.get().data, 'the first read');
    expect(stub.count('agents.list')).toBe(1);
    stub.reconnect();
    await until(() => stub.count('agents.list') === 2, 'the read after reconnect');
    stub.gap();
    await until(() => stub.count('agents.list') === 3, 'the read after a gap');
  });

  it('keeps the last data under an error, and is not retried when refused', async () => {
    let n = 0;
    const stub = stubClient(() => {
      if (n++ === 0) return { agents: [{ name: 'a' }] };
      throw new RpcError('refused', 'refused', { reason: 'the daemon is sealed', gate: 'role' });
    });
    const p = probe(() => useAgents());
    await mount(openStore(stub), p.node);
    await until(() => p.get().data, 'the first read');
    stub.gap();
    await until(() => p.get().isError, 'the refusal');
    expect(p.get().data).toEqual({ agents: [{ name: 'a' }] });
    expect(stub.count('agents.list')).toBe(2);
    expect(viewState(p.get(), true)).toBe('error');
  });

  it('waits the time a busy daemon names, then asks again', async () => {
    let n = 0;
    const stub = stubClient(() => {
      if (n++ === 0) throw new RpcError('daemon_busy', 'busy', { retry_after_ms: 60 });
      return { agents: [] };
    });
    const p = probe(() => useAgents());
    await mount(openStore(stub), p.node);
    const t0 = Date.now();
    await until(() => p.get().data, 'the second try');
    expect(Date.now() - t0).toBeGreaterThanOrEqual(45);
    expect(stub.count('agents.list')).toBe(2);
  });

  it('does not run for a method the daemon does not list, or while the connection is not open', async () => {
    const stub = stubClient();
    const off = openStore(stub, ['agents.list']);
    const p = probe(() => ({ detail: useAgentDetail('a'), serves: useServes('agent.get') }));
    await mount(off, p.node);
    await sleep(30);
    expect(stub.count('agent.get')).toBe(0);
    expect(p.get().serves.served).toBe(false);
    expect(viewState(p.get().detail, p.get().serves.served)).toBe('not_served');
    await unmount();
    const closed = openStore(stub);
    closed.conn = 'reconnecting';
    const q = probe(() => useAgents());
    await mount(closed, q.node);
    await sleep(30);
    expect(stub.count('agents.list')).toBe(0);
  });

  it('separates two daemons by their ids', async () => {
    const stub = stubClient(() => ({ agents: [] }));
    const p = probe(() => useAgents());
    const { qc } = await mount(openStore(stub), p.node);
    await until(() => p.get().data, 'the read');
    expect(
      qc
        .getQueryCache()
        .getAll()
        .every((q) => q.queryKey[0] === 'd' && q.queryKey[1] === 'd1'),
    ).toBe(true);
  });

  it('a daemon without chat says so and is never asked for it', async () => {
    const { store, client } = await connected({ daemon: { chat: false } });
    const spy = vi.spyOn(client, 'call');
    const p = probe(() => useChat(ARCH, 'mine'));
    await mount(store, p.node);
    await sleep(30);
    expect(p.get().status).toBe('off');
    expect(spy.mock.calls.filter((c) => String(c[0]).startsWith('chat.'))).toEqual([]);
  });

  it('chat is read, paged, and its events reach the cache', async () => {
    const { store, daemon } = await connected();
    daemon.knobs.latency(100); // the fake would otherwise answer before the first assertion
    const p = probe(() => useChat(ARCH, 'mine'));
    await mount(store, p.node);
    expect(p.get().status).toBe('loading');
    await until(() => p.get().status === 'ready', 'the conversation');
    daemon.knobs.latency(0);
    const before = p.get().items.length;
    daemon.chat.seedItem(ARCH, { kind: 'reply', text: 'late' });
    daemon.emit('chat', 'chat.transcript', { agent: ARCH, data: { id: 99, at: 't', kind: 'reply', text: 'late' } });
    await until(() => p.get().items.length === before + 1, 'the transcript event');
    daemon.emit('chat', 'chat.working', { agent: ARCH, data: { turn_running: true, line: 'Thinking', since: 't' } });
    await until(() => p.get().working.turn_running, 'the working line');
  });
});

describe('the skeleton', () => {
  it('shows after the delay while a read waits, and is replaced by the content', async () => {
    const { store, daemon } = await connected();
    daemon.knobs.latency(500);
    const p = probe(() => useAgents());
    const View = () => <Skeleton shape="agents" loading={useAgents().isPending} label="agents" />;
    await mount(
      store,
      <>
        {p.node}
        <View />
      </>,
    );
    expect(document.querySelector('.skeleton')).toBeNull();
    await sleep(SKELETON_DELAY_MS + 100);
    const sk = document.querySelector('.skeleton')!;
    expect(sk.getAttribute('aria-busy')).toBe('true');
    expect(sk.getAttribute('role')).toBe('status');
    expect(sk.getAttribute('aria-label')).toBe('Loading agents');
    expect(sk.querySelectorAll('.sk').length).toBeGreaterThan(0);
    await until(() => p.get().data, 'the agents');
    await until(() => !document.querySelector('.skeleton'), 'the skeleton to go');
  });
});
