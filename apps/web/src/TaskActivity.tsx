// The Activity of a task, one list: its comments and its events in words (the last three; older ones fold), and the composer under them.
// `@agent` in a comment tells that agent; the assignee is told by default (the daemon's routing). Images go in as in the description.
import { useEffect, useRef, useState, type RefObject } from 'react';
import { activityOf, ago, dateTime, foldEvents, involved, isPerson, mentionState, noteIsLong, type ActivityItem } from '@handline/app-core';
import type { TaskDetail as Detail } from '@handline/client';
import { classify, sentence, useAgents, useOff, useWrite } from '@handline/app-data';
import { useHello, useMe } from '@handline/app-react';
import { BodyEditor } from '#src/BodyEditor.tsx';
import { CommentReaders } from '#src/CommentReaders.tsx';
import { CommentPicture } from '#src/CommentPicture.tsx';
import { Thumbs } from '#src/AttachmentImage.tsx';
import { MemoFiles } from '#src/MemoFiles.tsx';
import { Markdown } from '#src/Markdown.tsx';
import { AgentLink } from '#src/TaskInline.tsx';
import { useCan } from '#src/taskui.tsx';
import { Button, MenuButton, MenuItem } from '#src/ui/index.ts';
import { Fold } from '#src/Fold.tsx';

export const UNDO_MS = 6000;
/** The icons of a card show on hover and keyboard focus, and always on a touch screen. */
const ON_HOVER =
  'flex items-center opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100';

/**
 * One comment. Its author, and the founder for any, can Edit and Delete from the three-dot menu: both show at once and roll back with the daemon's
 * reason. Delete takes the card out at once and offers Undo for a few seconds before the daemon is asked.
 */
function Comment({
  t,
  item,
  onReply,
  onEditing,
}: {
  t: Detail;
  item: ActivityItem;
  onReply?: (memo: number) => void;
  onEditing: (on: boolean) => void;
}) {
  const m = t.memos.find((x) => x.id === item.memo);
  const of = m?.reply_to != null ? t.memos.find((x) => x.id === m.reply_to) : undefined; // the comment this one answers
  const me = useMe();
  const hello = useHello();
  const edit = useWrite('task.comment.edit', String(t.id));
  const del = useWrite('task.comment.delete', String(t.id));
  const offEdit = useOff('task.comment.edit');
  const offDelete = useOff('task.comment.delete');
  const [ask, setAsk] = useState<null | 'edit'>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [undoing, setUndoing] = useState(false);
  const editing = ask === 'edit';
  useEffect(() => {
    if (!editing) return;
    onEditing(true);
    return () => onEditing(false);
  }, [editing]); // eslint-disable-line react-hooks/exhaustive-deps
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const mine = !!m && (m.author === me || hello?.role === 'founder');
  const close = () => (setAsk(null), edit.reset(), del.reset());
  const send = () => m && del.mutate({ task: t.id, memo: m.id }, { onError: () => setUndoing(false) });
  // A pending delete is sent when the card goes away, or the page is closed, before Undo is pressed.
  const sendRef = useRef(send);
  useEffect(() => void (sendRef.current = send));
  useEffect(() => {
    const flush = () => timer.current && (clearTimeout(timer.current), (timer.current = undefined), sendRef.current());
    addEventListener('pagehide', flush);
    return () => (removeEventListener('pagehide', flush), flush());
  }, []);
  const remove = () => {
    setUndoing(true);
    timer.current = setTimeout(() => ((timer.current = undefined), send()), UNDO_MS);
  };
  const undo = () => (clearTimeout(timer.current), (timer.current = undefined), setUndoing(false));
  if (undoing)
    return (
      <li className="comment flex items-center gap-2 rounded-card border border-line px-3 py-1 text-hint text-ink-3">
        Comment deleted
        <Button variant="ghost" size="sm" icon="undo" label="Undo" onClick={undo}>
          Undo
        </Button>
      </li>
    );
  return (
    <li className="comment group grid min-w-0 gap-0.5 rounded-card border border-line px-3 py-1.5">
      <span className="flex items-center gap-1 text-hint text-ink-3">
        <span className="min-w-0 flex-1 truncate">
          <strong className="font-medium text-ink-2">{isPerson(item.who, me) ? 'You' : <AgentLink name={item.who} />}</strong>{' '}
          {ago(item.at)}
          {item.edited ? ' · edited' : ''}
        </span>
        <span className={ON_HOVER}>
          {m && onReply && m.id > 0 ? <Button size="sm" icon="reply" label="Reply" onClick={() => onReply(m.id)} /> : null}
          {mine && ask === null ? (
            <MenuButton size="sm" icon="ellipsis" label="More" title={offEdit || offDelete}>
              <MenuItem disabled={m.id < 0 || !!offEdit} onSelect={() => (setText(m.text), setAsk('edit'))}>
                Edit
              </MenuItem>
              <MenuItem disabled={m.id < 0 || !!offDelete} onSelect={remove}>
                Delete
              </MenuItem>
            </MenuButton>
          ) : null}
        </span>
      </span>
      {of ? <span className="truncate text-hint text-ink-3" data-reply-to={of.id}>{`↩ ${of.author}: ${firstLine(of.text)}`}</span> : null}
      {m && ask === 'edit' ? (
        <EditInPlace
          text={text}
          setText={setText}
          busy={busy}
          setBusy={setBusy}
          pending={edit.isPending}
          problem={!text.trim() ? 'A comment needs text.' : edit.isError ? sentence(classify(edit.error)) : undefined}
          onCancel={close}
          onSave={() => text.trim() && !busy && edit.mutate({ task: t.id, memo: m.id, text: text.trim() }, { onSuccess: close })}
        />
      ) : (
        <div className="min-w-0">
          <Thumbs.Provider value>
            <Markdown text={item.note} />
          </Thumbs.Provider>
        </div>
      )}
      {m?.files?.length ? <MemoFiles files={m.files} /> : null}
      {m?.readers?.length ? <CommentReaders memo={m.id} readers={m.readers} /> : null}
      {del.isError ? <span role="alert">{sentence(classify(del.error))}</span> : null}
    </li>
  );
}

/** The comment's body turned into one box, as wide as the composer, with Save and Cancel under it. Escape cancels, ⌘↵ saves. */
function EditInPlace(props: {
  text: string;
  setText: (v: string) => void;
  busy: boolean;
  setBusy: (b: boolean) => void;
  pending: boolean;
  problem?: string;
  onCancel: () => void;
  onSave: () => void;
}) {
  const ref = useRef<HTMLFormElement>(null);
  useEffect(() => ref.current?.querySelector<HTMLElement>('.ProseMirror')?.focus(), []);
  return (
    <form
      ref={ref}
      role="group"
      aria-label="Edit comment"
      className="grid min-w-0 gap-1.5"
      onSubmit={(e) => (e.preventDefault(), props.onSave())}
      onKeyDown={(e) => e.key === 'Escape' && !e.defaultPrevented && (e.stopPropagation(), props.onCancel())}
    >
      <BodyEditor
        plain
        compact
        value={props.text}
        onChange={props.setText}
        onBusy={props.setBusy}
        label="Comment"
        onSubmit={props.onSave}
      />
      {props.problem ? <span role="alert">{props.problem}</span> : null}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" label="Cancel" onClick={props.onCancel}>
          Cancel
        </Button>
        <Button variant="solid" size="sm" type="submit" label="Save" disabled={props.pending || props.busy}>
          {props.pending ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </form>
  );
}

const firstLine = (text: string): string =>
  text
    .split('\n')
    .find((l) => l.trim())
    ?.trim()
    .slice(0, 90) ?? '';

function Event({ item }: { item: ActivityItem }) {
  return (
    <li className="tevent min-w-0 text-ink-2">
      <span className="mr-2 inline-block h-2 w-2 rounded-pill bg-ink-3 align-middle" aria-hidden="true" />
      {item.line} <span className="text-hint text-ink-3">· {dateTime(item.at)}</span>
      {item.note ? (
        <div className="tnote mt-1 min-w-0 rounded-card border border-line px-3 py-1.5 text-ink">
          <Fold note={item.note} long={noteIsLong(item.note)}>
            {(text) => <Markdown text={text} />}
          </Fold>
        </div>
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
  const [editing, setEditing] = useState(0); // comments being edited: the composer hides its Source link meanwhile
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
              <Comment
                key={`${n}-${i.memo}`}
                t={t}
                item={i}
                onReply={can.ok ? setReplyTo : undefined}
                onEditing={(on) => setEditing((n) => Math.max(0, n + (on ? 1 : -1)))}
              />
            ) : (
              <Event key={`${n}-${i.line}`} item={i} />
            ),
          )}
        </ul>
      ) : null}
      <div ref={box} className="grid gap-1.5" title={can.why}>
        {can.ok ? (
          <>
            {answering ? (
              <p className="m-0 flex items-center gap-2 text-hint text-ink-2" data-replying={answering.id}>
                <span className="min-w-0 flex-1 truncate">{`Replying to ${answering.author}: ${firstLine(answering.text)}`}</span>
                <Button variant="outline" label="Cancel reply" onClick={() => setReplyTo(null)} />
              </p>
            ) : null}
            <BodyEditor
              plain={editing > 0}
              compact
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
              <span className="text-[12px] leading-4 text-ink-3">@agent asks that agent. The assignee is told either way.</span>
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
