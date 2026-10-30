// Where notes marked on a photo go: an agent's chat or a task. The viewer offers marking only under one of these.
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { withImages } from '@handline/app-core';
import { useChatActions, useUploadImage, useWrite } from '@handline/app-data';

export interface NotesPlace {
  /** The button words: "Send 2 notes to sigma", "Add 2 notes". */
  button: (n: number) => string;
  send: (text: string, image: Blob | null, name: string) => Promise<void>;
}

const Place = createContext<NotesPlace | null>(null);
export const useNotesPlace = () => useContext(Place);

const plural = (n: number) => `${n} ${n === 1 ? 'note' : 'notes'}`;

/** The words of the message: what the photo is, then one numbered line per note. */
export const notesText = (name: string, notes: string[]) =>
  [`${notes.length === 1 ? 'One change' : `${notes.length} changes`} on ${name}`, ...notes.map((t, i) => `${i + 1}. ${t}`)].join('\n');

export function ChatNotes({ agent, children }: { agent: string; children: ReactNode }) {
  const act = useChatActions(agent);
  const upload = useUploadImage();
  const place = useMemo<NotesPlace>(
    () => ({
      button: (n) => `Send ${plural(n)} to ${agent}`,
      send: async (text, image, name) => {
        const sent = image ? await upload(image, `${name} marked.png`, 'image/png') : null;
        act.send(text, sent ? { attachments: [sent.id] } : undefined);
      },
    }),
    // `act` is a new object each render; what it sends with does not change
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [agent, upload],
  );
  return <Place.Provider value={place}>{children}</Place.Provider>;
}

export function TaskNotes({ task, children }: { task: number; children: ReactNode }) {
  const comment = useWrite('task.comment.add', String(task));
  const upload = useUploadImage();
  const place = useMemo<NotesPlace>(
    () => ({
      button: (n) => `Add ${plural(n)} to T${task}`,
      send: async (text, image, name) => {
        const sent = image ? await upload(image, `${name} marked.png`, 'image/png') : null;
        await comment.mutateAsync({ task, text: withImages(text, sent ? [sent.id] : [], name) });
      },
    }),
    // `comment` is a new object each render, like `act` above
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [task, upload],
  );
  return <Place.Provider value={place}>{children}</Place.Provider>;
}
