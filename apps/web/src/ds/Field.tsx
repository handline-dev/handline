// Input and choice: one 36px input, a check list for picking several, a segmented switch for picking one of two or three.
import type { InputHTMLAttributes, ReactNode } from 'react';

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...rest}
      className={`h-[var(--h-l)] w-[280px] max-w-full rounded-sm border border-line-strong bg-canvas px-3 font-sans text-md text-ink placeholder:text-ink-3 focus:outline-2 focus:outline-offset-1 focus:outline-acc${className ? ` ${className}` : ''}`}
    />
  );
}

/** One row of a check list; rows are set apart by a hairline, not by boxes. */
export function Check({
  label,
  hint,
  ...rest
}: { label: ReactNode; hint?: ReactNode } & Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>) {
  return (
    <label className="flex items-baseline gap-2.5 border-t border-line py-2 text-sm first:border-t-0">
      <input {...rest} type="checkbox" className="translate-y-0.5 accent-[var(--acc)]" />
      <span className="min-w-40 font-mono text-sm">{label}</span>
      {hint ? <span className="text-ink-2">{hint}</span> : null}
    </label>
  );
}

export interface Choice<T extends string> {
  value: T;
  label: ReactNode;
}

export function Segmented<T extends string>({
  choices,
  value,
  onChange,
  label,
}: {
  choices: Choice<T>[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <span role="radiogroup" aria-label={label} className="inline-flex gap-0.5 rounded-sm bg-panel p-0.5 text-sm">
      {choices.map((c) => (
        <button
          key={c.value}
          type="button"
          role="radio"
          aria-checked={c.value === value}
          onClick={() => onChange(c.value)}
          className={`cursor-pointer rounded-[5px] border-0 px-2.5 py-[3px] focus-visible:outline-2 focus-visible:outline-acc ${c.value === value ? 'bg-canvas font-medium text-ink shadow-[0_1px_1px_rgb(0_0_0/0.05)]' : 'bg-transparent text-ink-2'}`}
        >
          {c.label}
        </button>
      ))}
    </span>
  );
}
