// Paste or drop an image into a task body: it is uploaded and `![name](attachment:<id>)` goes in at the cursor (docs/story1-task-images-graph.md A).
import { useState, type ClipboardEvent, type DragEvent } from 'react';
import type { Attachment } from '@handline/client';
import { imageRef } from '@handline/app-core';
import { classify, sentence, useUploadImage, whyNotImage } from '@handline/app-data';

type Box = HTMLTextAreaElement;
const imagesOf = (list: FileList | null | undefined): File[] => Array.from(list ?? []).filter((f) => f.type.startsWith('image/'));

/** Handlers for the body's textarea, and what to show: `busy` while an upload runs, `problem` when one was refused or failed. */
export function useBodyImages(setBody: (update: (body: string) => string) => void) {
  const upload = useUploadImage();
  const [busy, setBusy] = useState(0);
  const [problem, setProblem] = useState('');
  const take = async (files: File[], box: Box) => {
    setProblem('');
    setBusy((n) => n + 1);
    // Uploads run side by side; the refs go in together, in file order, where the cursor is when the last one ends.
    const done = await Promise.allSettled(
      files.map(async (f) => {
        const why = whyNotImage(f);
        if (why) throw new Error(why);
        const name = f.name || 'image';
        const a = await upload(f, name, f.type as Attachment['mime']);
        return imageRef(a.id, name.replace(/\.[^.]*$/, ''));
      }),
    );
    const first = done.find((r) => r.status === 'rejected');
    if (first) setProblem(sentence(classify(first.reason)));
    const refs = done.flatMap((r) => (r.status === 'fulfilled' ? r.value : []));
    const at = box.selectionStart;
    if (refs.length) setBody((b) => `${b.slice(0, at)}${refs.join('\n')}${b.slice(at)}`);
    setBusy((n) => n - 1);
  };
  return {
    busy: busy > 0,
    problem,
    handlers: {
      onPaste: (e: ClipboardEvent<Box>) => {
        const files = imagesOf(e.clipboardData.files);
        if (!files.length) return;
        e.preventDefault();
        take(files, e.currentTarget);
      },
      onDragOver: (e: DragEvent<Box>) => {
        if ([...e.dataTransfer.types].includes('Files')) e.preventDefault();
      },
      onDrop: (e: DragEvent<Box>) => {
        const files = imagesOf(e.dataTransfer.files);
        if (!files.length) return;
        e.preventDefault();
        take(files, e.currentTarget);
      },
    },
  };
}
