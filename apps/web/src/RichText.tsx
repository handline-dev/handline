// The one renderer of free text: openable paths, T<N> as a link to the task (plain click: this pane's Tasks tab; ctrl, cmd or middle click: a tab
// of its own; the Tasks page where there are no panes), and #N as a link to the pull request of the agent's repo when it has one of that number;
// any other #N stays plain text.
import type { MouseEvent, ReactNode } from 'react';
import { findImageRefs, findRefs, openTask, openTaskTab, taskRef } from '@handline/app-core';
import { useAgents, usePullRequests, useTasks } from '@handline/app-data';
import { AttachmentImage } from '#src/AttachmentImage.tsx';
import { PathText } from '#src/PathText.tsx';
import { goTasks } from '#src/route.ts';
import { useWorkspaceIfAny } from '#src/Workspace.tsx';

const LINK = 'ref border-0 bg-transparent p-0 font-mono text-[12px] leading-4 text-link underline cursor-pointer';

/** Which task numbers exist: those up to the highest the task list holds (numbers run on from 1). Nothing is a task until the list is in. */
function useKnownTask(): (id: number) => boolean {
  const top = Math.max(0, ...(useTasks({ all: true, limit: 200 }).data?.items ?? []).map((t) => t.id));
  return (id) => id <= top;
}

export function RichText({ text, agent }: { text: string; agent?: string }) {
  const images = findImageRefs(text);
  if (!images.length) return <Refs text={text} agent={agent} />;
  const parts: ReactNode[] = [];
  let at = 0;
  for (const i of images) {
    if (i.start > at) parts.push(<Refs key={`t${at}`} text={text.slice(at, i.start)} agent={agent} />);
    parts.push(<AttachmentImage key={i.start} id={i.id} name={i.caption} />);
    at = i.end;
  }
  if (at < text.length) parts.push(<Refs key={`t${at}`} text={text.slice(at)} agent={agent} />);
  return <>{parts}</>;
}

function Refs({ text, agent }: { text: string; agent?: string }) {
  const ws = useWorkspaceIfAny();
  const known = useKnownTask();
  const names = new Set((useAgents().data?.agents ?? []).map((a) => a.name));
  // A #N is a pull request of the agent's repo when that repo has one of that number; else it is plain text.
  const prs = usePullRequests(agent, text);
  const refs = findRefs(text, { task: known, pr: prs.repo ? prs.isPr : undefined });
  if (!refs.length) return <PathText text={text} agent={agent} names={names} />;
  const open = (id: number, own: boolean) => {
    if (!ws) goTasks(id);
    else ws.apply((w) => (own ? openTaskTab(w, id) : openTask(w, id)));
  };
  const parts: ReactNode[] = [];
  let at = 0;
  for (const r of refs) {
    if (r.start > at) parts.push(<PathText key={`t${at}`} text={text.slice(at, r.start)} agent={agent} names={names} whole={text} />);
    const shown = text.slice(r.start, r.end);
    parts.push(
      r.kind === 'pr' ? (
        <a
          key={r.start}
          className={LINK}
          data-pr={r.id}
          href={`https://github.com/${prs.repo}/pull/${r.id}`}
          target="_blank"
          rel="noopener noreferrer"
          title={`Open pull request ${shown} on GitHub`}
        >
          {shown}
        </a>
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
  if (at < text.length) parts.push(<PathText key={`t${at}`} text={text.slice(at)} agent={agent} names={names} whole={text} />);
  return <>{parts}</>;
}
