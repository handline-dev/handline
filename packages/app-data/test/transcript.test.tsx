import { afterEach, describe, expect, it } from 'vitest';
import { setup } from '@handline/fake-daemon/helpers';
import { Store } from '@handline/app-core';
import { RpcError, type TranscriptEvent, type TranscriptEvents } from '@handline/client';
import { makeQueryClient, qk, merge, EMPTY, toTurns, usePrompts, useTranscript, type Entry } from '../src/index.ts';
import { DAEMON, mount, openStore, probe, sleep, stubClient, unmount, until, type Stub } from './harness.tsx';

afterEach(unmount);
const AUDITOR = 'handline.auditor';
const tools = (entries: Entry[]) => entries.filter((e): e is Extract<Entry, { type: 'tool' }> => e.type === 'tool');

let at = 0;
const ev = (seq: number, kind: TranscriptEvent['kind'], turn: string | null, rest: Partial<TranscriptEvent> = {}): TranscriptEvent =>
  ({
    agent: 'a',
    session: 's',
    seq,
    turn,
    at: `2026-10-08T21:00:${String(at++ % 60).padStart(2, '0')}Z`,
    item: null,
    kind,
    ...rest,
  }) as TranscriptEvent;
const text = (seq: number, turn = 't1') => ev(seq, 'text', turn, { text: `line ${seq}` });
const page = (events: TranscriptEvent[], o: Partial<TranscriptEvents> = {}): TranscriptEvents => ({
  events,
  more: false,
  first_seq: 1,
  last_seq: events.at(-1)?.seq ?? 0,
  gap: false,
  working: { turn_running: false, line: null, since: null },
  ...o,
});
const frame = (stub: Stub, e: TranscriptEvent) =>
  stub.emit({ type: 'transcript.event', topic: 'transcript', agent: e.agent, data: e as never });

/** A stub daemon holding `log`; transcript.events answers from it like the real one. */
function daemonOf(log: TranscriptEvent[], o: { first?: number; gapBelow?: number } = {}) {
  const asked: Record<string, unknown>[] = [];
  const stub = stubClient((m, p) => {
    if (m !== 'transcript.events') return {};
    asked.push(p);
    const first = o.first ?? 1;
    const limit = p.limit as number;
    const kept = log.filter((e) => e.seq >= first);
    if (p.since_seq !== undefined) {
      const since = p.since_seq as number;
      const gap = since < first - 1;
      const rest = kept.filter((e) => e.seq > since);
      return page(rest.slice(0, limit), { more: rest.length > limit, first_seq: first, gap });
    }
    const pool = kept.filter((e) => p.before_seq === undefined || e.seq < (p.before_seq as number));
    return page(pool.slice(-limit), { more: pool.length > limit, first_seq: first });
  });
  return { stub, asked, log };
}
const seqs = (t: ReturnType<typeof useTranscript>) => t.turns.flatMap((x) => x.entries.map((e) => e.seq));

describe('the transcript model', () => {
  it('orders by seq whatever order events come in, and drops a seq it already holds', () => {
    const log = merge(merge(EMPTY, [text(3), text(1)]), [text(2), text(3)]);
    expect(log.events.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(merge(log, [text(2)])).toBe(log);
  });

  it('folds a turn: the message, its entries, a tool with its result, and the end', () => {
    const turns = toTurns(
      merge(EMPTY, [
        ev(1, 'turn_start', 't1', { turn_start: { message: 'm1', from: 'founder', text: 'go' } }),
        text(2),
        ev(3, 'tool_use', 't1', { tool_use: { id: 'u1', tool: 'Bash', input: { command: 'ls' } } }),
        ev(4, 'tool_result', 't1', { tool_result: { tool_use_id: 'u1', ok: true, text: 'a b', truncated: false } }),
        ev(5, 'turn_end', 't1', {
          turn_end: { reason: 'done', cost_usd: 0.1, duration_ms: 5, usage: { input: 1, output: 2, cache_read: 0, cache_write: 0 } },
        }),
        ev(6, 'turn_start', 't2', { turn_start: { message: 'm2', from: null, text: 'again' } }),
      ]),
    );
    expect(turns.map((t) => [t.id, t.message?.text, t.running, t.end?.cost_usd])).toEqual([
      ['t1', 'go', false, 0.1],
      ['t2', 'again', true, undefined],
    ]);
    const [tool] = tools(turns[0]!.entries);
    expect(tool).toMatchObject({ id: 'u1', tool: 'Bash', input: { command: 'ls' }, result: { ok: true, text: 'a b' } });
    expect(turns[0]!.entries.map((e) => e.type)).toEqual(['text', 'tool']);
  });

  it('links a permission card to its tool call until the answer ends it, and skips a kind it does not know', () => {
    const prompt = { id: 'u1', agent: 'a', kind: 'permission', options: [] } as never;
    const open = merge(EMPTY, [
      ev(1, 'tool_use', 't1', { tool_use: { id: 'u1', tool: 'Bash', input: {} } }),
      ev(2, 'permission', 't1', { permission: { tool_use_id: 'u1', prompt } }),
      ev(3, 'thinking' as never, 't1'),
    ]);
    const [t1] = tools(toTurns(open)[0]!.entries);
    expect(t1!.permission).toEqual({ prompt, open: true });
    expect(toTurns(open)[0]!.entries).toHaveLength(1);
    const answered = merge(open, [
      ev(4, 'permission_answer', 't1', {
        permission_answer: { tool_use_id: 'u1', behavior: 'deny', by: 'person', rule: null, message: 'no' },
      }),
    ]);
    const [t2] = tools(toTurns(answered)[0]!.entries);
    expect(t2).toMatchObject({ permission: { prompt, open: false }, answer: { behavior: 'deny', by: 'person' } });
  });

  it('shows a policy answer with no card, and a result whose tool_use is older than what is held', () => {
    const turns = toTurns(
      merge(EMPTY, [
        ev(7, 'tool_result', 't1', { tool_result: { tool_use_id: 'old', ok: false, text: 'x', truncated: true } }),
        ev(8, 'tool_use', 't1', { tool_use: { id: 'u2', tool: 'Read', input: {} } }),
        ev(9, 'permission_answer', 't1', {
          permission_answer: { tool_use_id: 'u2', behavior: 'allow', by: 'policy', rule: 'read', message: null },
        }),
      ]),
    );
    const [orphan, policy] = tools(turns[0]!.entries);
    expect(orphan).toMatchObject({ id: 'old', tool: null, result: { truncated: true } });
    expect(policy).toMatchObject({ permission: null, answer: { by: 'policy', rule: 'read' } });
  });
});

describe('useTranscript on the fake daemon', () => {
  async function connected() {
    const s = setup();
    const store = new Store(s.client);
    await store.start();
    return { ...s, store };
  }

  it('reads the newest page in seq order and folds the seeded turn', async () => {
    const { store } = await connected();
    const p = probe(() => useTranscript(AUDITOR));
    await mount(store, p.node);
    await until(() => p.get().status === 'ready', 'the transcript');
    const [turn] = p.get().turns;
    expect(turn!.message?.text).toBe('Run the contract tests.');
    expect(turn!.end?.reason).toBe('done');
    expect(turn!.entries.map((e) => e.type)).toEqual(['text', 'tool', 'error']);
    const seq = seqs(p.get());
    expect(seq).toEqual([...seq].sort((a, b) => a - b));
    expect(tools(turn!.entries)[0]).toMatchObject({ result: { text: '26 passed, 0 failed' }, answer: { by: 'person' } });
    expect(p.get().running).toBe(false);
  });

  it('adds live events, and the permission card it shows is the Prompt that prompt.answer closes', async () => {
    const { store, daemon, client } = await connected();
    const p = probe(() => useTranscript(AUDITOR));
    await mount(store, p.node);
    await until(() => p.get().status === 'ready', 'the transcript');
    daemon.transcript.push(AUDITOR, { kind: 'turn_start', turn: 't9', turn_start: { message: 't9', from: 'founder', text: 'push' } });
    const id = daemon.transcript.ask(AUDITOR, 't9', 'Bash', 'git push');
    await until(() => p.get().turns.at(-1)?.id === 't9' && !!tools(p.get().turns.at(-1)!.entries)[0]?.permission, 'the card');
    expect(p.get().running).toBe(true);
    const card = tools(p.get().turns.at(-1)!.entries)[0]!.permission!;
    expect(card.open).toBe(true);
    expect(card.prompt.id).toBe(id);
    await client.call('prompt.answer', { agent: AUDITOR, prompt: card.prompt.id, choice: 'allow', request_id: 'r-1' });
    await until(() => !p.get().running, 'the end of the turn');
    expect(tools(p.get().turns.at(-1)!.entries)[0]).toMatchObject({
      permission: { open: false },
      answer: { behavior: 'allow' },
      result: { ok: true },
    });
  });

  it('says terminal for an agent that is not a structured session', async () => {
    const { store } = await connected();
    const p = probe(() => useTranscript('handline.term-build'));
    await mount(store, p.node);
    await until(() => p.get().status === 'terminal', 'the unsupported answer');
  });

  it("is an error to retry, not a terminal, when a structured agent's read is refused", async () => {
    const { store } = await connected();
    const p = probe(() => useTranscript('handline.term-build', true));
    await mount(store, p.node);
    await until(() => p.get().status === 'error', 'the refused read');
  });

  it('marks a retention gap against the fake daemon that forgot events, and goes on from the first it keeps', async () => {
    const { store, daemon } = await connected();
    const qc = makeQueryClient();
    // What a view held before it was away: the first three events.
    qc.setQueryData(qk.transcriptEvents(DAEMON, AUDITOR), {
      log: merge(EMPTY, daemon.transcript.events(AUDITOR).slice(0, 3)),
      floor: 1,
    });
    for (let i = 0; i < 6; i++) daemon.transcript.push(AUDITOR, { kind: 'text', turn: 't-late', text: `late ${i}` });
    daemon.transcript.forget(AUDITOR, 10); // 14 events existed; it keeps 11 to 14
    const p = probe(() => useTranscript(AUDITOR));
    await mount(store, p.node, qc);
    p.get().refetch();
    await until(() => seqs(p.get()).includes(14), 'the events after the gap');
    const entries = p.get().turns.flatMap((t) => t.entries);
    const shown = entries.map((e) => (e.type === 'gap' ? 'gap' : e.seq));
    expect(shown.slice(0, 3)).toEqual([2, 3, 'gap']); // seq 1 is the turn's message
    expect(shown.slice(-4)).toEqual([11, 12, 13, 14]);
    expect(shown.filter((x) => x === 'gap')).toHaveLength(1);
    expect(p.get().hasOlder).toBe(false);
  });

  it('says terminal when the daemon does not serve the transcript', async () => {
    const s = setup({ daemon: { transcript: false } });
    const store = new Store(s.client);
    await store.start();
    const p = probe(() => useTranscript(AUDITOR));
    await mount(store, p.node);
    expect(p.get().status).toBe('terminal');
  });
});

describe('useTranscript on a scripted daemon', () => {
  const lines = (n: number, from = 1) => Array.from({ length: n }, (_, i) => text(from + i));

  it('applies events in order whatever order the stream delivers them, once each', async () => {
    const { stub } = daemonOf(lines(2));
    const p = probe(() => useTranscript('a'));
    await mount(openStore(stub), p.node);
    await until(() => p.get().status === 'ready', 'the read');
    frame(stub, text(3));
    frame(stub, text(3));
    frame(stub, text(2));
    await until(() => seqs(p.get()).length === 3, 'the live event');
    expect(seqs(p.get())).toEqual([1, 2, 3]);
  });

  it('keeps what the stream sent while the first read was out, above the read', async () => {
    let release = () => {};
    const stub = stubClient(async () => {
      await new Promise<void>((r) => (release = r));
      return page(lines(2));
    });
    const p = probe(() => useTranscript('a'));
    await mount(openStore(stub), p.node);
    frame(stub, text(2));
    frame(stub, text(3));
    release();
    await until(() => p.get().status === 'ready', 'the read');
    expect(seqs(p.get())).toEqual([1, 2, 3]);
  });

  it('fills a hole in the seq by reading from the last seq before it', async () => {
    const d = daemonOf(lines(5));
    const p = probe(() => useTranscript('a'));
    d.log.length = 2;
    await mount(openStore(d.stub), p.node);
    await until(() => seqs(p.get()).length === 2, 'the first read');
    d.log.push(...lines(3, 3));
    frame(d.stub, text(5));
    await until(() => seqs(p.get()).length === 5, 'the hole filled');
    expect(seqs(p.get())).toEqual([1, 2, 3, 4, 5]);
    expect(d.asked.at(-1)).toMatchObject({ since_seq: 2 });
  });

  it('resumes after a reconnect and after a gap from the last seq it holds, and pages while the daemon has more', async () => {
    const d = daemonOf(lines(2));
    const p = probe(() => useTranscript('a'));
    await mount(openStore(d.stub), p.node);
    await until(() => seqs(p.get()).length === 2, 'the first read');
    d.log.push(...lines(450, 3)); // missed while away: two pages of 200 and a rest
    d.stub.reconnect();
    await until(() => seqs(p.get()).length === 452, 'the missed events');
    expect(seqs(p.get())).toEqual(Array.from({ length: 452 }, (_, i) => i + 1));
    expect(d.asked.filter((a) => a.since_seq !== undefined).map((a) => a.since_seq)).toEqual([2, 202, 402]);
    d.log.push(text(453));
    d.stub.gap();
    await until(() => seqs(p.get()).length === 453, 'the read after a gap');
  });

  it('keeps an event the stream added while a fill was out', async () => {
    const d = daemonOf(lines(3));
    const p = probe(() => useTranscript('a'));
    await mount(openStore(d.stub), p.node);
    await until(() => p.get().status === 'ready', 'the read');
    let release = () => {};
    const inner = d.stub.reply;
    d.stub.reply = async (m, q) => {
      const r = inner(m, q);
      await new Promise<void>((res) => (release = res));
      return r;
    };
    d.stub.reconnect();
    await until(() => d.stub.count('transcript.events') === 2, 'the fill');
    frame(d.stub, text(4)); // the fill's reply does not hold it
    await until(() => seqs(p.get()).length === 4, 'the live event');
    release();
    await sleep(30);
    expect(seqs(p.get())).toEqual([1, 2, 3, 4]);
  });

  it('says why an older page did not load, and clears it on the next try', async () => {
    const d = daemonOf(lines(450));
    const p = probe(() => useTranscript('a'));
    await mount(openStore(d.stub), p.node);
    await until(() => p.get().status === 'ready', 'the newest page');
    const inner = d.stub.reply;
    d.stub.reply = (m, q) => {
      if (q.before_seq !== undefined) throw new RpcError('internal', 'down');
      return inner(m, q);
    };
    p.get().loadOlder();
    await until(() => p.get().olderError !== null, 'the error');
    d.stub.reply = inner;
    p.get().loadOlder();
    await until(() => p.get().olderError === null && seqs(p.get()).length === 400, 'the older page');
  });

  it('loads older pages with before_seq until the first the daemon keeps', async () => {
    const d = daemonOf(lines(450));
    const p = probe(() => useTranscript('a'));
    await mount(openStore(d.stub), p.node);
    await until(() => p.get().status === 'ready', 'the newest page');
    expect(seqs(p.get())[0]).toBe(251);
    expect(p.get().hasOlder).toBe(true);
    p.get().loadOlder();
    await until(() => seqs(p.get()).length === 400, 'the older page');
    expect(d.asked.at(-1)).toMatchObject({ before_seq: 251 });
    p.get().loadOlder();
    await until(() => !p.get().hasOlder, 'the oldest');
    expect(seqs(p.get())).toEqual(Array.from({ length: 450 }, (_, i) => i + 1));
  });

  it('keeps what it has under a failed resync, says error before the first read, and refetch reads again', async () => {
    let fail = true;
    const stub = stubClient(() => {
      if (fail) throw new RpcError('internal', 'down');
      return page(lines(2));
    });
    const p = probe(() => useTranscript('a'));
    await mount(openStore(stub), p.node);
    await until(() => p.get().status === 'error', 'the error');
    fail = false;
    p.get().refetch();
    await until(() => p.get().status === 'ready', 'the retry');
    fail = true;
    stub.reconnect();
    await until(() => p.get().error !== null, 'the failed resync');
    expect(p.get().status).toBe('ready');
    expect(seqs(p.get())).toEqual([1, 2]);
  });

  it('reads once at mount, not twice', async () => {
    const d = daemonOf(lines(2));
    const p = probe(() => useTranscript('a'));
    await mount(openStore(d.stub), p.node);
    await until(() => p.get().status === 'ready', 'the read');
    await new Promise((r) => setTimeout(r, 30));
    expect(d.stub.count('transcript.events')).toBe(1);
  });

  it('fills a hole between the first page and the events the stream sent while it was out, with no further event', async () => {
    const d = daemonOf(lines(2));
    let release = () => {};
    const inner = d.stub.reply;
    d.stub.reply = async (m, q) => {
      if (m === 'transcript.events' && q.since_seq === undefined) {
        const r = inner(m, q);
        await new Promise<void>((res) => (release = res));
        return r;
      }
      return inner(m, q);
    };
    const p = probe(() => useTranscript('a'));
    await mount(openStore(d.stub), p.node);
    d.log.push(...lines(3, 3));
    frame(d.stub, text(5));
    release();
    await until(() => seqs(p.get()).length === 5, 'the hole filled');
    expect(seqs(p.get())).toEqual([1, 2, 3, 4, 5]);
  });

  it('stops reading when a read adds nothing, even if it says there is more', async () => {
    const d = daemonOf(lines(3));
    const p = probe(() => useTranscript('a'));
    await mount(openStore(d.stub), p.node);
    await until(() => p.get().status === 'ready', 'the read');
    const inner = d.stub.reply;
    d.stub.reply = (m, q) => (q.since_seq !== undefined ? page([text(3)], { more: true }) : inner(m, q));
    const before = d.stub.count('transcript.events');
    d.stub.reconnect();
    await until(() => d.stub.count('transcript.events') > before, 'the resync');
    await new Promise((r) => setTimeout(r, 30));
    expect(d.stub.count('transcript.events') - before).toBe(1);
  });

  it('reads the prompt list again when a card opens or is answered', async () => {
    const d = daemonOf(lines(1));
    const p = probe(() => useTranscript('a'));
    const prompts = probe(() => usePrompts('a'));
    await mount(
      openStore(d.stub),
      <>
        {p.node}
        {prompts.node}
      </>,
    );
    await until(() => p.get().status === 'ready' && prompts.get().data, 'both reads');
    const n = d.stub.count('prompt.list');
    frame(d.stub, ev(2, 'permission', 't1', { permission: { tool_use_id: 'u1', prompt: { id: 'u1' } as never } }));
    await until(() => d.stub.count('prompt.list') === n + 1, 'the prompt list read');
    frame(
      d.stub,
      ev(3, 'permission_answer', 't1', {
        permission_answer: { tool_use_id: 'u1', behavior: 'allow', by: 'person', rule: null, message: null },
      }),
    );
    await until(() => d.stub.count('prompt.list') === n + 2, 'the prompt list read again');
  });

  it('ignores another agent', async () => {
    const { stub } = daemonOf(lines(1));
    const p = probe(() => useTranscript('a'));
    await mount(openStore(stub), p.node);
    await until(() => p.get().status === 'ready', 'the read');
    frame(stub, { ...text(2), agent: 'b' });
    await new Promise((r) => setTimeout(r, 20));
    expect(seqs(p.get())).toEqual([1]);
  });
});
