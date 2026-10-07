// The standing answers: what the daemon answers by itself (a trusted folder, a vendor's dialog and the option to choose). Add and remove are
// authored: the row changes at once and comes back if the daemon refuses.
import { useState } from 'react';
import { useAnswers, useServes, useWrite } from '@handline/app-data';
import { useOff } from './useOff.ts';
import { View } from './View.tsx';

const keyOf = (a: { kind: string; key: string[] }) => `${a.kind}\0${a.key.join('\0')}`;

function AddForm({ off, onDone }: { off: string | undefined; onDone: () => void }) {
  const add = useWrite('answer.add');
  const [kind, setKind] = useState<'trust' | 'dialog'>('trust');
  const [f, setF] = useState({ folder: '', vendor: '', title: '', label: '' });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const p = kind === 'trust' ? { folder: f.folder.trim() } : { vendor: f.vendor.trim(), title: f.title.trim(), label: f.label.trim() };
    if (Object.values(p).some((x) => !x)) return; // a form with an empty field sends nothing
    add.mutate(p);
    setF({ folder: '', vendor: '', title: '', label: '' });
    onDone();
  };
  return (
    <form className="answer-form" onSubmit={submit} aria-label="Add a standing answer">
      <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value as 'trust' | 'dialog')}>
        <option value="trust">Trust a folder</option>
        <option value="dialog">Answer a dialog</option>
      </select>
      {kind === 'trust' ? (
        <input aria-label="Folder" placeholder="/path/to/folder" value={f.folder} onChange={set('folder')} />
      ) : (
        <>
          <input aria-label="Vendor" placeholder="vendor" value={f.vendor} onChange={set('vendor')} />
          <input aria-label="Prompt title" placeholder="prompt title" value={f.title} onChange={set('title')} />
          <input aria-label="Option" placeholder="option to choose" value={f.label} onChange={set('label')} />
        </>
      )}
      <button className="btn primary" type="submit" disabled={!!off} title={off}>
        Add
      </button>
    </form>
  );
}

export function Answers() {
  const q = useAnswers();
  const { served } = useServes('answers.list');
  const remove = useWrite('answer.remove');
  const off = useOff('answer.add');
  const offRemove = useOff('answer.remove');
  const [picked, setPicked] = useState<string[]>([]);
  const [adding, setAdding] = useState(false);
  const rows = q.data?.answers ?? [];
  return (
    <>
      <h3>Standing answers</h3>
      <View
        q={q}
        served={served}
        shape="settings"
        what="standing answers"
        empty={!rows.length && 'none - you are asked when a dialog shows'}
      >
        <ul className="answers">
          {rows.map((a) => (
            <li key={keyOf(a)} data-answer={a.kind}>
              <label>
                <input
                  type="checkbox"
                  checked={picked.includes(keyOf(a))}
                  onChange={(e) => setPicked(e.target.checked ? [...picked, keyOf(a)] : picked.filter((k) => k !== keyOf(a)))}
                />{' '}
                {a.text}
              </label>
            </li>
          ))}
        </ul>
      </View>
      {served ? (
        <div className="answers-actions">
          <button className="btn" disabled={!!off} title={off} onClick={() => setAdding(!adding)}>
            Add…
          </button>
          <button
            className="btn"
            disabled={!picked.length || !!offRemove}
            title={offRemove}
            onClick={() => {
              for (const a of rows.filter((r) => picked.includes(keyOf(r)))) remove.mutate({ kind: a.kind, key: a.key });
              setPicked([]);
            }}
          >
            Remove selected
          </button>
        </div>
      ) : null}
      {adding ? <AddForm off={off} onDone={() => setAdding(false)} /> : null}
    </>
  );
}
