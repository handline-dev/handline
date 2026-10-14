// The ⌘K palette (G14): one box for agents, tasks by number or title, the pages and a few actions. Arrows move, Enter goes, Escape closes.
import { useEffect, useMemo, useState } from 'react';
import { taskRef } from '@handline/app-core';
import { useAgents, useChatSearch, useServes, useTasks } from '@handline/app-data';
import { snippet, when } from './ChatSearch.tsx';
import { goAgent, goInbox, goMessage, goNew, goTasks, goTeam, goWorktrees } from './route.ts';
import { Backdrop, Icon, PROW, PROW_HINT, PROW_NAME, type IconName } from './ui/index.ts';

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
  ['Worktrees', 'worktrees', () => goWorktrees()],
  ['New agent', 'plus', () => goNew()],
  ['HR', 'team', () => goTeam('hr')],
  ['Accounts', 'accounts', () => void (location.hash = '#/accounts')],
  ['Stats', 'stats', () => void (location.hash = '#/stats')],
  ['Settings', 'settings', () => void (location.hash = '#/settings')],
];

export function Palette({ onClose }: { onClose: () => void }) {
  const [q, setQ] = useState('');
  const [at, setAt] = useState(0);
  const agentData = useAgents().data?.agents;
  const taskData = useTasks({}).data?.items;
  const { served } = useServes('chat.search');
  const [typed, setTyped] = useState(''); // the box once the person stops typing
  useEffect(() => {
    const t = setTimeout(() => setTyped(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);
  const messageQuery = typed.length >= 2 && typed === q.trim() ? typed : '';
  const found = useChatSearch({ query: messageQuery, scope: 'all', limit: 5 }, served && messageQuery !== '').data?.messages;
  const hits = useMemo(() => {
    const needle = q.trim().toLowerCase().replace(/^#/, '');
    const agents = agentData ?? [];
    const tasks = taskData ?? [];
    const all: Hit[] = [
      ...PAGES.map(([label, icon, run]): Hit => ({ key: `p:${label}`, icon, label, hint: 'page', run })),
      ...agents.map((a): Hit => ({ key: `a:${a.name}`, icon: 'agent', label: a.name, hint: a.state, run: () => goAgent(a.name) })),
      ...tasks.map((t): Hit => ({
        key: `t:${t.id}`,
        icon: 'tasks',
        label: `${taskRef(t.id)} ${t.title}`,
        hint: t.to,
        run: () => goTasks(t.id),
      })),
    ];
    const names = new Set(agents.map((a) => a.name));
    const messages = (messageQuery ? (found ?? []) : []).map((m): Hit => {
      const agent = names.has(m.from) ? m.from : m.to;
      return {
        key: `m:${m.id}`,
        icon: 'chat',
        label: snippet(m.body, messageQuery),
        hint: `${agent} · ${when(m.at)}`,
        run: () => goMessage(agent, m.id),
      };
    });
    const main = needle ? all.filter((h) => h.label.toLowerCase().includes(needle)) : all.filter((h) => h.key.startsWith('p:'));
    return [...main.slice(0, 12), ...messages];
  }, [q, agentData, taskData, found, messageQuery]);
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
            aria-label="Search agents, tasks, pages and messages"
            placeholder="Agents, tasks, pages, messages…"
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
                className={`${PROW}${i === cur ? ' [background:var(--bg-3)] shadow-[inset_2px_0_0_var(--ink)]' : ''}`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => go(h)}
              >
                <Icon name={h.icon} size={14} aria-hidden />
                <span className={PROW_NAME}>{h.label}</span>
                {h.hint ? <span className={PROW_HINT}>{h.hint}</span> : null}
              </button>
            </li>
          ))}
          {!hits.length ? <li className="palette-none muted">–</li> : null}
        </ul>
      </div>
    </Backdrop>
  );
}
