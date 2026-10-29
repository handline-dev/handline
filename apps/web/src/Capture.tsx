// Capture a window of this machine (desktop shell only): lists the windows, marks the picked one up (Annotate.tsx), and sends it from one bottom bar
// (a comment on the open task, else a new task). One message carries the plain picture, the marked one, the memos as 1. 2. lines and each mark's shape and rect.
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { keptMarks, markText, titleFromNote, withImages, type Mark } from '@handline/app-core';
import { classify, sentence, useAgents, useTasks, useUploadImage, useWrite } from '@handline/app-data';
import { Annotator } from '#src/Annotate.tsx';
import { useTasksRoute } from '#src/route.ts';
import { shell } from '#src/shell.ts';
import { CaptureDestination, type Destination } from '#src/CaptureDestination.tsx';
import { Button, Dialog, NOTE, Textarea } from '#src/ui/index.ts';
import { AnimatedList } from '#src/AnimatedList.tsx';

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
  const task = route && /^\d+$/.test(route) ? Number(route) : null;
  return (
    <AnimatedList>{open ? <CaptureDialog key="capture" agents={agents} task={task} onClose={() => setOpen(false)} /> : null}</AnimatedList>
  );
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
  const [hidden, setHidden] = useState(false);
  const [problem, setProblem] = useState('');
  const [shot, setShot] = useState<{ name: string; blob: Blob } | null>(null);
  const [marked, setMarked] = useState<Blob | null>(null);
  const [marks, setMarks] = useState<Mark[]>([]); // the memos marked on the picture; one with no words is left out when it goes
  const [note, setNote] = useState('');
  const [dest, setDest] = useState<Destination>({ as: task === null ? 'task' : 'comment', target: task, owner: '' });
  const [choosing, setChoosing] = useState(false);
  const open = (useTasks({}).data?.items ?? []).filter((t) => t.state !== 'done' && t.state !== 'cancelled');
  const [sending, setSending] = useState(false);
  const upload = useUploadImage();
  const ask = useWrite('task.new');
  const memo = useWrite('task.comment.add');
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
    // The picture may be of this very window: the surface is taken off the screen, and two frames let that reach it, before the capture.
    setHidden(true);
    await new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done())));
    const r = await shell('captureWindow')
      ?.captureWindow?.({ id: w.id })
      .catch((e: unknown) => ({ ok: false as const, message: sentence(classify(e)) }))
      .finally(() => setHidden(false));
    if (gone.current) return;
    if (!r) return setProblem('This app cannot capture a window.');
    if (!r.ok) return setProblem(r.message);
    setShot({ name: r.name, blob: asBlob(r.png) });
    setMarked(null);
  };

  const picture = marked ?? shot?.blob ?? null;

  const send = async () => {
    if (!shot || !picture) return;
    const { as, target, owner } = dest;
    if ((as === 'task' && !owner) || (as === 'comment' && target === null)) {
      setChoosing(true);
      return setProblem(as === 'task' ? 'Pick who it is for.' : 'Pick the task.');
    }
    setSending(true);
    setProblem('');
    try {
      const kept = keptMarks(marks);
      const alt = shot.name.replace(/\.png$/, '');
      const plain = await upload(shot.blob, shot.name, 'image/png');
      const drawn = kept.length ? await upload(picture, `${alt} marked.png`, 'image/png') : null;
      const text = withImages(markText(note, marks), drawn ? [plain.id, drawn.id] : [plain.id], alt);
      if (as === 'comment' && target !== null) await memo.mutateAsync({ task: target, text });
      else
        await ask.mutateAsync({
          owner,
          title: titleFromNote(note || keptMarks(marks)[0]?.text || '', 'Capture'),
          body: text,
          time_box: '2h',
          priority: 2,
        });
      if (!gone.current) onClose();
    } catch (e) {
      if (!gone.current) {
        setProblem(sentence(classify(e)));
        setSending(false);
      }
    }
  };

  if (hidden) return null;
  return (
    <Dialog
      title="Capture a window"
      onClose={onClose}
      full
      action={shot ? { label: 'Send', keys: '⌘↵', onClick: () => void send(), disabled: sending } : undefined}
      bar={
        <>
          <CaptureDestination value={dest} onChange={setDest} open={choosing} onOpen={setChoosing} tasks={open} agents={agents} />
          <Textarea
            id="capture-note"
            rows={1}
            aria-label="Message"
            placeholder="Message"
            className="min-w-0 flex-1"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </>
      }
    >
      {!shot ? (
        <div className="grid gap-3">
          <div className="grid grid-cols-3 gap-3 max-sm:grid-cols-1" aria-label="Windows">
            {(windows ?? []).map((w) => (
              <button
                key={w.id}
                type="button"
                className="grid gap-1 rounded-md border border-line p-2 text-left"
                onClick={() => void pick(w)}
              >
                <img src={w.thumb} alt="" className="w-full rounded-md" />
                <span className="truncate text-ink-2">{w.name}</span>
              </button>
            ))}
          </div>
          {windows === null && !problem ? <p className={NOTE}>Looking for windows…</p> : null}
          <Button label="Refresh" onClick={() => void list()} />
        </div>
      ) : (
        <Annotator image={shot.blob} onChange={(b, m) => (setMarked(b), setMarks(m))} />
      )}
      {problem ? (
        <p className={`${NOTE} text-bad`} role="alert">
          {problem}
        </p>
      ) : null}
    </Dialog>
  );
}
