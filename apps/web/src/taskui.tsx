// Small pieces the Tasks list and the task detail share: the state chip, the clock, the width query and the inline reason sheet.
import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { isPerson } from '@handline/app-core';
import { useMe } from '@handline/app-react';
import { useServes } from '@handline/app-data';
import { STATE_GLYPH as GLYPHS, toneOf, type Tone } from '@handline/app-ui';
import { Icon, type IconName } from '#src/Icon.tsx';
import { Button, Input, Sheet as SheetBox, SheetActions, VisuallyHidden } from '#src/ui/index.ts';

export const STATE_GLYPH: Record<string, IconName> = GLYPHS;

/** The chip look per tone; the class is the hook, the utilities the look. `run` also spins the doing glyph (tasks.css). */
const TONE: Record<Tone, string> = { run: 'run text-working', wait: 'wait text-wait', block: 'block text-bad', idle: 'idle' };
const CHIP = 'inline-flex items-center gap-0.5 align-middle text-ink-2 [&>svg]:shrink-0';

/** A done gate that failed reads red, one that timed out grey; any other done stays green. */
const DONE_TONE: Record<string, string> = { failed: 'data-[state=done]:text-bad', timed_out: 'data-[state=done]:text-ink-3' };

/** A state is a shape of its own (the glyph), then a tooltip, then colour; the word stays for a screen reader. `mark` says what the write in flight is: hollow dot, or the decided "…". */
export function StateChip({
  state,
  mark,
  word,
  to,
  result,
}: {
  state: string;
  mark?: 'authored' | 'decided' | null;
  word?: boolean;
  to?: string;
  result?: string | null;
}) {
  const me = useMe();
  const foryou = state === 'doing' && !!to && isPerson(to, me); // the task is the person's to do: the agent is not working on it
  const said = foryou
    ? 'needs you'
    : state === 'done' && (result === 'failed' || result === 'timed_out')
      ? result === 'failed'
        ? 'failed'
        : 'timed out'
      : state;
  if (mark === 'decided')
    return (
      <span className={`tchip pending ${CHIP}`} title="waiting for the daemon">
        …
      </span>
    );
  return (
    <span
      className={`tchip ${TONE[foryou ? 'wait' : toneOf(state)]} ${DONE_TONE[result ?? ''] ?? 'data-[state=done]:text-ok'} ${CHIP}`}
      data-state={state}
      title={said}
    >
      {mark === 'authored' ? <Icon name="unsaved" size={12} aria-hidden /> : null}
      <Icon
        name={
          foryou
            ? 'taskPending'
            : state === 'done' && result === 'failed'
              ? 'failed'
              : state === 'done' && result === 'timed_out'
                ? 'waiting'
                : (STATE_GLYPH[state] ?? 'taskPending')
        }
        label={said}
        size={16}
        aria-hidden="true"
        className={state === 'doing' && !foryou ? 'animate-spin motion-reduce:animate-none' : undefined}
      />
      {word ? <span className="ml-0.5">{said}</span> : <VisuallyHidden>{said}</VisuallyHidden>}
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

/**
 * Which optional columns of the table fit its own width (not the window's: the detail pane takes part of it). The one place that decides, so the
 * colgroup, the group headings' span and the cells agree; on a phone the rows are cards and show everything.
 */
export function useTableColumns(ref: RefObject<HTMLElement | null>): { owner: boolean; pri: boolean } {
  const [width, setWidth] = useState(Infinity);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const on = () => setWidth(innerWidth < 640 ? Infinity : el.clientWidth || Infinity);
    on();
    const ro = new ResizeObserver(on);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return { owner: width > 600, pri: width > 720 };
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
      className={
        props.full
          ? 'full max-sm:fixed max-sm:inset-0 max-sm:z-20 max-sm:m-0 max-sm:p-4 max-sm:rounded-none max-sm:overflow-auto max-sm:content-start'
          : undefined
      }
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
