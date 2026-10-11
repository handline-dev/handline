// A task's memos: a row per memo (edit, delete asks first) and the box that adds one.
import { useState } from 'react';
import { useWrite } from '@handline/app-data';
import { Icon } from './ui/Icon.tsx';

const INPUT = 'tinput min-w-40 flex-1 rounded-ctl border border-line px-2 py-1.5 bg-canvas text-ink';
const MEMO = 'tmemo mt-2 flex gap-2';

export function MemoRow({
  task,
  memo,
  canEdit,
  canDelete,
}: {
  task: number;
  memo: { id: number; at: string; author: string; text: string };
  canEdit: { ok: boolean; why?: string };
  canDelete: { ok: boolean; why?: string };
}) {
  const edit = useWrite('task.memo.edit', String(task));
  const del = useWrite('task.memo.delete', String(task));
  const [text, setText] = useState<string | null>(null);
  const [ask, setAsk] = useState(false);
  const save = () => {
    const next = (text ?? '').trim();
    if (!next) return;
    edit.mutate({ task, memo: memo.id, text: next }, { onSuccess: () => setText(null) });
  };
  return (
    <li className="flex items-baseline gap-1.5 py-0.5">
      <Icon name="say" label="Memo" aria-hidden="true" size={14} />
      <span className="mono">{when(memo.at)}</span> <strong>{memo.author}</strong>:{' '}
      {text === null ? (
        <>
          {memo.text}{' '}
          <button
            className="btn"
            disabled={!canEdit.ok || memo.id < 0}
            title={canEdit.why}
            aria-label="Edit memo"
            onClick={() => setText(memo.text)}
          >
            Edit
          </button>{' '}
          <button
            className="btn"
            disabled={!canDelete.ok || memo.id < 0}
            title={canDelete.why}
            aria-label="Delete memo"
            onClick={() => setAsk(true)}
          >
            Delete
          </button>
        </>
      ) : (
        <span className={MEMO}>
          <textarea className={INPUT} rows={2} aria-label="Memo text" value={text} onChange={(e) => setText(e.target.value)} />
          <button className="btn primary" disabled={!text.trim()} onClick={save}>
            Save
          </button>{' '}
          <button className="btn" onClick={() => setText(null)}>
            Cancel
          </button>
        </span>
      )}
      {ask ? (
        <span className="verb-sheet danger" role="alertdialog" aria-label="Delete memo">
          <strong>Delete this memo?</strong>
          <span className="verb-sheet-actions">
            <button className="btn primary" onClick={() => del.mutate({ task, memo: memo.id }, { onSettled: () => setAsk(false) })}>
              Delete
            </button>
            <button className="btn" onClick={() => setAsk(false)}>
              Cancel
            </button>
          </span>
        </span>
      ) : null}
    </li>
  );
}

export function MemoBox({
  id,
  box,
  can,
}: {
  id: number;
  box: React.RefObject<HTMLTextAreaElement | null>;
  can: { ok: boolean; why?: string };
}) {
  const add = useWrite('task.memo.add', String(id));
  const [text, setText] = useState('');
  return (
    <form
      className={MEMO}
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) add.mutate({ task: id, text: text.trim() }, { onSuccess: () => setText('') });
      }}
    >
      <textarea
        ref={box}
        className={INPUT}
        rows={2}
        value={text}
        disabled={!can.ok}
        title={can.why}
        placeholder="Add a memo"
        aria-label="Memo"
        onChange={(e) => setText(e.target.value)}
      />
      <button className="btn icon" disabled={!can.ok || !text.trim()} title={can.why ?? 'Add memo'} aria-label="Add memo">
        <Icon name="send" label="Add memo" aria-hidden="true" />
      </button>
    </form>
  );
}

export const when = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};
