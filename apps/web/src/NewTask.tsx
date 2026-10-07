// New task: owner, title, body, time box and priority. The row shows in the list at once with a hollow dot; a refusal leaves the text here.
import { useState } from 'react';
import { useWrite } from '@handline/app-data';
import { Sheet } from './taskui.tsx';

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
  return (
    <Sheet label="New task" submit="Ask" full onCancel={onClose} onSubmit={send} busy={ask.isPending}>
      <div className="tform">
        <label>
          Owner
          <select value={owner} onChange={(e) => setOwner(e.target.value)}>
            <option value="">pick an agent</option>
            {agents.map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
        </label>
        <label>
          Title
          <input className="tinput" value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="wide">
          Body
          <textarea className="tinput" rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
        </label>
        <label>
          Time box
          <input className="tinput" value={box} onChange={(e) => setBox(e.target.value)} />
        </label>
        <label>
          Priority
          <select value={priority} onChange={(e) => setPriority(Number(e.target.value) as 1 | 2 | 3)}>
            <option value={1}>1 high</option>
            <option value={2}>2</option>
            <option value={3}>3 low</option>
          </select>
        </label>
      </div>
      {missing ? (
        <span className="tsheet-err" role="alert">
          {missing}
        </span>
      ) : null}
    </Sheet>
  );
}
