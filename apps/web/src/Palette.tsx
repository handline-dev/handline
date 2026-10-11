// The ⌘K palette (G14): one box for agents, tasks by number or title, the pages and a few actions. Arrows move, Enter goes, Escape closes.
import { useMemo, useState } from 'react';
import { useAgents, useTasks } from '@handline/app-data';
import { goAgent, goInbox, goTasks, goTeam } from './route.ts';
import { Backdrop, Icon, type IconName } from './ui/index.ts';

interface Hit {
  key: string;
  icon: IconName;
  label: string;
  hint?: string;
  run: () => void;
}

const PAGES: [string, IconName, () => void][] = [
  ['Inbox', 'inbox', goInbox],
  ['Tasks', 'tasks', () => goTasks()],
  ['Team', 'team', () => goTeam()],
  ['Accounts', 'accounts', () => void (location.hash = '#/accounts')],
  ['Stats', 'stats', () => void (location.hash = '#/stats')],
  ['Settings', 'settings', () => void (location.hash = '#/settings')],
];

export function Palette({ onClose }: { onClose: () => void }) {
  const [q, setQ] = useState('');
  const [at, setAt] = useState(0);
  const agentData = useAgents().data?.agents;
  const taskData = useTasks({}).data?.items;
  const hits = useMemo(() => {
    const needle = q.trim().toLowerCase().replace(/^#/, '');
    const agents = agentData ?? [];
    const tasks = taskData ?? [];
    const all: Hit[] = [
      ...PAGES.map(([label, icon, run]): Hit => ({ key: `p:${label}`, icon, label, hint: 'page', run })),
      ...agents.map((a): Hit => ({ key: `a:${a.name}`, icon: 'agent', label: a.name, hint: a.state, run: () => goAgent(a.name) })),
      ...tasks.map((t): Hit => ({ key: `t:${t.id}`, icon: 'tasks', label: `#${t.id} ${t.title}`, hint: t.to, run: () => goTasks(t.id) })),
    ];
    return (needle ? all.filter((h) => h.label.toLowerCase().includes(needle)) : all.filter((h) => h.key.startsWith('p:'))).slice(0, 12);
  }, [q, agentData, taskData]);
  const go = (h: Hit | undefined) => {
    if (!h) return;
    onClose();
    h.run();
  };
  const cur = Math.min(at, hits.length - 1);
  return (
    <Backdrop onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="palette w-[min(560px,calc(100vw-2*var(--sp-6)))] overflow-hidden rounded-dialog border border-line-strong bg-panel shadow-[var(--shadow-pop)]"
        role="dialog"
        aria-modal="true"
        aria-label="Search everything"
      >
        <div className="palette-box flex items-center gap-2 border-b border-line px-3">
          <Icon name="search" size={16} aria-hidden />
          <input
            autoFocus
            className="h-9 flex-1 border-0 bg-transparent outline-none"
            aria-label="Search agents, tasks and pages"
            placeholder="Agents, tasks, pages…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setAt(0);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') onClose();
              else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                setAt((a) => (hits.length ? (a + (e.key === 'ArrowDown' ? 1 : hits.length - 1)) % hits.length : 0));
              } else if (e.key === 'Enter') go(hits[cur]);
            }}
          />
        </div>
        <ul role="listbox" aria-label="Results" className="m-0 max-h-80 list-none overflow-auto py-1">
          {hits.map((h, i) => (
            <li key={h.key} role="option" aria-selected={i === cur}>
              <button
                type="button"
                className={`ui-prow${i === cur ? ' bg-raised shadow-[inset_2px_0_0_var(--ink)]' : ''}`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => go(h)}
              >
                <Icon name={h.icon} size={14} aria-hidden />
                <span className="ui-prow-name">{h.label}</span>
                {h.hint ? <span className="ui-prow-hint">{h.hint}</span> : null}
              </button>
            </li>
          ))}
          {!hits.length ? <li className="palette-none muted">–</li> : null}
        </ul>
      </div>
    </Backdrop>
  );
}
