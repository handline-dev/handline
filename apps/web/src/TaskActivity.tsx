// The Activity of a task, one list: its comments and its events in words (the last three; older ones fold), and the composer under them.
// `@agent` in a comment tells that agent; the assignee is told by default (the daemon's routing). Images go in as in the description.
import { useState, type RefObject } from 'react';
import { activityOf, ago, dateTime, foldEvents, involved, mentionState, type ActivityItem } from '@handline/app-core';
import type { TaskDetail as Detail } from '@handline/client';
import { classify, sentence, useAgents, useOff, useWrite } from '@handline/app-data';
import { useHello, useMe } from '@handline/app-react';
import { BodyEditor } from '#src/BodyEditor.tsx';
import { CommentPicture } from '#src/CommentPicture.tsx';
import { MemoFiles } from '#src/MemoFiles.tsx';
import { Markdown } from '#src/Markdown.tsx';
import { AgentLink } from '#src/TaskInline.tsx';
import { Sheet, useCan } from '#src/taskui.tsx';
import { Button } from '#src/ui/index.ts';
import { Reveal } from '#src/ui/Reveal.tsx';

/** One comment. Its author, and the founder for any, can Edit and Delete: both show at once and roll back with the daemon's reason; Delete asks first. */
function Comment({ t, item, onReply }: { t: Detail; item: ActivityItem; onReply?: (memo: number) => void }) {
  const m = t.memos.find((x) => x.id === item.memo);
  const of = m?.reply_to != null ? t.memos.find((x) => x.id === m.reply_to) : undefined; // the comment this one answers
  const me = useMe();
  const hello = useHello();
  const edit = useWrite('task.comment.edit', String(t.id));
  const del = useWrite('task.comment.delete', String(t.id));
  const offEdit = useOff('task.comment.edit');
  const offDelete = useOff('task.comment.delete');
  const [ask, setAsk] = useState<null | 'edit' | 'delete'>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const mine = !!m && (m.author === me || hello?.role === 'founder');
  const close = () => (setAsk(null), edit.reset(), del.reset());
  return (
    <li className="comment grid min-w-0 gap-0.5 rounded-card border border-line px-3 py-2">
      <span className="text-hint text-ink-3">
        <strong className="font-medium text-ink-2">
          <AgentLink name={item.who} />
        </strong>{' '}
        {ago(item.at)}
        {item.edited ? ' · edited' : ''}
      </span>
      {of ? <span className="truncate text-hint text-ink-3" data-reply-to={of.id}>{`↩ ${of.author}: ${firstLine(of.text)}`}</span> : null}
      <div className="min-w-0">
        <Markdown text={item.note} />
      </div>
      {m?.files?.length ? <MemoFiles files={m.files} /> : null}
      {m && onReply && m.id > 0 ? (
        <div className="flex">
          <Button variant="outline" label="Reply" onClick={() => onReply(m.id)} />
        </div>
      ) : null}
      {mine && ask === null ? (
        <div className="flex flex-wrap items-center gap-2" title={offEdit || offDelete}>
          <Button variant="outline" label="Edit" disabled={m.id < 0 || !!offEdit} onClick={() => (setText(m.text), setAsk('edit'))} />
          <Button variant="outline" label="Delete" disabled={m.id < 0 || !!offDelete} onClick={() => setAsk('delete')} />
        </div>
      ) : null}
      {m && ask === 'edit' ? (
        <Sheet
          label="Edit comment"
          submit="Save"
          busy={edit.isPending}
          onCancel={close}
          onSubmit={() => text.trim() && !busy && edit.mutate({ task: t.id, memo: m.id, text: text.trim() }, { onSuccess: close })}
        >
          <BodyEditor value={text} onChange={setText} onBusy={setBusy} label="Comment" />
          {!text.trim() ? (
            <span role="alert">A comment needs text.</span>
          ) : edit.isError ? (
            <span role="alert">{sentence(classify(edit.error))}</span>
          ) : null}
        </Sheet>
      ) : null}
      {m && ask === 'delete' ? (
        <Sheet
          label="Delete this comment?"
          submit="Delete"
          busy={del.isPending}
          onCancel={close}
          onSubmit={() => del.mutate({ task: t.id, memo: m.id }, { onSuccess: close })}
        >
          {del.isError ? <span role="alert">{sentence(classify(del.error))}</span> : null}
        </Sheet>
      ) : null}
    </li>
  );
}

const firstLine = (text: string): string =>
  text
    .split('\n')
    .find((l) => l.trim())
    ?.trim()
    .slice(0, 90) ?? '';

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
          <Reveal show={open}>
            <div className="mt-1 min-w-0 rounded-card bg-raised p-2 text-ink">
              <Markdown text={item.note} />
            </div>
          </Reveal>
        </>
      ) : null}
    </li>
  );
}

export function Activity({ t, box }: { t: Detail; box: RefObject<HTMLDivElement | null> }) {
  const can = useCan('task.comment.add');
  const add = useWrite('task.comment.add', String(t.id));
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState(0); // a new editor after a send: its content is the box's own
  const [all, setAll] = useState(false);
  const [replyTo, setReplyTo] = useState<number | null>(null);
  const answering = replyTo === null ? undefined : t.memos.find((x) => x.id === replyTo);
  const { items, hidden } = foldEvents(activityOf(t.memos, t.timeline), all);
  const agents = useAgents().data?.agents ?? [];
  const names = agents.map((a) => a.name);
  const stateOf = (n: string) => mentionState(agents.find((a) => a.name === n) ?? { state: 'idle' });
  const near = involved(t);
  const send = () => {
    if (!text.trim() || busy || add.isPending) return;
    add.mutate(
      { task: t.id, text: text.trim(), ...(answering ? { reply: answering.id } : {}) },
      { onSuccess: () => (setText(''), setFresh((n) => n + 1), setReplyTo(null)) },
    );
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
            i.kind === 'comment' ? (
              <Comment key={`${n}-${i.memo}`} t={t} item={i} onReply={can.ok ? setReplyTo : undefined} />
            ) : (
              <Event key={`${n}-${i.line}`} item={i} />
            ),
          )}
        </ul>
      ) : null}
      <div ref={box} className="grid gap-2" title={can.why}>
        {can.ok ? (
          <>
            {answering ? (
              <p className="m-0 flex items-center gap-2 text-hint text-ink-2" data-replying={answering.id}>
                <span className="min-w-0 flex-1 truncate">{`Replying to ${answering.author}: ${firstLine(answering.text)}`}</span>
                <Button variant="outline" label="Cancel reply" onClick={() => setReplyTo(null)} />
              </p>
            ) : null}
            <BodyEditor
              key={fresh}
              value={text}
              onChange={setText}
              onBusy={setBusy}
              label="Comment"
              onSubmit={send}
              mentions={{ names, near, state: stateOf }}
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="solid" icon="send" label="Comment" keys="⌘↵" disabled={!text.trim() || busy || add.isPending} onClick={send}>
                {add.isPending ? 'Sending…' : 'Comment'}
              </Button>
              <CommentPicture onAttach={(md) => (setText((v) => [v.trim(), md].filter(Boolean).join('\n')), setFresh((n) => n + 1))} />
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
