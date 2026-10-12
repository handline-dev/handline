// Images on a message: the chips under the box while they upload (a preview, the progress, a remove mark) and the pictures drawn in a sent
// message (a small copy; a click opens the original). The files come from a paste, a drop or the file picker.
import { useRef, useState, type ClipboardEvent, type DragEvent } from 'react';
import type { Attachment } from '@handline/client';
import {
  MAX_PER_MESSAGE,
  forgetAttachment,
  rememberAttachment,
  sentence,
  classify,
  useAttachmentUrl,
  useUploadImage,
  whyNotImage,
} from '@handline/app-data';
import { Dialog, Icon } from './ui/index.ts';

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

/** The images being attached to the next message: each is sent to the daemon as soon as it is added. */
export function useAttachDraft() {
  const upload = useUploadImage();
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const live = useRef(new Map<number, { aborted: boolean; preview: string }>());
  const count = useRef(0);
  const patch = (key: number, p: Partial<Draft>) => setDrafts((d) => d.map((x) => (x.key === key ? { ...x, ...p } : x)));

  const add = (files: File[]) => {
    setProblem(null);
    for (const f of files) {
      const why = whyNotImage(f);
      if (why) return setProblem(why);
      if (count.current >= MAX_PER_MESSAGE) return setProblem(`At most ${MAX_PER_MESSAGE} images to a message`);
      count.current += 1;
      const key = nextKey++;
      const preview = URL.createObjectURL(f);
      const signal = { aborted: false, preview };
      live.current.set(key, signal);
      setDrafts((d) => [...d, { key, name: f.name || 'image', preview, progress: 0 }]);
      upload(
        f,
        f.name || 'image',
        f.type as Attachment['mime'],
        (sent, total) => patch(key, { progress: sent / total }),
        signal,
      ).then(
        (attachment) => !signal.aborted && (rememberAttachment(attachment), patch(key, { attachment, progress: 1 })),
        (e) => !signal.aborted && patch(key, { error: sentence(classify(e)) }),
      );
    }
  };
  // Stops the upload and lets go of the preview; false when the draft was already gone.
  const drop = (key: number) => {
    const l = live.current.get(key);
    if (!l) return false;
    l.aborted = true;
    URL.revokeObjectURL(l.preview);
    live.current.delete(key);
    return true;
  };
  const remove = (key: number) => {
    if (!drop(key)) return;
    const gone = drafts.find((x) => x.key === key)?.attachment;
    if (gone) forgetAttachment(gone.id);
    count.current -= 1;
    setDrafts((d) => d.filter((x) => x.key !== key));
  };
  const clear = () => {
    for (const key of [...live.current.keys()]) drop(key);
    count.current = 0;
    setDrafts([]);
    setProblem(null);
  };
  return {
    drafts,
    problem,
    add,
    remove,
    clear,
    /** Sent to the daemon in full: the ids to name in the message. */
    ids: drafts.flatMap((d) => d.attachment?.id ?? []),
    /** Still going, or failed: the message waits (a failed one is removed by the person). */
    busy: drafts.some((d) => !d.attachment),
  };
}

const imagesOf = (list: FileList | null | undefined): File[] => Array.from(list ?? []).filter((f) => f.type.startsWith('image/'));

/** Handlers that take images from a paste or a drop; the rest of the paste goes into the box as text. */
export function attachHandlers(add: (files: File[]) => void) {
  return {
    onPaste: (e: ClipboardEvent) => {
      const files = imagesOf(e.clipboardData?.files);
      if (!files.length) return;
      e.preventDefault();
      add(files);
    },
    onDragOver: (e: DragEvent) => {
      if ([...e.dataTransfer.types].includes('Files')) e.preventDefault();
    },
    onDrop: (e: DragEvent) => {
      const files = imagesOf(e.dataTransfer.files);
      if (!files.length) return;
      e.preventDefault();
      add(files);
    },
  };
}

export function AttachChips(props: { drafts: Draft[]; problem: string | null; onRemove: (key: number) => void }) {
  if (!props.drafts.length && !props.problem) return null;
  return (
    <div className="attach-chips mx-3 mb-1 flex flex-wrap items-center gap-1.5">
      {props.drafts.map((d) => (
        <span
          key={d.key}
          className="attach-chip relative inline-flex items-center gap-1.5 rounded-chip bg-panel py-0.5 pl-0.5 pr-1.5 text-[12px]"
          data-state={d.error ? 'failed' : d.attachment ? 'ready' : 'sending'}
          title={d.error ?? d.name}
        >
          <img src={d.preview} alt="" className="h-9 w-9 rounded-chip object-cover" />
          <span className="max-w-[140px] truncate">{d.name}</span>
          {d.error ? (
            <span className="text-bad" role="alert">
              <Icon name="failed" size={13} label={d.error} />
            </span>
          ) : !d.attachment ? (
            <progress className="h-1 w-12" max={1} value={d.progress} aria-label={`Sending ${d.name}`} />
          ) : null}
          <button className="icon" aria-label={`Remove ${d.name}`} title="Remove" onClick={() => props.onRemove(d.key)}>
            <Icon name="close" size={12} label="Remove" />
          </button>
        </span>
      ))}
      {props.problem ? (
        <span className="text-label text-bad" role="status">
          {props.problem}
        </span>
      ) : null}
    </div>
  );
}

/** The file picker behind the paperclip. */
export function PickButton(props: { onPick: (files: File[]) => void; disabled?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp"
        multiple
        hidden
        aria-label="Choose images"
        onChange={(e) => {
          props.onPick([...(e.target.files ?? [])]);
          e.target.value = '';
        }}
      />
      <button
        type="button"
        className="icon compose-attach self-center"
        aria-label="Attach images"
        title="Attach images (or paste, or drop them here)"
        disabled={props.disabled}
        onClick={() => input.current?.click()}
      >
        <Icon name="attach" size={16} label="Attach images" />
      </button>
    </>
  );
}

function Thumb({ a }: { a: Attachment }) {
  const small = useAttachmentUrl(a.id, true);
  const [open, setOpen] = useState(false);
  if (small.gone)
    return (
      <span className="attachment gone muted text-[12px]" title="This image is no longer kept">
        {a.name}
      </span>
    );
  return (
    <>
      <button type="button" className="attachment border-0 bg-transparent p-0" title={a.name} aria-label={`Open ${a.name}`} onClick={() => setOpen(true)}>
        {small.url ? <img src={small.url} alt={a.name} className="max-h-40 max-w-full rounded-chip object-cover" /> : <span className="muted text-[12px]">{a.name}</span>}
      </button>
      {open ? <Original a={a} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function Original({ a, onClose }: { a: Attachment; onClose: () => void }) {
  const full = useAttachmentUrl(a.id, false);
  return (
    <Dialog title={a.name} onClose={onClose}>
      {full.url ? <img src={full.url} alt={a.name} className="max-h-[70vh] max-w-full" /> : <p className="muted">{full.gone ? 'This image is no longer kept.' : 'Loading…'}</p>}
    </Dialog>
  );
}

/** The pictures of a sent message, in the order they were attached. */
export function Pictures({ items }: { items: Attachment[] }) {
  return (
    <div className="attachments mt-1 flex flex-wrap gap-1.5">
      {items.map((a) => (
        <Thumb key={a.id} a={a} />
      ))}
    </div>
  );
}
