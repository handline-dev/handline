// The one renderer of free text: openable paths, T<N> as a link to the task (plain click: this pane's Tasks tab; ctrl, cmd or middle click: a tab
// of its own; the Tasks page where there are no panes), and #N as a link to the pull request of the agent's repo when it has one of that number;
// any other #N stays plain text.
import type { MouseEvent, ReactNode } from 'react';
import { findImageRefs, findRefs, openTask, openTaskTab, taskRef } from '@handline/app-core';
import { AttachmentImage } from '#src/AttachmentImage.tsx';
import { PathText } from '#src/PathText.tsx';
import { WebLink, findUrls } from '#src/WebLink.tsx';
import { goTasks } from '#src/route.ts';
import { useRefsData, useSharedRefs, type RefsData } from '#src/RefsContext.tsx';

const LINK = 'ref border-0 bg-transparent p-0 font-mono text-[12px] leading-4 text-link underline cursor-pointer';

export function RichText({ text, agent }: { text: string; agent?: string }) {
  const images = findImageRefs(text);
  if (!images.length) return <Linked text={text} agent={agent} />;
  const parts: ReactNode[] = [];
  let at = 0;
  // Blank space between two pictures would break their row in a pre-wrapped text, so it is left out.
  const words = (from: number, to: number) => text.slice(from, to).trim() !== '';
  for (const i of images) {
    if (i.start > at && words(at, i.start)) parts.push(<Linked key={`t${at}`} text={text.slice(at, i.start)} agent={agent} />);
    parts.push(<AttachmentImage key={i.start} id={i.id} name={i.caption} />);
    at = i.end;
  }
  if (at < text.length && words(at, text.length)) parts.push(<Linked key={`t${at}`} text={text.slice(at)} agent={agent} />);
  return <>{parts}</>;
}

/** Web addresses are links that ask where to open; the text between them goes on to the paths and numbers. */
function Linked({ text, agent }: { text: string; agent?: string }) {
  const urls = findUrls(text);
  if (!urls.length) return <Refs text={text} agent={agent} />;
  const parts: ReactNode[] = [];
  let at = 0;
  for (const u of urls) {
    if (u.start > at) parts.push(<Refs key={`t${at}`} text={text.slice(at, u.start)} agent={agent} />);
    parts.push(<WebLink key={u.start} url={text.slice(u.start, u.end)} />);
    at = u.end;
  }
  if (at < text.length) parts.push(<Refs key={`t${at}`} text={text.slice(at)} agent={agent} />);
  return <>{parts}</>;
}

/** The chat gives its reads once; a text outside a chat reads its own. */
function Refs({ text, agent }: { text: string; agent?: string }) {
  const shared = useSharedRefs();
  return shared ? <RefsView data={shared} text={text} agent={agent} /> : <OwnRefs text={text} agent={agent} />;
}

function OwnRefs({ text, agent }: { text: string; agent?: string }) {
  return <RefsView data={useRefsData(agent, text)} text={text} agent={agent} />;
}

function RefsView({ data, text, agent }: { data: RefsData; text: string; agent?: string }) {
  const { ws, known, names, pr, repo } = data;
  // A #N is a pull request of the agent's repo when that repo has one of that number; else it is plain text.
  const refs = findRefs(text, { task: known, pr });
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
          href={`https://github.com/${repo}/pull/${r.id}`}
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
