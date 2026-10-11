// A select with a chevron: the browser's own list, drawn in the tokens.
import type { SelectHTMLAttributes } from 'react';
import { Icon } from './Icon.tsx';

export function Select({
  options,
  label,
  ...rest
}: { options: { value: string; label: string }[]; label: string } & Omit<SelectHTMLAttributes<HTMLSelectElement>, 'children'>) {
  return (
    <span className="ui-select relative inline-flex items-center">
      <select
        aria-label={label}
        title={label}
        className="h-8 appearance-none rounded-ctl border border-line-strong bg-panel py-0 pl-2 pr-6 text-ink"
        {...rest}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <Icon name="chevron" size={14} label="Open" aria-hidden className="pointer-events-none absolute right-1.5 text-ink-2" />
    </span>
  );
}
