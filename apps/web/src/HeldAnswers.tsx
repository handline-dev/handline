// Requests answers that wait before they are sent, so Undo is real.
import { useEffect, useRef, useState } from 'react';
import { classify, sentence, useWrite } from '@handline/app-data';

const HOLD_MS = 5000;
const STORE = 'handline:held-answers';

/** One daemon write of a held answer, with the request id that makes a second send of it harmless. */
export interface HeldStep {
  method: 'inbox.reply' | 'prompt.answer' | 'task.done' | 'inbox.mark_read';
  params: Record<string, unknown> & { request_id: string };
}

/** The answers not yet confirmed by the daemon, kept so a page that goes away within the hold still sends them on its next start. */
const stored = (): Record<string, HeldStep[]> => {
  try {
    return JSON.parse(localStorage.getItem(STORE) ?? '{}') as Record<string, HeldStep[]>;
  } catch {
    return {};
  }
};
const keep = (change: (all: Record<string, HeldStep[]>) => void) => {
  try {
    const all = stored();
    change(all);
    localStorage.setItem(STORE, JSON.stringify(all));
  } catch {
    // Storage blocked: the hold still works for as long as the page lives.
  }
};

/**
 * Answers that wait HOLD_MS before they are sent, so Undo is real: a held card is off the list at once, and a held answer is sent when the
 * window closes or the page goes away. The toast says what happened once the daemon has answered; a refused answer puts its card back and says why.
 */
export function useHeldAnswers(say: (line: string) => void) {
  const [away, setAway] = useState<string[]>([]);
  const [last, setLast] = useState<{ key: string; line: string } | null>(null);
  const writer = {
    'inbox.reply': useWrite('inbox.reply'),
    'prompt.answer': useWrite('prompt.answer'),
    'task.done': useWrite('task.done'),
    'inbox.mark_read': useWrite('inbox.mark_read'),
  };
  const waiting = useRef(new Map<string, { send: () => Promise<unknown>; done: string; timer: ReturnType<typeof setTimeout> }>());
  const back = (key: string) => setAway((a) => a.filter((k) => k !== key));
  const fire = (key: string) => {
    const w = waiting.current.get(key);
    if (!w) return;
    clearTimeout(w.timer);
    waiting.current.delete(key);
    setLast((l) => (l?.key === key ? null : l));
    w.send().then(
      () => (keep((all) => delete all[key]), say(w.done)),
      (e: unknown) => (keep((all) => delete all[key]), back(key), say(sentence(classify(e)))),
    );
  };
  /** `steps` are what `send` writes; they are kept until the daemon has answered, so a reload inside the hold loses nothing. */
  const hold = (key: string, done: string, steps: HeldStep[], send: () => Promise<unknown>) => {
    keep((all) => (all[key] = steps));
    setAway((a) => [...a, key]);
    setLast({ key, line: 'Sends in 5 s' });
    waiting.current.set(key, { send, done, timer: setTimeout(() => fire(key), HOLD_MS) });
  };
  const undo = (key: string) => {
    const w = waiting.current.get(key);
    if (w) clearTimeout(w.timer);
    waiting.current.delete(key);
    keep((all) => delete all[key]);
    back(key);
    setLast(null);
  };
  useEffect(() => {
    // Answers a page left unsent: send them now, the same ids as before, so one the daemon already took is not done twice.
    const left = Object.entries(stored()).filter(([key]) => !waiting.current.has(key));
    for (const [key, steps] of left) {
      const sent = steps.reduce(
        (p, st) => p.then(() => writer[st.method].mutateAsync(st.params as never)),
        Promise.resolve<unknown>(undefined),
      );
      void sent.catch((e: unknown) => say(sentence(classify(e)))).then(() => keep((all) => delete all[key]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    const flush = () => [...waiting.current.keys()].forEach(fire);
    addEventListener('pagehide', flush);
    return () => (removeEventListener('pagehide', flush), flush());
    // `fire` reads only refs and a setter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return { away, last, hold, undo };
}
