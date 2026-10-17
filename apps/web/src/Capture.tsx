// Capture a window of this machine and send it: pick a window, look at it, write a note, then ask a new task whose body holds the picture
// or add it as a comment on a task of the list (docs/story1-research.md, 1-1). The picture is marked up first (Annotate.tsx) and takes the whole window,
// its notes pinned to spots on it. Only in the desktop shell, which owns the capture.
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { taskRef, titleFromNote, withImages } from '@handline/app-core';
import { classify, sentence, useAgents, useTasks, useUploadImage, useWrite } from '@handline/app-data';
import { Annotator } from './Annotate.tsx';
import { useTasksRoute } from './route.ts';
import { shell } from './shell.ts';
import { Button, Dialog, NOTE, Select, Textarea } from './ui/index.ts';

/** True when this shell can capture a window. */
export const canCapture = (): boolean => shell('captureWindow') !== null;

const EVENT = 'handline:capture';
/** Asks the capture host (mounted once, app-wide) to open the dialog. */
export const requestCapture = (): void => void dispatchEvent(new Event(EVENT));

/** The capture dialog for the whole app: opens on a request or ⌘⇧C (not Ctrl+Shift+C, which is copy in a terminal), over the open task if any. */
export function CaptureHost(): ReactNode {
  const [open, setOpen] = useState(false);
  const route = useTasksRoute();
  const agents = useAgents().data?.agents.map((a) => a.name) ?? [];
  useEffect(() => {
    if (!canCapture()) return;
    const key = (e: KeyboardEvent) => {
      if (e.metaKey && e.shiftKey && !e.ctrlKey && !e.altKey && e.key.toLowerCase() === 'c') {
        e.preventDefault();
        setOpen(true);
      }
    };
    const ask = () => setOpen(true);
    addEventListener('keydown', key);
    addEventListener(EVENT, ask);
    return () => {
      removeEventListener('keydown', key);
      removeEventListener(EVENT, ask);
    };
  }, []);
  if (!open) return null;
  const task = route && /^\d+$/.test(route) ? Number(route) : null;
  return <CaptureDialog agents={agents} task={task} onClose={() => setOpen(false)} />;
}

type Win = { id: string; name: string; thumb: string };
const asBlob = (base64: string): Blob => new Blob([Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))], { type: 'image/png' });

export function CaptureDialog({
  agents,
  task,
  onClose,
}: {
  agents: string[];
  /** The open task, when there is one: the comment option starts on it. */
  task: number | null;
  onClose: () => void;
}) {
  const [windows, setWindows] = useState<Win[] | null>(null);
  const [taking, setTaking] = useState(false);
  const [problem, setProblem] = useState('');
  const [shot, setShot] = useState<{ name: string; blob: Blob } | null>(null);
  const [marked, setMarked] = useState<Blob | null>(null);
  const [pinned, setPinned] = useState<string[]>([]); // the words of the notes pinned on the picture, numbered as it shows them
  const [note, setNote] = useState('');
  const [owner, setOwner] = useState('');
  const [as, setAs] = useState<'task' | 'comment'>(task === null ? 'task' : 'comment');
  const [target, setTarget] = useState<number | null>(task);
  const open = (useTasks({}).data?.items ?? []).filter((t) => t.state !== 'done' && t.state !== 'cancelled');
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
    // The picture may be of this very window: take the other windows' thumbnails off it first.
    setWindows(null);
    setTaking(true);
    await new Promise((r) => setTimeout(r, 150));
    const r = await shell('captureWindow')?.captureWindow?.({ id: w.id });
    if (gone.current) return;
    setTaking(false);
    if (!r) return setProblem('This app cannot capture a window.');
    if (!r.ok) return setProblem(r.message);
    setShot({ name: r.name, blob: asBlob(r.png) });
    setMarked(null);
  };

  const picture = marked ?? shot?.blob ?? null;

  const send = async () => {
    if (!shot || !picture) return;
    if (as === 'task' && !owner) return setProblem('Pick who it is for.');
    if (as === 'comment' && target === null) return setProblem('Pick the task.');
    setSending(true);
    setProblem('');
    try {
      const a = await upload(picture, shot.name, 'image/png');
      const said = [note.trim(), ...pinned.map((t, i) => `${i + 1}. ${t}`)].filter(Boolean).join('\n');
      const text = withImages(said, [a.id], shot.name.replace(/\.png$/, ''));
      if (as === 'comment' && target !== null) await memo.mutateAsync({ task: target, text });
      else
        await ask.mutateAsync({ owner, title: titleFromNote(note || pinned[0] || '', 'Capture'), body: text, time_box: '2h', priority: 2 });
      if (!gone.current) onClose();
    } catch (e) {
      if (!gone.current) {
        setProblem(sentence(classify(e)));
        setSending(false);
      }
    }
  };

  return (
    <Dialog
      title="Capture a window"
      onClose={onClose}
      full={!!shot}
      action={shot ? { label: as === 'task' ? 'Ask' : 'Comment', keys: '⌘↵', onClick: () => void send(), disabled: sending } : undefined}
      bar={
        <Textarea
          id="capture-note"
          rows={1}
          aria-label="Message"
          placeholder="Message"
          className="min-w-0 flex-1"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      }
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
          {windows === null && !problem ? <p className={NOTE}>{taking ? 'Taking the picture…' : 'Looking for windows…'}</p> : null}
          <Button label="Refresh" onClick={() => void list()} />
        </div>
      ) : (
        <Annotator
          image={shot.blob}
          onChange={(b, notes) => (setMarked(b), setPinned(notes))}
          extra={
            <>
              <Select
                label="Send as"
                value={as}
                onChange={(e) => setAs(e.target.value as 'task' | 'comment')}
                options={[
                  { value: 'task', label: 'A new task' },
                  { value: 'comment', label: 'A comment on a task' },
                ]}
              />
              {as === 'comment' ? (
                <Select
                  label="Task"
                  value={target === null ? '' : String(target)}
                  onChange={(e) => setTarget(e.target.value ? Number(e.target.value) : null)}
                  options={[
                    { value: '', label: 'pick a task' },
                    ...open.map((t) => ({ value: String(t.id), label: `${taskRef(t.id)} ${t.title}` })),
                  ]}
                />
              ) : (
                <Select
                  label="Owner"
                  value={owner}
                  onChange={(e) => setOwner(e.target.value)}
                  options={[{ value: '', label: 'pick an agent' }, ...agents.map((a) => ({ value: a, label: a }))]}
                />
              )}
              <Button icon="back" label="Another window" onClick={() => setShot(null)} />
            </>
          }
        />
      )}
      {problem ? (
        <p className={`${NOTE} text-bad`} role="alert">
          {problem}
        </p>
      ) : null}
    </Dialog>
  );
}
