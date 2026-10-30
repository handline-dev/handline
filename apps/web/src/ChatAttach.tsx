// Images on a message: the chips under the box while they upload (a preview, the progress, a remove mark) and the pictures drawn in a sent
// message (a small copy; a click opens the original). The files come from a paste, a drop or the file picker.
import { useEffect, useRef, useState, type ClipboardEvent } from 'react';
import type { Attachment } from '@handline/client';
import {
  MAX_PER_MESSAGE,
  forgetAttachment,
  keptState,
  rememberAttachment,
  sentence,
  classify,
  useUploadImage,
  whyNotImage,
} from '@handline/app-data';
import { AttachmentImage } from '#src/AttachmentImage.tsx';
import { ImageViewer } from '#src/ImageViewer.tsx';
import { Icon } from '#src/ui/index.ts';

export interface Draft {
  key: number;
  name: string;
  preview: string;
  /** 0..1 while sending. */
  progress: number;
  attachment?: Attachment;
  error?: string;
}

let nextKey = 1;

interface Held {
  drafts: Draft[];
  problem: string | null;
  live: Map<number, { aborted: boolean; preview: string; file: File }>;
}
const useHeld = keptState<Held>(() => ({ drafts: [], problem: null, live: new Map() }));

/** The images being attached to the next message: each is sent to the daemon as soon as it is added. Kept per agent, so they are still there when the person comes back to the chat. */
export function useAttachDraft(agent?: string) {
  const upload = useUploadImage();
  const held = useHeld(agent);
  const { drafts, problem } = held.state;
  const live = held.get().live;
  const patch = (key: number, p: Partial<Draft>) =>
    held.set((s) => ({ drafts: s.drafts.map((x) => (x.key === key ? { ...x, ...p } : x)) }));

  const send = (key: number, f: File, signal: { aborted: boolean }) =>
    upload(f, f.name || 'image', f.type as Attachment['mime'], (sent, total) => patch(key, { progress: sent / total }), signal).then(
      (attachment) => !signal.aborted && (rememberAttachment(attachment), patch(key, { attachment, progress: 1 })),
      (e) => !signal.aborted && patch(key, { error: sentence(classify(e)) }),
    );
  const retry = (key: number) => {
    const l = live.get(key);
    if (!l) return;
    patch(key, { error: undefined, progress: 0 });
    void send(key, l.file, l);
  };
  const add = (files: File[]) => {
    held.set({ problem: null });
    for (const f of files) {
      const why = whyNotImage(f);
      if (why) return held.set({ problem: why });
      if (live.size >= MAX_PER_MESSAGE) return held.set({ problem: `At most ${MAX_PER_MESSAGE} images to a message` });
      const key = nextKey++;
      const preview = URL.createObjectURL(f);
      const signal = { aborted: false, preview, file: f };
      live.set(key, signal);
      held.set((s) => ({ drafts: [...s.drafts, { key, name: f.name || 'image', preview, progress: 0 }] }));
      void send(key, f, signal);
    }
  };
  // Stops the upload and lets go of the preview; false when the draft was already gone.
  const drop = (key: number) => {
    const l = live.get(key);
    if (!l) return false;
    l.aborted = true;
    URL.revokeObjectURL(l.preview);
    live.delete(key);
    return true;
  };
  const remove = (key: number) => {
    if (!drop(key)) return;
    const gone = held.get().drafts.find((x) => x.key === key)?.attachment;
    if (gone) forgetAttachment(gone.id);
    held.set((s) => ({ drafts: s.drafts.filter((x) => x.key !== key) }));
  };
  const clear = () => {
    for (const key of [...live.keys()]) drop(key);
    held.set({ drafts: [], problem: null });
  };
  return {
    drafts,
    problem,
    add,
    remove,
    retry,
    clear,
    /** Sent to the daemon in full: the ids to name in the message. */
    ids: drafts.flatMap((d) => d.attachment?.id ?? []),
    /** Still going, or failed: the message waits (a failed one is removed by the person). */
    busy: drafts.some((d) => !d.attachment),
  };
}

const imagesOf = (list: FileList | null | undefined): File[] => Array.from(list ?? []).filter((f) => f.type.startsWith('image/'));

/** The paste handler: images go to `add`; the rest of the paste goes into the box as text. */
export function pasteImages(add: (files: File[]) => void) {
  return (e: ClipboardEvent) => {
    const files = imagesOf(e.clipboardData?.files);
    if (!files.length) return;
    e.preventDefault();
    add(files);
  };
}

/**
 * Files dropped anywhere in the chat pane (the `.chat` section around `wrap`'s element): pictures go to `add`, anything else is said not to
 * be sendable. `over` is true while files are dragged over the pane, so the caller can cover it.
 */
export function usePaneDrop(add: (files: File[]) => void, enabled: boolean) {
  const wrap = useRef<HTMLDivElement>(null);
  const [pane, setPane] = useState<HTMLElement | null>(null);
  const [over, setOver] = useState(false);
  const [note, setNote] = useState('');
  const take = useRef(add);
  useEffect(() => {
    take.current = add;
  });
  useEffect(() => {
    const root = wrap.current?.closest<HTMLElement>('.chat') ?? null;
    setPane(root);
    if (!root || !enabled) return;
    const files = (e: DragEvent) => [...(e.dataTransfer?.types ?? [])].includes('Files');
    const enter = (e: DragEvent) => files(e) && setOver(true);
    const dragOver = (e: DragEvent) => files(e) && e.preventDefault();
    const leave = (e: DragEvent) => root.contains(e.relatedTarget as Node | null) || setOver(false);
    const drop = (e: DragEvent) => {
      setOver(false);
      const all = Array.from(e.dataTransfer?.files ?? []);
      if (!all.length) return;
      e.preventDefault();
      const pictures = imagesOf(e.dataTransfer?.files);
      if (pictures.length) take.current(pictures);
      setNote(pictures.length < all.length ? 'Only pictures can be sent for now' : '');
    };
    root.addEventListener('dragenter', enter);
    root.addEventListener('dragover', dragOver);
    root.addEventListener('dragleave', leave);
    root.addEventListener('drop', drop);
    return () => {
      root.removeEventListener('dragenter', enter);
      root.removeEventListener('dragover', dragOver);
      root.removeEventListener('dragleave', leave);
      root.removeEventListener('drop', drop);
    };
  }, [enabled]);
  useEffect(() => {
    if (!note) return;
    const t = setTimeout(() => setNote(''), 4000);
    return () => clearTimeout(t);
  }, [note]);
  return { wrap, pane, over, note };
}

/** A ring over a thumbnail that fills clockwise as the upload goes; a red full ring when it failed. */
function Ring({ value, failed }: { value: number; failed?: boolean }) {
  const r = 15;
  const c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 36 36" className="h-9 w-9 -rotate-90 rounded-sm bg-black/35" aria-hidden="true">
      <circle cx="18" cy="18" r={r} fill="none" strokeWidth="3" className="stroke-white/30" />
      <circle
        cx="18"
        cy="18"
        r={r}
        fill="none"
        strokeWidth="3"
        strokeLinecap="round"
        className={failed ? 'stroke-bad' : 'stroke-white'}
        strokeDasharray={c}
        strokeDashoffset={c * (1 - (failed ? 1 : Math.max(0.04, value)))}
      />
    </svg>
  );
}

export function AttachChips(props: {
  drafts: Draft[];
  problem: string | null;
  onRemove: (key: number) => void;
  onRetry?: (key: number) => void;
}) {
  const [viewing, setViewing] = useState<number | null>(null);
  if (!props.drafts.length && !props.problem) return null;
  return (
    <div className="attach-chips mx-3 mb-1 flex flex-wrap items-center gap-1.5">
      {props.drafts.map((d) => (
        <span
          key={d.key}
          className="attach-chip relative inline-flex items-center gap-1.5 rounded-sm bg-panel py-0.5 pl-0.5 pr-1.5 text-[12px]"
          data-state={d.error ? 'failed' : d.attachment ? 'ready' : 'sending'}
          title={d.error ?? d.name}
        >
          <span className="relative inline-flex h-9 w-9">
            <button
              type="button"
              className="border-0 bg-transparent p-0"
              aria-label={`Open ${d.name}`}
              title="Enlarge"
              onClick={() => setViewing(props.drafts.indexOf(d))}
            >
              <img src={d.preview} alt="" className="h-9 w-9 rounded-sm object-cover" />
            </button>
            {d.error ? (
              <button
                type="button"
                className="absolute inset-0 border-0 bg-transparent p-0"
                aria-label={`Retry ${d.name}`}
                title={`${d.error} Retry`}
                onClick={() => props.onRetry?.(d.key)}
              >
                <Ring value={1} failed />
              </button>
            ) : !d.attachment ? (
              <span
                className="pointer-events-none absolute inset-0"
                role="progressbar"
                aria-label={`Sending ${d.name}`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(d.progress * 100)}
              >
                <Ring value={d.progress} />
              </span>
            ) : null}
          </span>
          <span className="max-w-[140px] truncate">{d.name}</span>
          {d.error ? (
            <span className="sr-only" role="alert">
              {d.error}
            </span>
          ) : null}
          <button className="icon" aria-label={`Remove ${d.name}`} title="Remove" onClick={() => props.onRemove(d.key)}>
            <Icon name="close" size={12} label="Remove" />
          </button>
        </span>
      ))}
      {props.problem ? (
        <span className="text-sm text-bad" role="status">
          {props.problem}
        </span>
      ) : null}
      {viewing !== null ? (
        <ImageViewer
          pictures={props.drafts.map((d) => ({ url: d.preview, name: d.name }))}
          start={viewing}
          onClose={() => setViewing(null)}
        />
      ) : null}
    </div>
  );
}

/** The pictures of a sent message, in the order they were attached. */
export function Pictures({ items, thumb }: { items: Pick<Attachment, 'id' | 'name'>[]; thumb?: boolean }) {
  return (
    <div className="attachments mt-1 flex flex-wrap gap-1.5">
      {items.map((a, i) => (
        <AttachmentImage key={a.id} id={a.id} name={a.name} group={items} at={i} thumb={thumb} />
      ))}
    </div>
  );
}
