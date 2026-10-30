// Requests answers that wait before they are sent, so Undo is real.
import { useEffect, useRef, useState } from 'react';
import { useStore } from '@handline/app-react';
import { Button } from '#src/ui/index.ts';

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
 * window closes or the page goes away. A refused answer puts its card back.
 */
export function useHeldAnswers() {
  const [away, setAway] = useState<string[]>([]);
  const [last, setLast] = useState<{ key: string; line: string } | null>(null);
  const store = useStore();
  const waiting = useRef(new Map<string, { send: () => Promise<unknown>; timer: ReturnType<typeof setTimeout> }>());
  const back = (key: string) => setAway((a) => a.filter((k) => k !== key));
  const fire = (key: string) => {
    const w = waiting.current.get(key);
    if (!w) return;
    clearTimeout(w.timer);
    waiting.current.delete(key);
    w.send().then(
      () => keep((all) => delete all[key]),
      () => (keep((all) => delete all[key]), back(key)),
    );
  };
  /** `steps` are what `send` writes; they are kept until the daemon has answered, so a reload inside the hold loses nothing. */
  const hold = (key: string, line: string, steps: HeldStep[], send: () => Promise<unknown>) => {
    keep((all) => (all[key] = steps));
    setAway((a) => [...a, key]);
    setLast({ key, line });
    waiting.current.set(key, { send, timer: setTimeout(() => (fire(key), setLast((l) => (l?.key === key ? null : l))), HOLD_MS) });
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
        (p, st) => p.then(() => store.client.call(st.method, st.params as never)),
        Promise.resolve<unknown>(undefined),
      );
      void sent.catch(() => undefined).then(() => keep((all) => delete all[key]));
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

/** The toast line of the answer just held, with its Undo. */
export function HeldLine({ last, undo }: { last: { key: string; line: string }; undo: (key: string) => void }) {
  return (
    <>
      {last.line}{' '}
      <span className="pointer-events-auto">
        <Button variant="ghost" size="sm" icon="undo" label="Undo" onClick={() => undo(last.key)}>
          Undo
        </Button>
      </span>
    </>
  );
}
