// A review card on the Inbox: the pictures of the task's first comment as thumbnails, and "Ask for change", a box with the first picture to mark
// up and the words of what should change. Approve needs no box and lives in MyTurn.
import { useEffect, useState } from 'react';
import { changeAsked, keptMarks, markText, withImages, type Mark } from '@handline/app-core';
import { classify, sentence, useAttachmentUrl, useReviewPictures, useUploadImage, useWrite } from '@handline/app-data';
import { Annotator } from '#src/Annotate.tsx';
import { Dialog, NOTE, Textarea } from '#src/ui/index.ts';

function Thumb({ id, name }: { id: string; name: string }) {
  const { url } = useAttachmentUrl(id, true);
  return url ? <img src={url} alt={name} className="h-16 w-auto max-w-[160px] rounded-card border border-line object-cover" /> : <span className="muted text-hint">{name}</span>;
}

export function ReviewPictures({ task }: { task: number }) {
  const pictures = useReviewPictures(task);
  if (!pictures.length) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-2" aria-label="Pictures to review">
      {pictures.map((p) => (
        <Thumb key={p.id} {...p} />
      ))}
    </div>
  );
}

/** The picture as a Blob to draw on; null while it loads or when there is none. */
function usePicture(id: string | undefined) {
  const { url } = useAttachmentUrl(id ?? '', false);
  const [blob, setBlob] = useState<Blob | null>(null);
  useEffect(() => {
    setBlob(null);
    if (!id || !url) return;
    let live = true;
    void fetch(url)
      .then((r) => r.blob())
      .then((b) => live && setBlob(b));
    return () => {
      live = false;
    };
  }, [id, url]);
  return blob;
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
      bar={<Textarea rows={1} autoFocus aria-label="What should change" placeholder="What should change?" className="min-w-0 flex-1" value={note} onChange={(e) => setNote(e.target.value)} />}
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
