// New task: owner, title, body, time box and priority. As a request (`request`) it is the founder's own ask: owner, title and body only. The row shows in the list at once with a hollow dot; a refusal leaves the text here.
// A refusal for a title that repeats an open task names that task and offers to create it anyway.
import { useRef, useState } from 'react';
import { classify, sentence, useWrite } from '@handline/app-data';
import { BodyEditor } from '#src/BodyEditor.tsx';
import { Button, Dialog, FIELD, FIELD_LABEL, Field, Icon, NOTE, Select } from '#src/ui/index.ts';

export type NewTaskStart = { owner?: string; title?: string; body?: string; priority?: 1 | 2 | 3 };

/** `parent` makes it a sub-task of that task; `start` fills the form (a duplicate); `copy` sends it past the duplicate-title gate, as a duplicate has the title of its original. */
export function NewTask(props: {
  agents: string[];
  onClose: () => void;
  request?: boolean;
  parent?: number;
  start?: NewTaskStart;
  copy?: boolean;
}) {
  const { agents, onClose, request = false, parent, start, copy = false } = props;
  const ask = useWrite('task.new');
  const [owner, setOwner] = useState(start?.owner ?? '');
  const [title, setTitle] = useState(start?.title ?? '');
  const [body, setBody] = useState(start?.body ?? '');
  const [uploading, setUploading] = useState(false);
  const uploadingNow = useRef(false);
  const [box, setBox] = useState('2h');
  const [priority, setPriority] = useState<1 | 2 | 3>(start?.priority ?? 2);
  const [missing, setMissing] = useState('');
  const send = (duplicateOk = false) => {
    if (uploadingNow.current) return;
    if (!owner) return setMissing('Pick who it is for.');
    if (!title.trim()) return setMissing('A task needs a title.');
    setMissing('');
    ask.mutate(
      {
        owner,
        title: title.trim(),
        body,
        time_box: box.trim() || '2h',
        priority,
        ...(parent !== undefined ? { parent } : {}),
        ...(duplicateOk || copy ? { duplicate_ok: true } : {}),
      },
      { onSuccess: onClose },
    );
  };
  const failure = ask.isError ? classify(ask.error) : null;
  const duplicate = failure?.kind === 'refused' && failure.gate === 'duplicate_title' ? failure : null;
  const said = missing || (failure ? (duplicate ? duplicate.reason : sentence(failure)) : '');
  return (
    <Dialog
      title={request ? 'New request' : parent !== undefined ? 'New sub-task' : copy ? 'Duplicate task' : 'New task'}
      onClose={onClose}
      action={{ label: request ? 'Send' : 'Ask', keys: '⌘↵', form: 'newtask-form', disabled: ask.isPending || uploading }}
    >
      <form
        id="newtask-form"
        name="newtask-form"
        className="tform grid gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <div className={FIELD}>
          <span className={FIELD_LABEL}>Assigned to</span>
          <Select
            label="Assigned to"
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            options={[{ value: '', label: 'pick an agent' }, ...agents.map((a) => ({ value: a, label: a }))]}
          />
        </div>
        <Field label="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <div className={FIELD}>
          <span className={FIELD_LABEL}>Body</span>
          <BodyEditor
            id="newtask-body"
            label="Body"
            value={body}
            onChange={setBody}
            onBusy={(b) => ((uploadingNow.current = b), setUploading(b))}
          />
        </div>
        {request ? null : (
          <div className="tform-row grid grid-cols-2 gap-3 max-sm:grid-cols-1">
            <Field label="Time box" value={box} onChange={(e) => setBox(e.target.value)} />
            <div className={FIELD}>
              <span className={FIELD_LABEL}>Priority</span>
              <Select
                label="Priority"
                value={String(priority)}
                onChange={(e) => setPriority(Number(e.target.value) as 1 | 2 | 3)}
                options={[
                  { value: '1', label: '1 high' },
                  { value: '2', label: '2' },
                  { value: '3', label: '3 low' },
                ]}
              />
            </div>
          </div>
        )}
        {said ? (
          <p className={`${NOTE} text-bad`} role="alert">
            <Icon name="failed" size={14} label="Error" />
            {said}
          </p>
        ) : null}
        {duplicate ? (
          <div className="flex items-center gap-2">
            {duplicate.task !== undefined ? (
              <a href={`#/tasks/${duplicate.task}`} className="mono" onClick={onClose}>
                {`Open T${duplicate.task}`}
              </a>
            ) : null}
            <Button label="Create anyway" disabled={ask.isPending} onClick={() => send(true)} />
          </div>
        ) : null}
      </form>
    </Dialog>
  );
}
