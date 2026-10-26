// The images being attached to the next text, for any app: each is uploaded as soon as it is added, a failed one keeps its place with Retry, and
// the text waits while one is still going. The app brings the picture ready (picked, shrunk, a Blob and a preview address).
import type { Attachment } from '@handline/client';
import { classify, sentence } from '#src/errors.ts';
import { keptState } from '#src/kept.ts';
import { MAX_PER_MESSAGE, forgetAttachment, rememberAttachment, useUploadImage, whyNotImage } from '#src/attachments.ts';

export interface ImageSource {
  blob: Blob;
  name: string;
  mime: string;
  /** An address the app can draw the picture from while it uploads. */
  preview: string;
}

export interface ImageDraft {
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
  drafts: ImageDraft[];
  problem: string | null;
  live: Map<number, { aborted: boolean; src: ImageSource }>;
}
const useHeld = keptState<Held>(() => ({ drafts: [], problem: null, live: new Map() }));

/** `agent`: the drafts are kept for that agent, so they are still there when the person returns to it; without it they go with the component. */
export function useImageDrafts(max = MAX_PER_MESSAGE, agent?: string) {
  const upload = useUploadImage();
  const held = useHeld(agent);
  const { drafts, problem } = held.state;
  const live = held.get().live;
  const patch = (key: number, p: Partial<ImageDraft>) =>
    held.set((s) => ({ drafts: s.drafts.map((x) => (x.key === key ? { ...x, ...p } : x)) }));

  const start = (key: number) => {
    const l = live.get(key);
    if (!l) return;
    l.aborted = false;
    const { src } = l;
    patch(key, { progress: 0, error: undefined });
    upload(src.blob, src.name, src.mime as Attachment['mime'], (sent, total) => patch(key, { progress: sent / total }), l).then(
      (attachment) => !l.aborted && (rememberAttachment(attachment), patch(key, { attachment, progress: 1 })),
      (e) => !l.aborted && patch(key, { error: sentence(classify(e)) }),
    );
  };

  const add = (sources: ImageSource[]) => {
    held.set({ problem: null });
    for (const src of sources) {
      const why = whyNotImage({ type: src.mime, size: src.blob.size, name: src.name });
      if (why) return held.set({ problem: why });
      if (live.size >= max) return held.set({ problem: `At most ${max} images to a message` });
      const key = nextKey++;
      live.set(key, { aborted: false, src });
      held.set((s) => ({ drafts: [...s.drafts, { key, name: src.name, preview: src.preview, progress: 0 }] }));
      start(key);
    }
  };
  const retry = (key: number) => start(key);
  const remove = (key: number) => {
    const l = live.get(key);
    if (!l) return;
    l.aborted = true;
    live.delete(key);
    const gone = held.get().drafts.find((x) => x.key === key)?.attachment;
    if (gone) forgetAttachment(gone.id);
    held.set((s) => ({ drafts: s.drafts.filter((x) => x.key !== key) }));
  };
  const clear = () => {
    for (const l of live.values()) l.aborted = true;
    live.clear();
    held.set({ drafts: [], problem: null });
  };
  return {
    drafts,
    problem,
    add,
    retry,
    remove,
    clear,
    /** Uploaded in full: the ids to name in the text. */
    ids: drafts.flatMap((d) => d.attachment?.id ?? []),
    /** Still going, or failed: the text waits. */
    busy: drafts.some((d) => !d.attachment),
  };
}
