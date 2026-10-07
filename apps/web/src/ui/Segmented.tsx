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
    <div className="ui-seg" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          aria-label={o.label}
          title={o.label}
          className={o.value === value ? 'on' : ''}
          onClick={() => onChange(o.value)}
        >
          {o.icon ? <Icon name={o.icon} size={14} label={o.label} aria-hidden /> : o.label}
        </button>
      ))}
    </div>
  );
}
