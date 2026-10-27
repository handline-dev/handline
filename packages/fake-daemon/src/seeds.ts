// Seeds a UI check needs that the first-load fixtures leave out: switch them on by name, in a test or at serve time.
import { crc32, deflateSync } from 'node:zlib';
import type { FakeDaemon } from '#src/daemon.ts';

const ARCH = 'handline.architect';
const AUD = 'handline.auditor';
const ME = 'human';

type Seed = (d: FakeDaemon) => void;

/** A one-colour PNG of the size given: a picture with a real height for a layout check. */
function solidPng(w: number, h: number): Buffer {
  const chunk = (type: string, data: Buffer) => {
    const out = Buffer.concat([Buffer.alloc(4), Buffer.from(type), data, Buffer.alloc(4)]);
    out.writeUInt32BE(data.length, 0);
    out.writeUInt32BE(crc32(out.subarray(4, out.length - 4)), out.length - 4);
    return out;
  };
  const head = Buffer.alloc(13);
  head.writeUInt32BE(w, 0);
  head.writeUInt32BE(h, 4);
  head.set([8, 2, 0, 0, 0], 8);
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(w * 3, 0x9a)]);
  return Buffer.concat([
    Buffer.from('89504e470d0a1a0a', 'hex'),
    chunk('IHDR', head),
    chunk('IDAT', deflateSync(Buffer.concat(Array.from({ length: h }, () => row)))),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

const task = (d: FakeDaemon, id: number, more: Parameters<FakeDaemon['tasks']['seed']>[0]) => d.tasks.seed({ ...more, id });

export const SEEDS: Record<string, Seed> = {
  /** An inbox card the daemon raised after its recovery failed, with an agent, so it offers Open terminal. */
  'inbox-terminal': (d) =>
    void d.inbox.add({
      kind: 'block',
      agent: AUD,
      title: 'Recovery failed: the agent did not come back',
      body: 'Every step the daemon could take was taken; the terminal is the last one.',
      fix: 'open_terminal',
    }),

  /** A review: a task tagged review under the agent's own, a first comment naming two pictures, and a decision card asking for a look. */
  review: (d) => {
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
    const [before, after] = [d.uploads.add('before.png', png), d.uploads.add('after.png', png)];
    d.tasks.seed({ id: 9301, from: ARCH, to: ME, title: 'Review: wider margin on the board', state: 'doing', tags: ['review'] });
    d.tasks.memo(
      { task: 9301, text: `Margin widened.\n![before](attachment:${before.id})\n![after](attachment:${after.id})` },
      'add',
      ARCH,
    );
    d.inbox.add({ kind: 'decision', agent: ARCH, title: 'Review: wider margin on the board', body: 'Is this right?', task: 9301 });
  },

  /** An open task given to the person, with its card in the architect's chat: the card then offers Open and Done. */
  'task-card': (d) => {
    d.tasks.seed({ id: 1695, from: ARCH, to: ME, title: 'Confirm the chat plan', state: 'doing' });
    d.chat.seedItem(ARCH, {
      at: new Date().toISOString(),
      kind: 'task_event',
      task_event: { event: 'opened', task: 1695, request: null, evidence: null, who: ARCH },
    });
  },

  /** The daemon's weekly-loss notice: a task for the person tagged spare-notice, listing four spare drafts. */
  'spare-notice': (d) => {
    const spare = [
      [7101, 'Tidy the gap list'],
      [7102, 'Add tests for the fold'],
      [7103, 'Check the phone card'],
      [7104, 'Rename the helpers'],
    ] as const;
    for (const [id, title] of spare) d.tasks.seed({ id, from: ME, to: ARCH, title, state: 'draft', tags: ['spare'] });
    d.tasks.seed({
      id: 7100,
      from: ARCH,
      to: ME,
      title: "18% of theta's weekly use will be lost at 02:10. These spare tasks can use it: T7101, T7102, T7103, T7104.",
      state: 'doing',
      tags: ['spare-notice'],
    });
  },

  /** Three HR proposals: a new agent (team, model), a setting change old -> new, and one to reject. */
  hr: (d) => {
    const hr = d.script.hr;
    hr.raise({
      id: 901,
      kind: 'add',
      agent: 'handline.new-builder',
      title: 'add handline.new-builder',
      diff: 'add agent handline.new-builder\nteam: handline\nmodel: sonnet',
      asked: { vendor: 'claude', model: 'sonnet', team: 'handline' },
      reason: 'The story has nine independent UI tasks and one builder.',
    });
    hr.raise({
      id: 902,
      kind: 'settings',
      agent: ARCH,
      title: `settings ${ARCH}`,
      diff: 'model: sonnet -> opus',
      asked: { sets: { model: 'opus' } },
      reason: 'Design reviews need the stronger model.',
    });
    hr.raise({
      id: 903,
      kind: 'retire',
      agent: 'handline.term-build',
      title: 'retire handline.term-build',
      diff: 'retire agent handline.term-build',
      reason: 'Its work is merged. Reject this one to keep the agent.',
    });
  },

  /** Two agents set their own handoff prompt, so the global one is ignored by them. */
  'handoff-own': (d) => {
    d.admin.handoffOwn.push('handline.auditor', 'handline.term-build');
  },

  /** Two more HR proposals: one applying, held for the agent's running turn, and one the founder asked for themself (`requested_by` human). */
  'hr-held': (d) => {
    const hr = d.script.hr;
    hr.raise({
      id: 904,
      kind: 'settings',
      agent: 'handline.auditor',
      title: 'settings handline.auditor',
      diff: 'effort: medium -> high',
      asked: { sets: { effort: 'high' } },
      state: 'applying',
      waiting_on: ['handline.auditor'],
    });
    hr.raise({
      id: 905,
      kind: 'retire',
      agent: 'handline.term-build',
      title: 'retire handline.term-build',
      diff: 'retire agent handline.term-build',
      requested_by: 'human',
      reason: 'Held until its running session ends.',
    });
  },

  /** 200 tasks, so a list has to scroll and page. */
  'tasks-200': (d) => {
    const states = ['queued', 'doing', 'done', 'draft'];
    for (let i = 0; i < 200; i++) {
      task(d, 6000 + i, {
        id: 6000 + i,
        from: ARCH,
        to: i % 2 ? AUD : 'handline.term-build',
        title: `Seeded task ${i + 1}`,
        state: states[i % states.length]!,
        priority: ((i % 3) + 1) as 1 | 2 | 3,
      });
    }
  },

  /** The founder's requests: one waiting on them, one in review, one doing, a done one, a message that is not a task yet. */
  requests: (d) => {
    d.requests.add({
      title: 'Show the usage graph first',
      task: 6071,
      status: 'doing',
      agent: 'handline.pc',
      holders: ['handline.pc'],
      open: 2,
      done: 1,
    });
    d.requests.add({
      title: 'Open a new shell from the + menu',
      task: 6068,
      status: 'review',
      agent: 'handline.pc',
      holders: ['handline.pc'],
      open: 1,
      done: 2,
      prs: [630],
    });
    d.requests.add({
      title: 'Which build is live?',
      task: 6020,
      status: 'waiting_on_you',
      agent: 'handline',
      holders: ['handline'],
      open: 1,
      unread_comments: 2,
    });
    d.requests.add({ title: 'Settings in one place', task: 5983, status: 'done', agent: 'handline.pc', done: 6 });
    d.requests.add({ title: 'Why is the build so slow today?', kind: 'message', agent: 'handline' });
  },

  /** The architect is held back for the machine's load: its chat and row say so, and agent.force_start starts it. */
  'agent-held': (d) => {
    const s = d.sessions.get(ARCH)!;
    const since = new Date(Date.now() - 4 * 60_000).toISOString();
    s.fx.view = {
      ...s.fx.view,
      status: {
        state: 'waiting',
        reason: 'load',
        text: 'Waiting: the computer is under heavy memory load right now. It starts by itself when the load drops.',
        until: null,
        since,
        tasks: [],
      },
    };
  },

  /** The architect waits on another task (status reason task): its chat shows the blocked-by card with the task as a link. */
  'agent-blocked': (d) => {
    const s = d.sessions.get(ARCH)!;
    const since = new Date(Date.now() - 12 * 60_000).toISOString();
    s.fx.view = {
      ...s.fx.view,
      status: { state: 'waiting', reason: 'task', text: 'blocked by T1684 (human)', until: null, since, tasks: [1684] },
    };
  },

  /** An approved proposal that did not apply, the daemon's error in its own words, and the daemon down notice's page. */
  'hr-apply-failed': (d) => {
    d.script.hr.raise({
      id: 908,
      kind: 'settings',
      agent: ARCH,
      title: 'settings handline.architect',
      diff: 'effort: high',
      state: 'apply_failed',
      error:
        'proposal p908 was not applied: agent.yaml is locked by another process. Retry when the lock clears (/Users/x/.handline/agents/handline.architect/agent.yaml).',
    });
  },

  /** Two approved proposals whose change is applied but whose agent did not start: the architect's waits for the load, the auditor's failed. */
  'hr-followup': (d) => {
    const hr = d.script.hr;
    hr.raise({
      id: 906,
      kind: 'settings',
      agent: ARCH,
      title: 'rename handline.architect',
      diff: 'rename handline.architect -> handline.planner',
      state: 'approved',
      settled: new Date().toISOString(),
      follow_up: { agent: ARCH, state: 'waiting', text: 'held: machine load - load 3.09 per core, limit 1.50' },
    });
    hr.raise({
      id: 907,
      kind: 'settings',
      agent: AUD,
      title: 'rename handline.auditor',
      diff: 'rename handline.auditor -> handline.reviewer',
      state: 'approved',
      settled: new Date().toISOString(),
      follow_up: { agent: AUD, state: 'failed', text: 'start failed: no free account' },
    });
  },

  /** Chat messages with one-tap actions: OK / No on an open task the founder owns. */
  'chat-actions': (d) => {
    task(d, 6902, { id: 6902, from: ARCH, to: ME, title: 'Open task the founder owns', state: 'doing' });
    d.chat.seed(ARCH, {
      from: ARCH,
      to: ME,
      body: 'T6902 is yours. Is it fine to go ahead?',
      tasks: [{ id: 6902, title: 'Open task the founder owns' }],
      actions: [
        { key: 'ok', label: 'OK', verb: 'task.done', task: 6902, params: { task: 6902 } },
        { key: 'no', label: 'No', verb: 'task.handoff', task: 6902, params: { task: 6902, to: ARCH }, needs: 'note' },
      ],
    });
  },

  /** A long chat with the architect: 120 messages (four pages), every seventh with a picture, for paging up through history. */
  'chat-long': (d) => {
    const png = solidPng(240, 160);
    for (let n = 1; n <= 120; n++) {
      const mine = n % 2 === 0;
      const shot = n % 7 === 0 ? d.uploads.add(`shot-${n}.png`, png) : null;
      d.chat.seed(ARCH, {
        from: mine ? ME : ARCH,
        to: mine ? ARCH : ME,
        body: `Message ${n}. ${'A line of the talk. '.repeat(1 + (n % 5))}`,
        ...(shot ? { attachments: [{ id: shot.id, name: shot.name, mime: 'image/png', bytes: png.length }] } : {}),
      });
    }
  },

  /** The account beta stalled on a billing error: its row shows STALLED and a founder can Wake it. */
  'account-stalled': (d) =>
    void d.accounts.stall('beta', { reason: 'billing_error', detail: 'The turn ended: the organization has a billing problem.' }),
};

/** Switches the named seeds on: a comma-separated list, or `all`. An unknown name is an error, not a silent no-op. */
export function applySeeds(d: FakeDaemon, names: string | string[]): string[] {
  const list = (Array.isArray(names) ? names : names.split(',')).map((n) => n.trim()).filter(Boolean);
  const on = list.includes('all') ? Object.keys(SEEDS) : list;
  for (const n of on) {
    const seed = SEEDS[n];
    if (!seed) throw new Error(`no seed "${n}"; known: ${Object.keys(SEEDS).join(', ')}, all`);
    seed(d);
  }
  return on;
}
