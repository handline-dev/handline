// A select with a chevron: the browser's own list, drawn in the tokens.
import type { SelectHTMLAttributes } from 'react';
import { Icon } from '#src/ui/Icon.tsx';

export function Select({
  options,
  label,
  ...rest
}: { options: { value: string; label: string; disabled?: boolean }[]; label: string } & Omit<
  SelectHTMLAttributes<HTMLSelectElement>,
  'children'
>) {
  return (
    <span className="ui-select relative inline-flex max-w-full items-center">
      <select
        aria-label={label}
        title={label}
        className="h-9 min-w-0 max-w-full appearance-none truncate rounded-sm border-0 bg-panel py-0 pl-3 pr-8 text-ink tint"
        {...rest}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={o.disabled}>
            {o.label}
          </option>
        ))}
      </select>
      <Icon name="chevron" size={14} label="Open" aria-hidden className="pointer-events-none absolute right-2.5 text-ink-2" />
    </span>
  );
}
