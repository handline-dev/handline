// "Mark an area to ask": the preview's picture is marked up (Annotate.tsx) and goes to the agent's chat with a question, the plain picture and the marked one in one message.
import { useState } from 'react';
import { keptMarks, markText, type Mark } from '@handline/app-core';
import { classify, sentence, useChatActions, useUploadImage } from '@handline/app-data';
import { Annotator } from '#src/Annotate.tsx';
import { Dialog, NOTE, Textarea } from '#src/ui/index.ts';

export function PreviewAsk({ agent, shot, onClose }: { agent: string; shot: Blob; onClose: () => void }) {
  const chat = useChatActions(agent);
  const upload = useUploadImage();
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
      const plain = await upload(shot, 'preview.png', 'image/png');
      const drawn = kept.length && marked ? await upload(marked, 'preview marked.png', 'image/png') : null;
      chat.send(markText(note, marks), { attachments: drawn ? [plain.id, drawn.id] : [plain.id] });
      onClose();
    } catch (e) {
      setProblem(sentence(classify(e)));
      setSending(false);
    }
  };
  return (
    <Dialog
      title="Mark an area to ask"
      onClose={onClose}
      full
      action={{ label: 'Send', keys: '⌘↵', onClick: () => void send(), disabled: sending }}
      bar={<Textarea rows={1} aria-label="Question" placeholder="What should change here?" className="min-w-0 flex-1" value={note} onChange={(e) => setNote(e.target.value)} />}
    >
      <Annotator image={shot} onChange={(b, m) => (setMarked(b), setMarks(m))} />
      {problem ? (
        <p className={`${NOTE} text-bad`} role="alert">
          {problem}
        </p>
      ) : null}
    </Dialog>
  );
}
