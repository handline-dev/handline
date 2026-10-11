// Two or three options in one control (§1.6, "On / Agent / Off"). The chosen one is raised and bold; an option can be an icon with its words as
// the tooltip.
import { Icon, type IconName } from './Icon.tsx';

export interface Choice<V extends string> {
  value: V;
  label: string;
  icon?: IconName;
}

export function Segmented<V extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: V;
  onChange: (v: V) => void;
  options: Choice<V>[];
  label: string;
}) {
  return (
    <div className="ui-seg inline-flex rounded-ctl border border-line-strong bg-surface p-0.5" role="radiogroup" aria-label={label}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            aria-label={o.label}
            title={o.label}
            className={`inline-flex h-6 min-w-6 items-center justify-center rounded-chip border-0 px-2 ${
              on ? 'on bg-raised font-semibold text-ink shadow-[inset_0_-2px_0_var(--ink)]' : 'bg-transparent text-ink-2'
            }`}
            onClick={() => onChange(o.value)}
          >
            {o.icon ? <Icon name={o.icon} size={14} label={o.label} aria-hidden /> : o.label}
          </button>
        );
      })}
    </div>
  );
}
