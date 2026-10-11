// The two menus of the message box (docs/bridge-feature-tree.md §2.4): `/` lists the agent's verbs and `#` a task or a message to refer to,
// and Ctrl+R lists what was sent before. A menu is entries; the box decides which one is open from its text.
import { useMemo } from 'react';
import { openTab, verbsOf, type Verb } from '@handline/app-core';
import type { AgentDetail, ChatMessage } from '@handline/client';
import { useAgents, useServes, useTasks } from '@handline/app-data';
import type { IconName } from './ui/index.ts';
import { useWorkspace } from './Workspace.tsx';

export type MenuKind = 'slash' | 'hash' | 'history';
export interface Entry {
  key: string;
  icon: IconName;
  label: string;
  hint?: string;
  /** What choosing it does to the text: the box replaces the part the menu was opened on. */
  insert?: string;
  verb?: Verb;
  terminal?: boolean;
}

const VERB_ICON: Record<string, IconName> = {
  wake: 'working',
  restart: 'retry',
  restart_fresh: 'retry',
  close: 'close',
  park: 'idle',
  fold: 'less',
  move: 'accounts',
  clear_stall: 'blocked',
};

/** Which menu the text asks for: a line that is only `/word`, a `#` at its end, or none. */
export function menuOf(text: string): { kind: MenuKind; query: string } | null {
  const slash = /^\/(\w*)$/.exec(text);
  if (slash) return { kind: 'slash', query: slash[1]! };
  const hash = /(?:^|\s)#(\w*)$/.exec(text);
  if (hash) return { kind: 'hash', query: hash[1]! };
  return null;
}

export function useEntries(
  agent: string,
  detail: AgentDetail,
  kind: MenuKind | null,
  query: string,
  messages: ChatMessage[],
  history: string[],
): Entry[] {
  const { served } = useServes('tasks.list');
  const tasks = useTasks({});
  const view = (useAgents().data?.agents ?? []).find((a) => a.name === agent);
  return useMemo(() => {
    const q = query.toLowerCase();
    if (kind === 'slash') {
      const verbs = view ? verbsOf(view, detail) : [];
      const all: Entry[] = [
        ...verbs.map((v): Entry => ({
          key: v.id,
          icon: VERB_ICON[v.id] ?? 'menu',
          label: v.label,
          hint: `/${v.id.replace('_fresh', ' fresh')}`,
          verb: v,
        })),
        ...(detail.structured
          ? [{ key: 'transcript', icon: 'terminal', label: 'Transcript', hint: '/transcript', terminal: true } as Entry]
          : detail.terminal
            ? [{ key: 'terminal', icon: 'terminal', label: 'Terminal', hint: '/terminal', terminal: true } as Entry]
            : []),
      ];
      return all.filter((e) => !q || e.key.startsWith(q) || e.label.toLowerCase().startsWith(q));
    }
    if (kind === 'hash') {
      const mine = (served ? (tasks.data?.items ?? []) : [])
        .filter((t) => t.to === agent)
        .map((t): Entry => ({ key: `t${t.id}`, icon: 'tasks', label: `#${t.id}`, hint: t.title, insert: `#${t.id} ` }));
      const said = [...messages]
        .reverse()
        .slice(0, 8)
        .map((m): Entry => ({ key: `m${m.id}`, icon: 'reference', label: `#${m.id}`, hint: m.body.slice(0, 50), insert: `#${m.id} ` }));
      return [...mine, ...said].filter((e) => !q || e.label.slice(1).startsWith(q));
    }
    if (kind === 'history') {
      const hits = history.filter((h) => h.toLowerCase().includes(q));
      return hits
        .slice(0, 12)
        .map((h, i): Entry => ({ key: `h${i}`, icon: 'typed', label: h.length > 70 ? `${h.slice(0, 70)}…` : h, insert: h }));
    }
    return [];
  }, [kind, query, view, detail, served, tasks.data, agent, messages, history]);
}

/** Opens the agent's terminal (a structured agent's transcript) beside the chat, as the `/terminal` and `/transcript` verbs do. */
export function useOpenTerminal(detail: AgentDetail): () => void {
  const { apply } = useWorkspace();
  return () =>
    detail.structured
      ? apply((w) => openTab(w, 'transcript'))
      : detail.terminal && apply((w) => openTab(w, 'terminal', { handle: detail.terminal! }));
}
