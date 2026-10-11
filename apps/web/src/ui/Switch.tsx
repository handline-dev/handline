// An on/off switch (Base UI), 28 by 16. On is the knob at the right and a check in it, so it is not told by colour alone.
import { Switch as Base } from '@base-ui/react/switch';
import { Icon } from './Icon.tsx';

export function Switch({
  on,
  onChange,
  label,
  disabled,
}: {
  on: boolean;
  onChange: (on: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <Base.Root
      checked={on}
      onCheckedChange={onChange}
      aria-label={label}
      title={label}
      disabled={disabled}
      className={`ui-switch inline-flex h-4 w-7 items-center rounded-pill p-0.5 ${on ? 'justify-end bg-primary' : 'bg-raised'}`}
    >
      <Base.Thumb
        className={`ui-knob inline-flex h-2.5 w-2.5 items-center justify-center rounded-pill text-primary ${on ? 'bg-on-primary' : 'bg-ink-2'}`}
      >
        {on ? <Icon name="markRead" size={10} label="On" aria-hidden /> : null}
      </Base.Thumb>
    </Base.Root>
  );
}
