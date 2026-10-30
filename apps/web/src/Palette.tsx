// The ⌘K palette (G14): one box for agents, tasks by number or title, the pages and a few actions. Arrows move, Enter goes, Escape closes.
import { Fragment, useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { dateTime, searchSnippet, taskRef } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useAgents, useChatSearch, useServes, useTasks } from '@handline/app-data';
import { canCapture, requestCapture } from '#src/Capture.tsx';
import { goAgent, goMessage, goNew, goTasks, goTeam } from '#src/route.ts';
import { COMMANDS, runCommand } from '#src/commands.ts';
import { recents } from '#src/recent.ts';
import { Backdrop, Icon, PROW, PROW_HINT, PROW_NAME, useOpenMotion, type IconName } from '#src/ui/index.ts';

interface Hit {
  key: string;
  /** The heading it sits under while the box is empty. */
  group?: string;
  /** The keys that do the same, shown at the row's end. */
  keys?: string;
  icon: IconName;
  label: string;
  hint?: string;
  run: () => void;
}

const PAGES: [string, IconName, () => void][] = [
  ['Requests', 'inbox', () => void (location.hash = '#/requests')],
  ['Tasks', 'tasks', () => goTasks()],
  ['Team', 'team', () => goTeam()],
  ['New agent', 'plus', () => goNew()],
  ['HR', 'team', () => goTeam('hr')],
  ['Usage', 'accounts', () => void (location.hash = '#/stats')],
  ['Settings', 'settings', () => void (location.hash = '#/settings')],
  ['Machine', 'settings', () => void (location.hash = '#/machine')],
];

/** What the empty box lists, by heading: the agents and tasks opened last, then the commands with their keys and the pages. Another heading's rows (what waits on the person) go in front of these. */
function emptyState(all: Hit[], agents: AgentView[], tasks: { id: number }[]): Hit[] {
  const byKey = new Map(all.map((h) => [h.key, h]));
  const recent = recents()
    .map((r) => byKey.get(r.kind === 'agent' ? `a:${r.id}` : `t:${r.id}`))
    .filter((h): h is Hit => !!h && (agents.length > 0 || tasks.length > 0))
    .slice(0, 5)
    .map((h) => ({ ...h, key: `r:${h.key}`, group: 'Recent' }));
  const commands = COMMANDS.map((c): Hit => ({
    key: `c:${c.name}`,
    group: 'Commands',
    icon: c.icon,
    label: c.label,
    keys: c.keys,
    run: () => runCommand(c.name),
  }));
  const pages = all.filter((h) => h.key.startsWith('p:')).map((h) => ({ ...h, group: 'Pages' }));
  return [...recent, ...commands, ...pages];
}

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
      ...(canCapture()
        ? [{ key: 'c:capture', icon: 'select', label: 'Capture a window', hint: 'action', run: requestCapture } satisfies Hit]
        : []),
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
        label: searchSnippet(m.body, messageQuery),
        hint: `${agent} · ${dateTime(m.at)}`,
        run: () => goMessage(agent, m.id),
      };
    });
    if (!needle) return emptyState(all, agents, tasks);
    return [...all.filter((h) => h.label.toLowerCase().includes(needle)).slice(0, 12), ...messages];
  }, [q, agentData, taskData, found, messageQuery]);
  const go = (h: Hit | undefined) => {
    if (!h) return;
    onClose();
    h.run();
  };
  const cur = Math.min(at, hits.length - 1);
  const open = useOpenMotion();
  return (
    <Backdrop onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <motion.div
        {...open}
        className="palette w-[min(560px,calc(100vw-2*var(--sp-4)))] overflow-hidden rounded-lg border border-line-strong bg-panel shadow-[var(--shadow)]"
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
            <Fragment key={h.key}>
              {h.group && h.group !== hits[i - 1]?.group ? (
                <li role="presentation" className="px-3 pt-2 pb-0.5 text-xs font-medium tracking-wide text-ink-3 uppercase">
                  {h.group}
                </li>
              ) : null}
              <li role="option" aria-selected={i === cur}>
                <button
                  type="button"
                  className={`${PROW}${i === cur ? ' [background:var(--bg-3)] shadow-[inset_2px_0_0_var(--ink)]' : ''}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => go(h)}
                >
                  <Icon name={h.icon} size={14} aria-hidden />
                  <span className={PROW_NAME}>{h.label}</span>
                  {h.keys ? <kbd className="ui-keys flex-none font-sans text-xs text-ink-3">{h.keys}</kbd> : null}
                  {h.hint ? <span className={PROW_HINT}>{h.hint}</span> : null}
                </button>
              </li>
            </Fragment>
          ))}
          {!hits.length ? <li className="palette-none muted">–</li> : null}
        </ul>
      </motion.div>
    </Backdrop>
  );
}
