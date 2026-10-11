// A sheet: the bordered strip that asks something under the thing it is about (`verb` for an inline confirm, `form` for a task form).
import type { ComponentProps, ComponentType } from 'react';

type Kind = 'verb' | 'form';
const SIZE: Record<Kind, string> = {
  verb: 'verb-sheet mt-0.5 mb-1 gap-x-2.5 gap-y-1.5 px-2.5 py-2 text-[12px]',
  form: 'tsheet my-2 gap-2 px-3 py-2',
};

/** `danger` takes the whole row in a bad-state frame. The class names are hooks the tests and the phone rules read. */
export function Sheet({
  kind = 'verb',
  as: As = 'div',
  danger,
  className,
  ...rest
}: ComponentProps<'form'> & { kind?: Kind; as?: 'div' | 'span' | 'form'; danger?: boolean }) {
  const Tag = As as unknown as ComponentType<ComponentProps<'form'>>; // one of three tags that take the same props
  const look = danger ? 'danger basis-full border-bad' : 'border-line';
  return (
    <Tag
      {...rest}
      className={`${SIZE[kind]} flex flex-wrap items-center rounded-card border bg-panel ${look}${className ? ` ${className}` : ''}`}
    />
  );
}

/** The sheet's buttons, pushed to the end. */
export function SheetActions({ kind = 'verb', className, ...rest }: ComponentProps<'span'> & { kind?: Kind }) {
  const hook = kind === 'verb' ? 'verb-sheet-actions' : 'tsheet-actions';
  return <span {...rest} className={`${hook} ml-auto flex gap-1.5${className ? ` ${className}` : ''}`} />;
}

/** A line of feedback under a control, indented past the label column. */
export function FNote({ tone, className, ...rest }: ComponentProps<'p'> & { tone: 'bad' | 'warn' }) {
  const look = tone === 'bad' ? 'bad text-bad' : 'warn text-wait';
  return <p {...rest} className={`fnote ${look} ml-[170px] basis-full text-[12px] max-sm:ml-0${className ? ` ${className}` : ''}`} />;
}
