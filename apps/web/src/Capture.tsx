// Capture a window of this machine and send it: pick a window, look at it, write a note, then ask a new task whose body holds the picture
// or add it as a comment on the open task (docs/story1-research.md, 1-1). Only in the desktop shell, which owns the capture.
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { titleFromNote, withImages } from '@handline/app-core';
import { classify, sentence, useUploadImage, useWrite } from '@handline/app-data';
import { shell } from './shell.ts';
import { Button, Dialog, FIELD, FIELD_LABEL, NOTE, Select, Textarea } from './ui/index.ts';

/** True when this shell can capture a window. */
export const canCapture = (): boolean => shell('captureWindow') !== null;

/** ⌘⇧C (Ctrl+Shift+C elsewhere) asks for the capture dialog. */
export function useCaptureShortcut(open: () => void): void {
  useEffect(() => {
    if (!canCapture()) return;
    const key = (e: KeyboardEvent) => {
      if (e.shiftKey && (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'c' && !e.altKey) {
        e.preventDefault();
        open();
      }
    };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  });
}

/**
 * Where the annotation editor plugs in: it gets the picture and calls `onChange` with the marked-up one (a PNG), and what it draws shows
 * between the preview and the note. There is none yet (the library is the founder's call, T3927), so the picture goes as captured.
 */
export type Annotator = (props: { image: Blob; onChange: (marked: Blob) => void }) => ReactNode;

type Win = { id: string; name: string; thumb: string };
const asBlob = (base64: string): Blob => new Blob([Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))], { type: 'image/png' });

export function CaptureDialog({
  agents,
  task,
  annotate,
  onClose,
}: {
  agents: string[];
  /** The open task, when there is one: the picture may be added to it as a comment. */
  task: number | null;
  annotate?: Annotator;
  onClose: () => void;
}) {
  const [windows, setWindows] = useState<Win[] | null>(null);
  const [problem, setProblem] = useState('');
  const [shot, setShot] = useState<{ name: string; blob: Blob } | null>(null);
  const [marked, setMarked] = useState<Blob | null>(null);
  const [note, setNote] = useState('');
  const [owner, setOwner] = useState('');
  const [as, setAs] = useState<'task' | 'comment'>(task === null ? 'task' : 'comment');
  const [sending, setSending] = useState(false);
  const upload = useUploadImage();
  const ask = useWrite('task.new');
  const memo = useWrite('task.memo.add');
  const gone = useRef(false);
  useEffect(() => () => void (gone.current = true), []);

  const list = async () => {
    setProblem('');
    const r = await shell('captureWindows')?.captureWindows?.();
    if (gone.current) return;
    if (!r) return setProblem('This app cannot capture a window.');
    if (!r.ok) return setProblem(r.message);
    setWindows(r.windows);
    if (!r.windows.length) setProblem('No window to capture.');
  };
  useEffect(() => void list(), []);

  const pick = async (w: Win) => {
    setProblem('');
    const r = await shell('captureWindow')?.captureWindow?.({ id: w.id });
    if (gone.current) return;
    if (!r) return setProblem('This app cannot capture a window.');
    if (!r.ok) return setProblem(r.message);
    setShot({ name: r.name, blob: asBlob(r.png) });
    setMarked(null);
  };

  const picture = marked ?? shot?.blob ?? null;
  const [preview, setPreview] = useState('');
  useEffect(() => {
    if (!picture) return setPreview('');
    const u = URL.createObjectURL(picture);
    setPreview(u);
    return () => URL.revokeObjectURL(u);
  }, [picture]);

  const send = async () => {
    if (!shot || !picture) return;
    if (as === 'task' && !owner) return setProblem('Pick who it is for.');
    setSending(true);
    setProblem('');
    try {
      const a = await upload(picture, shot.name, 'image/png');
      const text = withImages(note, [a.id], shot.name.replace(/\.png$/, ''));
      if (as === 'comment' && task !== null) await memo.mutateAsync({ task, text } as never);
      else await ask.mutateAsync({ owner, title: titleFromNote(note, 'Capture'), body: text, time_box: '2h', priority: 2 } as never);
      if (!gone.current) onClose();
    } catch (e) {
      if (!gone.current) {
        setProblem(sentence(classify(e)));
        setSending(false);
      }
    }
  };

  const Annotate = annotate;
  return (
    <Dialog
      title="Capture a window"
      onClose={onClose}
      action={shot ? { label: as === 'task' ? 'Ask' : 'Comment', keys: '⌘↵', onClick: () => void send(), disabled: sending } : undefined}
    >
      {!shot ? (
        <div className="grid gap-3">
          <div className="grid grid-cols-2 gap-3 max-sm:grid-cols-1" aria-label="Windows">
            {(windows ?? []).map((w) => (
              <button
                key={w.id}
                type="button"
                className="grid gap-1 rounded-card border border-line p-2 text-left"
                onClick={() => void pick(w)}
              >
                <img src={w.thumb} alt="" className="w-full rounded-card" />
                <span className="truncate text-ink-2">{w.name}</span>
              </button>
            ))}
          </div>
          {windows === null && !problem ? <p className={NOTE}>Looking for windows…</p> : null}
          <Button label="Refresh" onClick={() => void list()} />
        </div>
      ) : (
        <div className="grid gap-3">
          {preview ? (
            <img src={preview} alt="The capture" data-testid="capture-preview" className="max-h-[50vh] w-full object-contain" />
          ) : null}
          {Annotate && shot ? <Annotate image={shot.blob} onChange={setMarked} /> : null}
          <div className={FIELD}>
            <label htmlFor="capture-note" className={FIELD_LABEL}>
              Note
            </label>
            <Textarea id="capture-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          {task !== null ? (
            <Select
              label="Send as"
              value={as}
              onChange={(e) => setAs(e.target.value as 'task' | 'comment')}
              options={[
                { value: 'comment', label: `A comment on T${task}` },
                { value: 'task', label: 'A new task' },
              ]}
            />
          ) : null}
          {as === 'task' ? (
            <Select
              label="Owner"
              value={owner}
              onChange={(e) => setOwner(e.target.value)}
              options={[{ value: '', label: 'pick an agent' }, ...agents.map((a) => ({ value: a, label: a }))]}
            />
          ) : null}
          <Button label="Another window" onClick={() => setShot(null)} />
        </div>
      )}
      {problem ? (
        <p className={`${NOTE} text-bad`} role="alert">
          {problem}
        </p>
      ) : null}
    </Dialog>
  );
}
