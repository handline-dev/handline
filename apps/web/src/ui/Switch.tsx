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
      className="ui-switch"
      onClick={() => onChange(!on)}
    >
      <span className="ui-knob">{on ? <Icon name="markRead" size={10} label="On" aria-hidden /> : null}</span>
    </button>
  );
}
