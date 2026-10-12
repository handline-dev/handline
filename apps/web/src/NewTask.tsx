// New task: owner, title, body, time box and priority. The row shows in the list at once with a hollow dot; a refusal leaves the text here.
import { useState } from 'react';
import { classify, sentence, useWrite } from '@handline/app-data';
import { Dialog, FIELD, FIELD_LABEL, Field, Icon, NOTE, Select, Textarea } from './ui/index.ts';

export function NewTask({ agents, onClose }: { agents: string[]; onClose: () => void }) {
  const ask = useWrite('task.new');
  const [owner, setOwner] = useState('');
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [box, setBox] = useState('2h');
  const [priority, setPriority] = useState<1 | 2 | 3>(2);
  const [missing, setMissing] = useState('');
  const send = () => {
    if (!owner) return setMissing('Pick who it is for.');
    if (!title.trim()) return setMissing('A task needs a title.');
    setMissing('');
    ask.mutate({ owner, title: title.trim(), body, time_box: box.trim() || '2h', priority }, { onSuccess: onClose });
  };
  const said = missing || (ask.isError ? sentence(classify(ask.error)) : '');
  return (
    <Dialog title="New task" onClose={onClose} action={{ label: 'Ask', keys: '⌘↵', form: 'newtask-form', disabled: ask.isPending }}>
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
          <span className={FIELD_LABEL}>Owner</span>
          <Select
            label="Owner"
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            options={[{ value: '', label: 'pick an agent' }, ...agents.map((a) => ({ value: a, label: a }))]}
          />
        </div>
        <Field label="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <div className={FIELD}>
          <label htmlFor="newtask-body" className={FIELD_LABEL}>
            Body
          </label>
          <Textarea
            id="newtask-body"
            className="bg-panel border-line-strong px-3 py-2 resize-y"
            rows={4}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        </div>
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
        {said ? (
          <p className={`${NOTE} text-bad`} role="alert">
            <Icon name="failed" size={14} label="Error" />
            {said}
          </p>
        ) : null}
      </form>
    </Dialog>
  );
}
