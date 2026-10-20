// The images being attached to the next text, for any app: each is uploaded as soon as it is added, a failed one keeps its place with Retry, and
// the text waits while one is still going. The app brings the picture ready (picked, shrunk, a Blob and a preview address).
import { useRef, useState } from 'react';
import type { Attachment } from '@handline/client';
import { classify, sentence } from '#src/errors.ts';
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

export function useImageDrafts(max = MAX_PER_MESSAGE) {
  const upload = useUploadImage();
  const [drafts, setDrafts] = useState<ImageDraft[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const live = useRef(new Map<number, { aborted: boolean; src: ImageSource }>());
  const patch = (key: number, p: Partial<ImageDraft>) => setDrafts((d) => d.map((x) => (x.key === key ? { ...x, ...p } : x)));

  const start = (key: number) => {
    const l = live.current.get(key);
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
    setProblem(null);
    for (const src of sources) {
      const why = whyNotImage({ type: src.mime, size: src.blob.size, name: src.name });
      if (why) return setProblem(why);
      if (live.current.size >= max) return setProblem(`At most ${max} images to a message`);
      const key = nextKey++;
      live.current.set(key, { aborted: false, src });
      setDrafts((d) => [...d, { key, name: src.name, preview: src.preview, progress: 0 }]);
      start(key);
    }
  };
  const retry = (key: number) => start(key);
  const remove = (key: number) => {
    const l = live.current.get(key);
    if (!l) return;
    l.aborted = true;
    live.current.delete(key);
    const gone = drafts.find((x) => x.key === key)?.attachment;
    if (gone) forgetAttachment(gone.id);
    setDrafts((d) => d.filter((x) => x.key !== key));
  };
  const clear = () => {
    for (const l of live.current.values()) l.aborted = true;
    live.current.clear();
    setDrafts([]);
    setProblem(null);
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
