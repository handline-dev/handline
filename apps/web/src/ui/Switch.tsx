// An on/off switch (Base UI), 36 by 20. On is the knob at the right and a check in it, so it is not told by colour alone.
import { Switch as Base } from '@base-ui/react/switch';
import { Icon } from '#src/ui/Icon.tsx';

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
      className={`ui-switch inline-flex h-5 w-9 items-center rounded-full p-0.5 ${on ? 'justify-end bg-acc' : 'bg-raised'}`}
    >
      <Base.Thumb
        className={`ui-knob inline-flex h-4 w-4 items-center justify-center rounded-full text-acc ${on ? 'bg-on-acc' : 'bg-ink-2'}`}
      >
        {on ? <Icon name="markRead" size={12} label="On" aria-hidden /> : null}
      </Base.Thumb>
    </Base.Root>
  );
}
