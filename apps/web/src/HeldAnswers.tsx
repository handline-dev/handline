// Requests answers that wait before they are sent, so Undo is real.
import { useEffect, useRef, useState } from 'react';
import { Button } from '#src/ui/index.ts';

const HOLD_MS = 5000;

/**
 * Answers that wait HOLD_MS before they are sent, so Undo is real: a held card is off the list at once, and a held answer is sent when the
 * window closes or the page goes away. A refused answer puts its card back.
 */
export function useHeldAnswers() {
  const [away, setAway] = useState<string[]>([]);
  const [last, setLast] = useState<{ key: string; line: string } | null>(null);
  const waiting = useRef(new Map<string, { send: () => Promise<unknown>; timer: ReturnType<typeof setTimeout> }>());
  const back = (key: string) => setAway((a) => a.filter((k) => k !== key));
  const fire = (key: string) => {
    const w = waiting.current.get(key);
    if (!w) return;
    clearTimeout(w.timer);
    waiting.current.delete(key);
    w.send().catch(() => back(key));
  };
  const hold = (key: string, line: string, send: () => Promise<unknown>) => {
    setAway((a) => [...a, key]);
    setLast({ key, line });
    waiting.current.set(key, { send, timer: setTimeout(() => (fire(key), setLast((l) => (l?.key === key ? null : l))), HOLD_MS) });
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
