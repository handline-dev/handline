// An empty state (§1.6): a 20 px icon in a circle, one line of name, one line of hint; nothing else.
import { Icon, type IconName } from './Icon.tsx';

export function Empty({ icon, name, hint }: { icon: IconName; name: string; hint?: string }) {
  return (
    <div className="ui-empty flex flex-col items-center gap-1.5 px-4 py-8 text-center font-sans font-medium text-body">
      <span className="ui-empty-ring inline-flex h-10 w-10 items-center justify-center rounded-pill border border-line-strong text-ink-2">
        <Icon name={icon} size={20} label={name} aria-hidden />
      </span>
      <strong>{name}</strong>
      {hint ? <span className="font-normal text-body text-ink-3">{hint}</span> : null}
    </div>
  );
}
