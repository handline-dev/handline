// Seeds a UI check needs that the first-load fixtures leave out: switch them on by name, in a test or at serve time.
import type { FakeDaemon } from '#src/daemon.ts';
import { task } from '#src/seedkit.ts';
import { CHAT_SEEDS } from '#src/seeds-chat.ts';
import { HR_SEEDS } from '#src/seeds-hr.ts';

const ARCH = 'handline.architect';
const AUD = 'handline.auditor';
const ME = 'human';

type Seed = (d: FakeDaemon) => void;

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
      now: 'handline.pc',
      progress: 'sub 2/3 done, now handline.pc, in review',
    });
    d.requests.add({
      title: 'Which build is live?',
      task: 6020,
      status: 'waiting_on_you',
      agent: 'handline',
      holders: ['handline'],
      open: 1,
      now: null,
      progress: 'waiting on you',
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

  /** The architect hit the session limit (needs_you kind usage): its chat shows the needs-you card. */
  'agent-usage': (d) => {
    const s = d.sessions.get(ARCH)!;
    const since = new Date(Date.now() - 3 * 60_000).toISOString();
    s.fx.view = { ...s.fx.view, needs_you: { kind: 'usage', since, title: 'Usage limit: choose what to do', text: null, source: 'hook' } };
  },

  /** The account beta stalled on a billing error: its row shows STALLED and a founder can Wake it. */
  'account-stalled': (d) =>
    void d.accounts.stall('beta', { reason: 'billing_error', detail: 'The turn ended: the organization has a billing problem.' }),
  ...HR_SEEDS,
  ...CHAT_SEEDS,
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
