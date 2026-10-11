// Small pieces the Tasks list and the task detail share: the state chip, the clock, the width query and the inline reason sheet.
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useServes } from '@handline/app-data';
import { Icon, type IconName } from './Icon.tsx';
import { Button, Input, Sheet as SheetBox, SheetActions, VisuallyHidden } from './ui/index.ts';

export const STATE_GLYPH: Record<string, IconName> = {
  draft: 'taskDraft',
  queued: 'taskQueued',
  pending: 'taskPending',
  doing: 'taskDoing',
  blocked: 'taskBlocked',
  done: 'taskDone',
  discarded: 'taskDiscarded',
};

/** The chip look per state; the class is the hook, the utilities the look. `run` also spins the doing glyph (tasks.css). */
const TONE: Record<string, string> = {
  doing: 'run text-working',
  queued: 'wait text-wait',
  pending: 'wait text-wait',
  draft: 'idle',
  blocked: 'block text-bad',
  done: 'idle',
  discarded: 'idle',
};
const CHIP = 'inline-flex items-center gap-0.5 align-middle text-ink-2 [&>svg]:shrink-0';

/** A state is a shape of its own (the glyph), then a tooltip, then colour; the word stays for a screen reader. `mark` says what the write in flight is: hollow dot, or the decided "…". */
export function StateChip({ state, mark }: { state: string; mark?: 'authored' | 'decided' | null }) {
  if (mark === 'decided')
    return (
      <span className={`tchip pending ${CHIP}`} title="waiting for the daemon">
        …
      </span>
    );
  return (
    <span className={`tchip ${TONE[state] ?? 'idle'} data-[state=done]:text-ok ${CHIP}`} data-state={state} title={state}>
      {mark === 'authored' ? <span aria-hidden="true">○</span> : null}
      <Icon
        name={STATE_GLYPH[state] ?? 'taskPending'}
        label={state}
        size={16}
        aria-hidden="true"
        className={state === 'doing' ? 'animate-[ui-spin_1.2s_linear_infinite] motion-reduce:animate-none' : undefined}
      />
      <VisuallyHidden>{state}</VisuallyHidden>
    </span>
  );
}

/** The clock for idle and timeout words: it moves every `every` ms, so an open page keeps them true. */
export function useNow(every = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), every);
    return () => clearInterval(t);
  }, [every]);
  return now;
}

/** True when the window is at least `px` wide (the bridge's `TaskPane.fit`: the detail goes beside the table from 1100). */
export function useWide(px: number): boolean {
  const query = `(min-width: ${px}px)`;
  const [wide, setWide] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const m = matchMedia(query);
    const on = () => setWide(m.matches);
    on();
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, [query]);
  return wide;
}

/** Whether the daemon lists the method; a control for one it does not is shown disabled and says why. */
export function useCan(method: string): { ok: boolean; why: string | undefined } {
  const { served, open } = useServes(method);
  if (!open) return { ok: false, why: 'daemon not connected' };
  return served ? { ok: true, why: undefined } : { ok: false, why: `This daemon does not serve ${method} yet` };
}

/** A small inline sheet under the row or the bar, not a modal: it names the thing, takes Enter and Escape. */
export function Sheet(props: {
  label: string;
  children: ReactNode;
  onCancel: () => void;
  onSubmit: () => void;
  submit: string;
  busy?: boolean;
  /** Fills the screen at phone width (the new task form). */
  full?: boolean;
}) {
  const ref = useRef<HTMLFormElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>('input,select,textarea,button')?.focus();
  }, []);
  return (
    <SheetBox
      as="form"
      kind="form"
      ref={ref}
      className={props.full ? 'full max-sm:fixed max-sm:inset-0 max-sm:z-20 max-sm:m-0 max-sm:p-6 max-sm:rounded-none max-sm:overflow-auto max-sm:content-start' : undefined}
      role="group"
      aria-label={props.label}
      onSubmit={(e) => {
        e.preventDefault();
        props.onSubmit();
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          props.onCancel();
        }
      }}
    >
      <strong className="tsheet-label">{props.label}</strong>
      {props.children}
      <SheetActions kind="form">
        <Button type="submit" variant="solid" label={props.submit} disabled={props.busy}>
          {props.busy ? '…' : props.submit}
        </Button>
        <Button variant="outline" label="Cancel" onClick={props.onCancel} />
      </SheetActions>
    </SheetBox>
  );
}

/** A sheet that asks for a required sentence (the discard, done and reopen reasons): an empty one keeps the sheet open. */
export function ReasonSheet(props: { label: string; submit: string; onCancel: () => void; onSubmit: (reason: string) => void }) {
  const [text, setText] = useState('');
  const [empty, setEmpty] = useState(false);
  return (
    <Sheet
      label={props.label}
      submit={props.submit}
      onCancel={props.onCancel}
      onSubmit={() => (text.trim() ? props.onSubmit(text.trim()) : setEmpty(true))}
    >
      <Input
        grow
        className="tinput"
        value={text}
        aria-label={props.label}
        aria-invalid={empty && !text.trim()}
        onChange={(e) => {
          setText(e.target.value);
          setEmpty(false);
        }}
      />
      {empty ? (
        <span className="tsheet-err basis-full text-bad" role="alert">
          A reason is required.
        </span>
      ) : null}
    </Sheet>
  );
}
