// The card of rows on a settings page: a row is its text, then the control at the end; a standing answer adds a leading icon.
import type { ComponentProps, ElementType } from 'react';

const join = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');
const ROW =
  'srow grid min-h-11 items-center gap-x-4 gap-y-1 px-4 py-2 [&:not(:first-child)]:border-t [&:not(:first-child)]:border-line [&_.ui-note]:col-span-full [&_.verb-sheet]:col-span-full [&_.ui-field>label]:sr-only [&_.ui-input-wrap_input]:h-8 [&_.ui-input-wrap_input]:w-40 [&_.ui-input-wrap_input]:pr-14 [&_.ui-input-wrap_input]:tabular-nums';

export function SettingsCard({ as: Tag = 'div', className, ...rest }: ComponentProps<'div'> & { as?: ElementType }) {
  return <Tag {...rest} className={join('scard m-0 list-none rounded-card border border-line bg-surface p-0', className)} />;
}

/** `answer` is the three-column row of a standing answer: an icon, the text, then its remove button. */
export function SettingsRow({
  answer,
  as: Tag = 'div',
  className,
  ...rest
}: ComponentProps<'div'> & { answer?: boolean; as?: ElementType }) {
  return (
    <Tag {...rest} className={join(ROW, answer ? 'grid-cols-[auto_minmax(0,1fr)_auto]' : 'grid-cols-[minmax(0,1fr)_auto]', className)} />
  );
}

export function RowText({ className, ...rest }: ComponentProps<'div'>) {
  return <div {...rest} className={join('srow-text flex min-w-0 flex-col', className)} />;
}

/** `plain` is the regular weight: the title of a standing answer is the answer's own words. */
export function RowTitle({ plain, className, ...rest }: ComponentProps<'span'> & { plain?: boolean }) {
  return (
    <span
      {...rest}
      className={join(
        'srow-title flex items-center gap-1.5 text-body text-ink [&_svg]:text-ink-3',
        plain ? 'font-normal' : 'font-medium',
        className,
      )}
    />
  );
}

export function RowHint({ className, ...rest }: ComponentProps<'span'>) {
  return <span {...rest} className={join('srow-hint text-label text-ink-2', className)} />;
}

export function RowControl({ className, ...rest }: ComponentProps<'div'>) {
  return <div {...rest} className={join('srow-ctl flex min-w-0 items-center justify-end gap-2', className)} />;
}
