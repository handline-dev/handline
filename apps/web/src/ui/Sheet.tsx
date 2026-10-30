// A sheet: the bordered strip that asks something under the thing it is about (`verb` for an inline confirm, `form` for a task form).
import type { ComponentProps, ComponentType } from 'react';
import { motion } from 'framer-motion';
import { Icon } from '#src/ui/Icon.tsx';
import { useOpenMotion } from '#src/ui/Reveal.tsx';

type Kind = 'verb' | 'form';
const SIZE: Record<Kind, string> = {
  verb: 'verb-sheet mt-[calc(2px*var(--o,1))] mb-[calc(4px*var(--o,1))] gap-x-2.5 gap-y-1.5 px-2.5 py-[calc(8px*var(--o,1))] text-[12px]',
  form: 'tsheet my-[calc(8px*var(--o,1))] gap-2 px-3 py-[calc(8px*var(--o,1))]',
};

/** It grows open when it mounts and shrinks shut when its parent's <Presence> lets it leave. `danger` takes the whole row in a bad-state frame. The class names are hooks the tests and the phone rules read. */
export function Sheet({
  kind = 'verb',
  as: As = 'div',
  danger,
  className,
  ...rest
}: ComponentProps<'form'> & { kind?: Kind; as?: 'div' | 'span' | 'form'; danger?: boolean }) {
  const Tag = motion[As] as unknown as ComponentType<ComponentProps<'form'>>; // one of three tags that take the same props
  const open = useOpenMotion();
  const look = danger ? 'danger basis-full bg-[color-mix(in_srgb,var(--s-bad)_8%,var(--bg-2))]' : '';
  return (
    <Tag
      {...open}
      {...rest}
      style={{ overflowY: 'clip', overflowX: 'visible', ...rest.style }}
      className={`${SIZE[kind]} flex flex-wrap items-center rounded-md bg-panel ${look}${className ? ` ${className}` : ''}`}
    />
  );
}

/** The sheet's buttons, pushed to the end. */
export function SheetActions({ kind = 'verb', className, ...rest }: ComponentProps<'span'> & { kind?: Kind }) {
  const hook = kind === 'verb' ? 'verb-sheet-actions' : 'tsheet-actions';
  return <span {...rest} className={`${hook} ml-auto flex gap-1.5 max-sm:w-full max-sm:[&>*]:flex-1${className ? ` ${className}` : ''}`} />;
}

/** A line of feedback under a control, indented past the label column. The tone is a glyph first, colour second. */
export function FNote({ tone, className, children, ...rest }: ComponentProps<'p'> & { tone: 'bad' | 'warn' }) {
  const look = tone === 'bad' ? 'bad text-bad' : 'warn text-wait';
  return (
    <p {...rest} className={`fnote ${look} ml-[170px] basis-full text-[12px] max-sm:ml-0${className ? ` ${className}` : ''}`}>
      <Icon name={tone === 'bad' ? 'failed' : 'warn'} size={13} aria-hidden className="mr-1 inline-block align-[-2px]" />
      {children}
    </p>
  );
}
