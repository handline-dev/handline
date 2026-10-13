// Small label-and-value layouts: a key/value line (label stacks over the value on a phone), a meta line of icon items, a meta grid of icon fields.
import type { ComponentProps, ReactNode } from 'react';
import { Icon, type IconName } from './Icon.tsx';

const join = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');

export function KeyValueRow({ className, ...rest }: ComponentProps<'div'>) {
  return (
    <div
      {...rest}
      className={join(
        'hr-kv grid grid-cols-[6rem_1fr] gap-2 text-body [overflow-wrap:anywhere] max-[480px]:grid-cols-1 max-[480px]:gap-0',
        className,
      )}
    />
  );
}

/** A wrapping line of icon-and-word items in the grey label size. */
export function MetaLine({ className, ...rest }: ComponentProps<'p'>) {
  return <p {...rest} className={join('m-0 flex flex-wrap items-center gap-x-3 gap-y-1 text-label text-ink-2', className)} />;
}

export function MetaItem({ className, ...rest }: ComponentProps<'span'>) {
  return <span {...rest} className={join('inline-flex items-center gap-1', className)} />;
}

/** Two columns of MetaField. */
export function MetaGrid({ className, ...rest }: ComponentProps<'dl'>) {
  return <dl {...rest} className={join('tmeta my-2 grid grid-cols-2 gap-x-4 gap-y-1.5', className)} />;
}

/** The label in words (an icon beside it), then the value. */
export function MetaField({ icon, label, value }: { icon: IconName; label: string; value: ReactNode }) {
  return (
    <div className="tfield2 flex min-w-0 items-center gap-1.5 text-ink-2" title={label}>
      <dt className="m-0 flex min-w-0 shrink-0 items-center gap-1 text-ink-3">
        <Icon name={icon} label={label} aria-hidden="true" />
        {label}
      </dt>
      <dd className="m-0 flex min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-ink">{value}</dd>
    </div>
  );
}
