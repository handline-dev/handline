// "In editor": the text goes to the person's editor through the desktop shell and comes back into the box (docs/electron-bridge.md §4.2).
// The box is never cleared by this: every failure leaves the draft where it was. Absent in a plain browser, where the box itself is the editor.
import { useState } from 'react';
import { shell } from './shell.ts';

export function EditorButton({ text, onText }: { text: string; onText: (v: string) => void }) {
  const [waiting, setWaiting] = useState(false);
  const [note, setNote] = useState('');
  const api = shell('compose');
  if (!api) return null;
  const run = async () => {
    setNote('');
    setWaiting(true);
    const r = await api.compose!({ text });
    setWaiting(false);
    if (!r.ok) return setNote(r.code === 'cancelled' ? '' : r.message);
    if (!r.changed) return setNote('Nothing changed in the editor.');
    onText(r.text.replace(/\n+$/, ''));
  };
  return waiting ? (
    <span role="status">
      Waiting for the editor…{' '}
      <button type="button" className="btn" onClick={() => void shell('cancelCompose')?.cancelCompose!()}>
        Cancel
      </button>
    </span>
  ) : (
    <>
      <button type="button" className="btn" onClick={() => void run()}>
        In editor
      </button>
      {note ? (
        <span className="muted" role="status">
          {' '}
          {note}
        </span>
      ) : null}
    </>
  );
}
