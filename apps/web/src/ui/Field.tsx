// A labelled input (§1.6): the label in `--ink-2` above a 36 px field, a note under it that starts with an icon when it is a warning or an error.
import { useId, type InputHTMLAttributes, type ReactNode } from 'react';
import { Icon } from './Icon.tsx';

export function Field({
  label,
  note,
  tone,
  unit,
  ...rest
}: { label: string; note?: ReactNode; tone?: 'warn' | 'bad'; unit?: string } & Omit<InputHTMLAttributes<HTMLInputElement>, 'children'>) {
  const id = useId();
  return (
    <div className="ui-field">
      <label htmlFor={id}>{label}</label>
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
        <p id={`${id}-note`} className={`ui-note${tone ? ` ${tone}` : ''}`}>
          {tone ? <Icon name={tone === 'bad' ? 'failed' : 'warn'} size={14} label={tone === 'bad' ? 'Error' : 'Warning'} /> : null}
          {note}
        </p>
      ) : null}
    </div>
  );
}
