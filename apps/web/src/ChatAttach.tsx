// Images on a message: the chips under the box while they upload (a preview, the progress, a remove mark) and the pictures drawn in a sent
// message (a small copy; a click opens the original). The files come from a paste, a drop or the file picker.
import { useRef, useState, type ClipboardEvent, type DragEvent } from 'react';
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
import { shell } from '#src/shell.ts';
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

/** A file or folder dropped from Finder: the message carries its absolute path, so the agent can read it. */
export interface Ref {
  key: number;
  name: string;
  path: string;
}

let nextKey = 1;

interface Held {
  drafts: Draft[];
  problem: string | null;
  refs: Ref[];
  live: Map<number, { aborted: boolean; preview: string }>;
}
const useHeld = keptState<Held>(() => ({ drafts: [], problem: null, refs: [], live: new Map() }));

/** The images being attached to the next message: each is sent to the daemon as soon as it is added. Kept per agent, so they are still there when the person comes back to the chat. */
export function useAttachDraft(agent?: string) {
  const upload = useUploadImage();
  const held = useHeld(agent);
  const { drafts, problem, refs } = held.state;
  const live = held.get().live;
  const patch = (key: number, p: Partial<Draft>) =>
    held.set((s) => ({ drafts: s.drafts.map((x) => (x.key === key ? { ...x, ...p } : x)) }));

  const add = (files: File[]) => {
    held.set({ problem: null });
    for (const f of files) {
      const why = whyNotImage(f);
      if (why) return held.set({ problem: why });
      if (live.size >= MAX_PER_MESSAGE) return held.set({ problem: `At most ${MAX_PER_MESSAGE} images to a message` });
      const key = nextKey++;
      const preview = URL.createObjectURL(f);
      const signal = { aborted: false, preview };
      live.set(key, signal);
      held.set((s) => ({ drafts: [...s.drafts, { key, name: f.name || 'image', preview, progress: 0 }] }));
      upload(f, f.name || 'image', f.type as Attachment['mime'], (sent, total) => patch(key, { progress: sent / total }), signal).then(
        (attachment) => !signal.aborted && (rememberAttachment(attachment), patch(key, { attachment, progress: 1 })),
        (e) => !signal.aborted && patch(key, { error: sentence(classify(e)) }),
      );
    }
  };
  // Only the desktop shell knows a dropped file's path; elsewhere nothing is added. The shell copies it into the agent's worktree, so the
  // path the message carries is one the agent can read; when it cannot copy, the original path is sent as before.
  const refer = async (files: File[]) => {
    const api = shell('pathOf');
    if (!api) return;
    const bring = shell('bringIn')?.bringIn;
    const found = files.flatMap((f) => {
      const path = api.pathOf!(f);
      return path ? [{ name: f.name || path.split('/').pop() || path, path }] : [];
    });
    const added = await Promise.all(
      found.map(async (f) => {
        const copy = agent && bring ? await bring({ path: f.path, agent }) : null;
        return { key: nextKey++, name: f.name, path: copy?.ok ? copy.path : f.path };
      }),
    );
    held.set((s) => ({
      refs: [
        ...s.refs,
        ...added.filter((a, i) => !s.refs.some((x) => x.path === a.path) && added.findIndex((b) => b.path === a.path) === i),
      ],
    }));
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
    held.set({ drafts: [], refs: [], problem: null });
  };
  return {
    drafts,
    refs,
    problem,
    add,
    refer,
    unrefer: (key: number) => held.set((s) => ({ refs: s.refs.filter((x) => x.key !== key) })),
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
export function attachHandlers(add: (files: File[]) => void, refer?: (files: File[]) => void | Promise<void>) {
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
      const all = Array.from(e.dataTransfer.files);
      const files = imagesOf(e.dataTransfer.files);
      // What is not an image (a document, a folder) is a reference by path, in the desktop shell.
      const rest = refer ? all.filter((f) => !f.type.startsWith('image/')) : [];
      if (!files.length && !rest.length) return;
      e.preventDefault();
      if (files.length) add(files);
      if (rest.length) void refer!(rest);
    },
  };
}

export function AttachChips(props: {
  drafts: Draft[];
  refs?: Ref[];
  problem: string | null;
  onRemove: (key: number) => void;
  onUnref?: (key: number) => void;
}) {
  const [viewing, setViewing] = useState<number | null>(null);
  if (!props.drafts.length && !props.refs?.length && !props.problem) return null;
  return (
    <div className="attach-chips mx-3 mb-1 flex flex-wrap items-center gap-1.5">
      {props.drafts.map((d) => (
        <span
          key={d.key}
          className="attach-chip relative inline-flex items-center gap-1.5 rounded-chip bg-panel py-0.5 pl-0.5 pr-1.5 text-[12px]"
          data-state={d.error ? 'failed' : d.attachment ? 'ready' : 'sending'}
          title={d.error ?? d.name}
        >
          <button
            type="button"
            className="border-0 bg-transparent p-0"
            aria-label={`Open ${d.name}`}
            title="Enlarge"
            onClick={() => setViewing(props.drafts.indexOf(d))}
          >
            <img src={d.preview} alt="" className="h-9 w-9 rounded-chip object-cover" />
          </button>
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
      {props.refs?.map((r) => (
        <span
          key={r.key}
          className="attach-chip inline-flex items-center gap-1.5 rounded-chip bg-panel px-1.5 py-0.5 text-[12px]"
          data-state="ref"
          title={r.path}
        >
          <Icon name="attach" size={13} label="Path" />
          <span className="max-w-[180px] truncate">{r.name}</span>
          <button className="icon" aria-label={`Remove ${r.name}`} title="Remove" onClick={() => props.onUnref?.(r.key)}>
            <Icon name="close" size={12} label="Remove" />
          </button>
        </span>
      ))}
      {props.problem ? (
        <span className="text-label text-bad" role="status">
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

/** The pictures of a sent message, in the order they were attached. */
export function Pictures({ items }: { items: Pick<Attachment, 'id' | 'name'>[] }) {
  return (
    <div className="attachments mt-1 flex flex-wrap gap-1.5">
      {items.map((a, i) => (
        <AttachmentImage key={a.id} id={a.id} name={a.name} group={items} at={i} />
      ))}
    </div>
  );
}
