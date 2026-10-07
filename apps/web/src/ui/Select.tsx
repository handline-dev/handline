// A select with a chevron: the browser's own list, drawn in the tokens.
import type { SelectHTMLAttributes } from 'react';
import { Icon } from './Icon.tsx';

export function Select({
  options,
  label,
  ...rest
}: { options: { value: string; label: string }[]; label: string } & Omit<SelectHTMLAttributes<HTMLSelectElement>, 'children'>) {
  return (
    <span className="ui-select">
      <select aria-label={label} title={label} {...rest}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <Icon name="chevron" size={14} label="Open" aria-hidden />
    </span>
  );
}
