// Seeds a UI check needs that the first-load fixtures leave out: switch them on by name, in a test or at serve time.
import type { FakeDaemon } from '#src/daemon.ts';

const ARCH = 'handline.architect';
const AUD = 'handline.auditor';
const ME = 'human';

type Seed = (d: FakeDaemon) => void;

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
    d.tasks.memo({ task: 9301, text: `Margin widened.\n![before](attachment:${before.id})\n![after](attachment:${after.id})` }, 'add', ARCH);
    d.inbox.add({ kind: 'decision', agent: ARCH, title: 'Review: wider margin on the board', body: 'Is this right?', task: 9301 });
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
