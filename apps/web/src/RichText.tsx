// The one renderer of free text: openable paths, T<N> as a link to the task (plain click: this pane's Tasks tab; ctrl, cmd or middle click: a tab
// of its own; the Tasks page where there are no panes), and #N as a link to that message where the caller has messages.
import type { MouseEvent, ReactNode } from 'react';
import { findRefs, openTask, openTaskTab, taskRef } from '@handline/app-core';
import { useTasks } from '@handline/app-data';
import { PathText } from './PathText.tsx';
import { goTasks } from './route.ts';
import { useWorkspaceIfAny } from './Workspace.tsx';

const LINK = 'ref border-0 bg-transparent p-0 font-mono text-[12px] leading-4 text-link underline cursor-pointer';

/** Which task numbers exist: those up to the highest the task list holds (numbers run on from 1). Nothing is a task until the list is in. */
function useKnownTask(): (id: number) => boolean {
  const top = Math.max(0, ...(useTasks({ all: true, limit: 200 }).data?.items ?? []).map((t) => t.id));
  return (id) => id <= top;
}

export function RichText({ text, agent, onRef }: { text: string; agent?: string; onRef?: (id: number) => void }) {
  const ws = useWorkspaceIfAny();
  const known = useKnownTask();
  const refs = findRefs(text, { messages: !!onRef, task: known });
  if (!refs.length) return <PathText text={text} agent={agent} />;
  const open = (id: number, own: boolean) => {
    if (!ws) goTasks(id);
    else ws.apply((w) => (own ? openTaskTab(w, id) : openTask(w, id)));
  };
  const parts: ReactNode[] = [];
  let at = 0;
  for (const r of refs) {
    if (r.start > at) parts.push(<PathText key={`t${at}`} text={text.slice(at, r.start)} agent={agent} />);
    const shown = text.slice(r.start, r.end);
    parts.push(
      r.kind === 'message' ? (
        <button key={r.start} type="button" className={LINK} title={`Go to ${shown}`} onClick={() => onRef!(r.id)}>
          {shown}
        </button>
      ) : (
        <button
          key={r.start}
          type="button"
          className={LINK}
          data-task={r.id}
          title={`Open ${taskRef(r.id)} (ctrl or middle click: in a new tab)`}
          onClick={(e: MouseEvent) => open(r.id, e.ctrlKey || e.metaKey)}
          onAuxClick={(e: MouseEvent) => e.button === 1 && open(r.id, true)}
        >
          {shown}
        </button>
      ),
    );
    at = r.end;
  }
  if (at < text.length) parts.push(<PathText key={`t${at}`} text={text.slice(at)} agent={agent} />);
  return <>{parts}</>;
}
