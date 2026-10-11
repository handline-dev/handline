// An on/off switch, 28 by 16. On is the knob at the right and a check in it, so it is not told by colour alone.
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
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      title={label}
      disabled={disabled}
      className={`ui-switch inline-flex h-4 w-7 items-center rounded-pill border border-line-strong p-0.5 ${on ? 'justify-end bg-primary' : 'bg-raised'}`}
      onClick={() => onChange(!on)}
    >
      <span
        className={`ui-knob inline-flex h-2.5 w-2.5 items-center justify-center rounded-pill text-primary ${on ? 'bg-on-primary' : 'bg-ink-2'}`}
      >
        {on ? <Icon name="markRead" size={10} label="On" aria-hidden /> : null}
      </span>
    </button>
  );
}
