// The two menus of the message box (docs/bridge-feature-tree.md §2.4): `/` lists what a person does in chat and `#` a task or a message to refer to,
// and Ctrl+R lists what was sent before. A menu is entries; the box decides which one is open from its text.
import { useMemo } from 'react';
import { openTab, verbsOf, type Verb, taskRef } from '@handline/app-core';
import type { AgentDetail, ChatMessage } from '@handline/client';
import { useAgents, useChatRefs, useServes, useTasks } from '@handline/app-data';
import type { IconName } from '#src/ui/index.ts';
import { useWorkspace } from '#src/Workspace.tsx';

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

const VERB_ICON: Record<string, IconName> = { interrupt: 'cancel', clear_stall: 'blocked' };

/** The verbs the slash menu offers: the ones a person reaches for. Restart, close, park, fold and move are automatic and are not shown. */
const SLASH_VERBS: ReadonlySet<string> = new Set(['interrupt', 'clear_stall']);

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
  const refsServed = useServes('chat.refs').served;
  const refs = useChatRefs(agent, kind === 'hash' && refsServed);
  const view = (useAgents().data?.agents ?? []).find((a) => a.name === agent);
  return useMemo(() => {
    const q = query.toLowerCase();
    if (kind === 'slash') {
      const verbs = (view ? verbsOf(view, detail) : []).filter((v) => SLASH_VERBS.has(v.id));
      const all: Entry[] = [
        ...verbs.map((v): Entry => ({
          key: v.id,
          icon: VERB_ICON[v.id] ?? 'menu',
          label: v.label,
          hint: `/${v.id}`,
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
        .map((t): Entry => ({ key: `t${t.id}`, icon: 'tasks', label: `${taskRef(t.id)}`, hint: t.title, insert: `${taskRef(t.id)} ` }));
      // The loaded messages show at once; chat.refs adds the requests the loaded page does not hold.
      const rows = new Map<number, string>();
      for (const m of [...messages].reverse().slice(0, 8)) rows.set(m.id, m.body.slice(0, 50));
      for (const r of refs.data?.requests ?? []) if (!rows.has(r.request)) rows.set(r.request, r.line);
      const said = [...rows]
        .sort((x, y) => y[0] - x[0])
        .map(([id, line]): Entry => ({ key: `m${id}`, icon: 'reference', label: `#${id}`, hint: line, insert: `#${id} ` }));
      return [...mine, ...said].filter((e) => !q || e.label.slice(1).startsWith(q));
    }
    if (kind === 'history') {
      const hits = history.filter((h) => h.toLowerCase().includes(q));
      return hits
        .slice(0, 12)
        .map((h, i): Entry => ({ key: `h${i}`, icon: 'typed', label: h.length > 70 ? `${h.slice(0, 70)}…` : h, insert: h }));
    }
    return [];
  }, [kind, query, view, detail, served, tasks.data, refs.data, agent, messages, history]);
}

/** Opens the agent's terminal (a structured agent's transcript) beside the chat, as the `/terminal` and `/transcript` verbs do. */
export function useOpenTerminal(detail: AgentDetail): () => void {
  const { apply } = useWorkspace();
  return () =>
    detail.structured
      ? apply((w) => openTab(w, 'transcript'))
      : detail.terminal && apply((w) => openTab(w, 'terminal', { handle: detail.terminal! }));
}
