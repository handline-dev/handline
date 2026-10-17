// The Activity of a task, one list: its comments and its events in words (the last three; older ones fold), and the composer under them.
// `@agent` in a comment tells that agent; the assignee is told by default (the daemon's routing). Images go in as in the description.
import { useState, type RefObject } from 'react';
import { activityOf, ago, dateTime, foldEvents, type ActivityItem } from '@handline/app-core';
import type { TaskDetail as Detail } from '@handline/client';
import { classify, sentence, useWrite } from '@handline/app-data';
import { BodyEditor } from './BodyEditor.tsx';
import { MemoFiles } from './MemoFiles.tsx';
import { Markdown } from './Markdown.tsx';
import { AgentLink } from './TaskInline.tsx';
import { useCan } from './taskui.tsx';
import { Button } from './ui/index.ts';

function Comment({ t, item }: { t: Detail; item: ActivityItem }) {
  const m = t.memos.find((x) => x.id === item.memo);
  return (
    <li className="comment grid min-w-0 gap-0.5 rounded-card border border-line px-3 py-2">
      <span className="text-hint text-ink-3">
        <strong className="font-medium text-ink-2">
          <AgentLink name={item.who} />
        </strong>{' '}
        {ago(item.at)}
        {item.edited ? ' · edited' : ''}
      </span>
      <div className="min-w-0">
        <Markdown text={item.note} />
      </div>
      {m?.files?.length ? <MemoFiles files={m.files} /> : null}
    </li>
  );
}

function Event({ item }: { item: ActivityItem }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="tevent min-w-0 text-ink-2">
      <span className="mr-2 inline-block h-2 w-2 rounded-pill bg-ink-3 align-middle" aria-hidden="true" />
      {item.line} <span className="text-hint text-ink-3">· {dateTime(item.at)}</span>
      {item.note ? (
        <>
          {' '}
          <button type="button" className="border-0 bg-transparent p-0 text-link" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? 'Hide note' : 'Read note'}
          </button>
          {open ? (
            <div className="mt-1 min-w-0 rounded-card bg-raised p-2 text-ink">
              <Markdown text={item.note} />
            </div>
          ) : null}
        </>
      ) : null}
    </li>
  );
}

export function Activity({ t, box }: { t: Detail; box: RefObject<HTMLDivElement | null> }) {
  const can = useCan('task.memo.add');
  const add = useWrite('task.memo.add', String(t.id));
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState(0); // a new editor after a send: its content is the box's own
  const [all, setAll] = useState(false);
  const { items, hidden } = foldEvents(activityOf(t.memos, t.timeline), all);
  const send = () => {
    if (!text.trim() || busy || add.isPending) return;
    add.mutate({ task: t.id, text: text.trim() }, { onSuccess: () => (setText(''), setFresh((n) => n + 1)) });
  };
  return (
    <section className="mt-4 grid min-w-0 grid-cols-[minmax(0,1fr)] gap-2" aria-label="Activity">
      <h3 className="m-0 text-ink-3">Activity</h3>
      {hidden ? (
        <button type="button" className="justify-self-start border-0 bg-transparent p-0 text-link" onClick={() => setAll(true)}>
          Show {hidden} earlier {hidden === 1 ? 'event' : 'events'}
        </button>
      ) : null}
      {items.length ? (
        <ul className="tmemos m-0 grid min-w-0 grid-cols-[minmax(0,1fr)] list-none gap-2 p-0">
          {items.map((i, n) =>
            i.kind === 'comment' ? <Comment key={`${n}-${i.memo}`} t={t} item={i} /> : <Event key={`${n}-${i.line}`} item={i} />,
          )}
        </ul>
      ) : null}
      <div ref={box} className="grid gap-2" title={can.why}>
        {can.ok ? (
          <>
            <BodyEditor key={fresh} value={text} onChange={setText} onBusy={setBusy} label="Comment" />
            <div className="flex items-center gap-2">
              <Button variant="solid" icon="send" label="Comment" disabled={!text.trim() || busy || add.isPending} onClick={send}>
                {add.isPending ? 'Sending…' : 'Comment'}
              </Button>
              <span className="text-hint text-ink-3">@agent asks that agent. The assignee is told either way.</span>
            </div>
          </>
        ) : null}
        {add.isError ? (
          <p className="muted m-0" role="alert">
            {sentence(classify(add.error))}
          </p>
        ) : null}
      </div>
    </section>
  );
}
