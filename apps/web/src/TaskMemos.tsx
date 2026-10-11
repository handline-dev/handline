// A task's memos: a row per memo (edit, delete asks first) and the box that adds one.
import { useState } from 'react';
import { useWrite } from '@handline/app-data';
import { Icon } from './ui/Icon.tsx';
import { Button, Sheet, SheetActions, Textarea } from './ui/index.ts';

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
          <Button
            size="sm"
            variant="outline"
            label="Edit memo"
            aria-label="Edit memo"
            disabled={!canEdit.ok || memo.id < 0}
            title={canEdit.why}
            onClick={() => setText(memo.text)}
          >
            Edit
          </Button>{' '}
          <Button
            size="sm"
            variant="outline"
            label="Delete memo"
            aria-label="Delete memo"
            disabled={!canDelete.ok || memo.id < 0}
            title={canDelete.why}
            onClick={() => setAsk(true)}
          >
            Delete
          </Button>
        </>
      ) : (
        <span className={MEMO}>
          <Textarea grow className="tinput" rows={2} aria-label="Memo text" value={text} onChange={(e) => setText(e.target.value)} />
          <Button size="sm" variant="solid" label="Save" disabled={!text.trim()} onClick={save}>
            Save
          </Button>{' '}
          <Button size="sm" variant="outline" label="Cancel" onClick={() => setText(null)} />
        </span>
      )}
      {ask ? (
        <Sheet as="span" danger role="alertdialog" aria-label="Delete memo">
          <strong>Delete this memo?</strong>
          <SheetActions>
            <Button
              variant="solid"
              label="Delete"
              className="max-sm:min-h-12 max-sm:flex-1"
              onClick={() => del.mutate({ task, memo: memo.id }, { onSettled: () => setAsk(false) })}
            >
              Delete
            </Button>
            <Button variant="outline" label="Cancel" className="max-sm:min-h-12 max-sm:flex-1" onClick={() => setAsk(false)} />
          </SheetActions>
        </Sheet>
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
      <Textarea
        ref={box}
        grow
        className="tinput"
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
