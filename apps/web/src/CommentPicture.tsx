// "Add picture" in the comment composer: pick an image, click or drag on it to mark a spot (Annotate.tsx), and the marked picture goes into the comment.
import { useRef, useState } from 'react';
import { keptMarks, markText, withImages, type Mark } from '@handline/app-core';
import { classify, sentence, useUploadImage, whyNotImage } from '@handline/app-data';
import { Annotator } from '#src/Annotate.tsx';
import { Button, Dialog } from '#src/ui/index.ts';

export function CommentPicture({ onAttach }: { onAttach: (markdown: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const upload = useUploadImage();
  const [shot, setShot] = useState<File | null>(null);
  const [marked, setMarked] = useState<Blob | null>(null);
  const [marks, setMarks] = useState<Mark[]>([]);
  const [problem, setProblem] = useState('');
  const [sending, setSending] = useState(false);
  const close = () => (setShot(null), setMarked(null), setMarks([]), setProblem(''), setSending(false));

  const pick = (f: File | undefined) => {
    if (input.current) input.current.value = '';
    if (!f) return;
    const why = whyNotImage(f);
    if (why) return setProblem(why);
    setProblem('');
    setShot(f);
  };
  const send = async () => {
    if (!shot) return;
    setSending(true);
    try {
      const kept = keptMarks(marks);
      const name = shot.name || 'image';
      const alt = name.replace(/\.[^.]*$/, '');
      const body = kept.length && marked ? marked : shot;
      const a = await upload(body, name, body.type as 'image/png');
      onAttach(withImages(markText('', marks), [a.id], alt));
      close();
    } catch (e) {
      setProblem(sentence(classify(e)));
      setSending(false);
    }
  };
  return (
    <>
      <input ref={input} type="file" accept="image/*" hidden aria-label="Picture file" onChange={(e) => pick(e.target.files?.[0])} />
      <Button label="Add picture" icon="attach" onClick={() => input.current?.click()}>
        Add picture
      </Button>
      {problem && !shot ? (
        <span className="text-hint text-bad" role="alert">
          {problem}
        </span>
      ) : null}
      {shot ? (
        <Dialog
          title="Mark the picture"
          onClose={close}
          full
          action={{ label: 'Attach', keys: '⌘↵', onClick: () => void send(), disabled: sending }}
        >
          <Annotator image={shot} onChange={(b, m) => (setMarked(b), setMarks(m))} />
          {problem ? (
            <p className="text-bad" role="alert">
              {problem}
            </p>
          ) : null}
        </Dialog>
      ) : null}
    </>
  );
}
