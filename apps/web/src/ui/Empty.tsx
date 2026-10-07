// An empty state (§1.6): a 20 px icon in a circle, one line of name, one line of hint; nothing else.
import { Icon, type IconName } from './Icon.tsx';

export function Empty({ icon, name, hint }: { icon: IconName; name: string; hint?: string }) {
  return (
    <div className="ui-empty">
      <span className="ui-empty-ring">
        <Icon name={icon} size={20} label={name} aria-hidden />
      </span>
      <strong>{name}</strong>
      {hint ? <span>{hint}</span> : null}
    </div>
  );
}
