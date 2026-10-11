// A labelled input (§1.6): the label in `--ink-2` above a 36 px field, a note under it that starts with an icon when it is a warning or an error.
import { useId, type InputHTMLAttributes, type ReactNode } from 'react';
import { Icon } from './Icon.tsx';

/** The labelled field and its note, shared by the hand-rolled forms (NewTask, NewAgent, the account pages). */
export const FIELD = 'ui-field flex flex-col gap-1';
export const FIELD_LABEL = 'ui-field-label text-label text-ink-2';
export const NOTE = 'ui-note m-0 flex items-center gap-3 text-hint text-ink-3';
export const noteTone = (tone?: 'warn' | 'bad'): string => (tone === 'bad' ? ' text-bad' : tone === 'warn' ? ' text-wait' : '');

export function Field({
  label,
  note,
  tone,
  unit,
  className,
  ...rest
}: { label: string; note?: ReactNode; tone?: 'warn' | 'bad'; unit?: string; className?: string } & Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'children' | 'className'
>) {
  const id = useId();
  return (
    <div className={`${FIELD}${className ? ` ${className}` : ''}`}>
      <label htmlFor={id} className={FIELD_LABEL}>
        {label}
      </label>
      <div className="ui-input-wrap relative flex items-center">
        <input
          id={id}
          className="h-9 w-full rounded-ctl border border-line-strong bg-panel px-3 py-0 text-ink placeholder:text-ink-3 disabled:border-dashed disabled:text-ink-3"
          aria-describedby={note ? `${id}-note` : undefined}
          aria-invalid={tone === 'bad' || undefined}
          {...rest}
        />
        {unit ? <span className="ui-unit absolute right-3 text-hint text-ink-3">{unit}</span> : null}
      </div>
      {note ? (
        <p id={`${id}-note`} className={`${NOTE}${noteTone(tone)}`}>
          {tone ? <Icon name={tone === 'bad' ? 'failed' : 'warn'} size={14} label={tone === 'bad' ? 'Error' : 'Warning'} /> : null}
          {note}
        </p>
      ) : null}
    </div>
  );
}
