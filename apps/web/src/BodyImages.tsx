// Paste or drop an image into a task body: it is uploaded and `![name](attachment:<id>)` goes in at the cursor (docs/story1-task-images-graph.md A).
import { useState, type ClipboardEvent, type DragEvent } from 'react';
import { imageRef } from '@handline/app-core';
import { classify, sentence, useUploadImage, whyNotImage } from '@handline/app-data';

type Box = HTMLTextAreaElement;
const imagesOf = (list: FileList | null | undefined): File[] => Array.from(list ?? []).filter((f) => f.type.startsWith('image/'));

/** Handlers for the body's textarea, and what to show: `busy` while an upload runs, `problem` when one was refused or failed. */
export function useBodyImages(setBody: (update: (body: string) => string) => void) {
  const upload = useUploadImage();
  const [busy, setBusy] = useState(0);
  const [problem, setProblem] = useState('');
  const take = (files: File[], box: Box) => {
    setProblem('');
    // The cursor is read now: the person may move it while the upload runs.
    const at = box.selectionStart;
    for (const f of files) {
      const why = whyNotImage(f);
      if (why) return setProblem(why);
      const name = f.name || 'image';
      setBusy((n) => n + 1);
      upload(f, name, f.type as never)
        .then(
          (a) => setBody((b) => `${b.slice(0, at)}${imageRef(a.id, name.replace(/\.[^.]*$/, ''))}${b.slice(at)}`),
          (e) => setProblem(sentence(classify(e))),
        )
        .finally(() => setBusy((n) => n - 1));
    }
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
