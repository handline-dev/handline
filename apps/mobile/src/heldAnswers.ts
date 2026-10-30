// Requests answers that wait before they are sent, so Undo is real: a held card is off the list at once, and a held answer is sent when the
// five seconds are over or the app goes to the background. A refused answer puts its card back and says why.
import { useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import type { CallParams } from '@handline/client';
import { classify, sentence, useWrite } from '@handline/app-data';

export const HOLD_MS = 5000;

/** One daemon write of a held answer. */
export type Step =
  | { method: 'inbox.reply'; params: CallParams<'inbox.reply'> }
  | { method: 'prompt.answer'; params: CallParams<'prompt.answer'> }
  | { method: 'task.done'; params: CallParams<'task.done'> }
  | { method: 'inbox.mark_read'; params: CallParams<'inbox.mark_read'> };

export interface Held {
  /** Keys of the cards held back: off the list and off every count until sent or undone. */
  away: string[];
  /** The newest held answer, for the Undo line. */
  last: { key: string; line: string } | null;
  hold: (key: string, done: string, steps: Step[]) => void;
  undo: (key: string) => void;
}

export function useHeldAnswers(say: (line: string) => void): Held {
  const [away, setAway] = useState<string[]>([]);
  const [last, setLast] = useState<{ key: string; line: string } | null>(null);
  const reply = useWrite('inbox.reply');
  const prompt = useWrite('prompt.answer');
  const done = useWrite('task.done');
  const read = useWrite('inbox.mark_read');
  const write = (s: Step): Promise<unknown> => {
    switch (s.method) {
      case 'inbox.reply':
        return reply.mutateAsync(s.params);
      case 'prompt.answer':
        return prompt.mutateAsync(s.params);
      case 'task.done':
        return done.mutateAsync(s.params);
      case 'inbox.mark_read':
        return read.mutateAsync(s.params);
    }
  };
  const waiting = useRef(new Map<string, { steps: Step[]; said: string; timer: ReturnType<typeof setTimeout> }>());
  const back = (key: string) => setAway((a) => a.filter((k) => k !== key));
  const fire = (key: string) => {
    const w = waiting.current.get(key);
    if (!w) return;
    clearTimeout(w.timer);
    waiting.current.delete(key);
    setLast((l) => (l?.key === key ? null : l));
    w.steps
      .reduce((p, s) => p.then(() => write(s)), Promise.resolve<unknown>(undefined))
      .then(
        () => say(w.said),
        (e: unknown) => (back(key), say(sentence(classify(e)))),
      );
  };
  const hold = (key: string, said: string, steps: Step[]) => {
    setAway((a) => [...a, key]);
    setLast({ key, line: 'Sends in 5 s' });
    waiting.current.set(key, { steps, said, timer: setTimeout(() => fire(key), HOLD_MS) });
  };
  const undo = (key: string) => {
    const w = waiting.current.get(key);
    if (w) clearTimeout(w.timer);
    waiting.current.delete(key);
    back(key);
    setLast(null);
  };
  useEffect(() => {
    const flush = () => [...waiting.current.keys()].forEach(fire);
    const sub = AppState.addEventListener('change', (s) => s === 'background' && flush());
    return () => (sub.remove(), flush());
    // `fire` reads only refs and setters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return { away, last, hold, undo };
}
