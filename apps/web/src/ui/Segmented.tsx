// Two or three options in one control (§1.6, "On / Agent / Off"). The chosen one is raised and bold; an option can be an icon with its words as
// the tooltip.
import { Radio } from '@base-ui/react/radio';
import { RadioGroup } from '@base-ui/react/radio-group';
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
    <RadioGroup
      value={value}
      onValueChange={(v) => onChange(v as V)}
      aria-label={label}
      className="ui-seg inline-flex rounded-ctl bg-surface p-0.5"
    >
      {options.map((o) => (
        <Radio.Root
          key={o.value}
          value={o.value}
          nativeButton
          render={<button type="button" />}
          aria-label={o.label}
          title={o.label}
          className="inline-flex h-6 min-w-6 items-center justify-center rounded-chip border-0 bg-transparent px-2 text-ink-2 data-[checked]:bg-raised data-[checked]:font-semibold data-[checked]:text-ink data-[checked]:shadow-[inset_0_-2px_0_var(--ink)]"
        >
          {o.icon ? <Icon name={o.icon} size={14} label={o.label} aria-hidden /> : o.label}
        </Radio.Root>
      ))}
    </RadioGroup>
  );
}
