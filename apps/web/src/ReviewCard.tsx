// A review card on the Inbox: the pictures of the task's first comment as thumbnails, and "Ask for change", a box with the first picture to mark
// up and the words of what should change. Approve needs no box and lives in MyTurn.
import { useState } from 'react';
import { changeAsked, keptMarks, markText, withImages, type Mark } from '@handline/app-core';
import { classify, sentence, useAttachmentUrl, useLoaded, useReviewPictures, useUploadImage, useWrite } from '@handline/app-data';
import { ImageViewer } from '#src/ImageViewer.tsx';
import { Annotator } from '#src/Annotate.tsx';
import { Dialog, NOTE, Textarea } from '#src/ui/index.ts';

function Thumb({ id, name, onOpen }: { id: string; name: string; onOpen: () => void }) {
  const { url } = useAttachmentUrl(id, true);
  return url ? (
    <button type="button" className="border-0 bg-transparent p-0" aria-label={`Open ${name}`} title={name} onClick={onOpen}>
      <img src={url} alt={name} className="h-16 w-auto max-w-[160px] rounded-card border border-line object-cover" />
    </button>
  ) : (
    <span className="muted text-hint">{name}</span>
  );
}

export function ReviewPictures({ task }: { task: number }) {
  const pictures = useReviewPictures(task);
  const [viewing, setViewing] = useState<number | null>(null);
  if (!pictures.length) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-2" aria-label="Pictures to review">
      {pictures.map((p, i) => (
        <Thumb key={p.id} {...p} onOpen={() => setViewing(i)} />
      ))}
      {viewing !== null ? <ImageViewer pictures={pictures} start={viewing} onClose={() => setViewing(null)} /> : null}
    </div>
  );
}

/** The picture as a Blob to draw on; null while it loads or when there is none. */
function usePicture(id: string | undefined) {
  const { url } = useAttachmentUrl(id ?? '', false);
  return useLoaded(['picture', id, url], async () => (await fetch(url!)).blob(), !!id && !!url) ?? null;
}

export function AskChange({ task, item, onClose }: { task: number; item: number; onClose: () => void }) {
  const comment = useWrite('task.comment.add', String(task));
  const done = useWrite('task.done');
  const read = useWrite('inbox.mark_read');
  const upload = useUploadImage();
  const shot = usePicture(useReviewPictures(task)[0]?.id);
  const [marked, setMarked] = useState<Blob | null>(null);
  const [marks, setMarks] = useState<Mark[]>([]);
  const [note, setNote] = useState('');
  const [problem, setProblem] = useState('');
  const [sending, setSending] = useState(false);
  const send = async () => {
    setSending(true);
    setProblem('');
    try {
      const kept = keptMarks(marks);
      const drawn = kept.length && marked ? await upload(marked, 'drawing.png', 'image/png') : null;
      await comment.mutateAsync({ task, text: withImages(markText(note, marks), drawn ? [drawn.id] : [], 'drawing') });
      await done.mutateAsync({ task, evidence: changeAsked(note), reason: true });
      read.mutate({ ids: [item] });
      onClose();
    } catch (e) {
      setProblem(sentence(classify(e)));
      setSending(false);
    }
  };
  return (
    <Dialog
      title="Ask for change"
      onClose={onClose}
      full
      action={{ label: 'Send', keys: '⌘↵', onClick: () => void send(), disabled: sending || !note.trim() }}
      bar={
        <Textarea
          rows={1}
          autoFocus
          aria-label="What should change"
          placeholder="What should change?"
          className="min-w-0 flex-1"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      }
    >
      {shot ? <Annotator image={shot} onChange={(b, m) => (setMarked(b), setMarks(m))} /> : null}
      {problem ? (
        <p className={`${NOTE} text-bad`} role="alert">
          {problem}
        </p>
      ) : null}
    </Dialog>
  );
}
