// The looks the settings pages share: the card of rows, a row, its title and grey hint. The classes are hooks (tests and the page's observer use them).
const ROW =
  'srow grid min-h-11 items-center gap-x-4 gap-y-1 px-4 py-2 [&:not(:first-child)]:border-t [&:not(:first-child)]:border-line [&_.ui-note]:col-span-full [&_.verb-sheet]:col-span-full [&_.ui-field>label]:sr-only [&_.ui-input-wrap_input]:h-8 [&_.ui-input-wrap_input]:w-40 [&_.ui-input-wrap_input]:pr-14 [&_.ui-input-wrap_input]:tabular-nums';
export const SCARD = 'scard m-0 list-none rounded-card border border-line bg-surface p-0';
/** A row: the text, then the control at the end; a note or a sheet takes the line below. */
export const SROW = `${ROW} grid-cols-[minmax(0,1fr)_auto]`;
/** A standing answer: an icon, the text, then its remove button. */
export const SROW_ANSWER = `${ROW} grid-cols-[auto_minmax(0,1fr)_auto]`;
export const SROW_TEXT = 'srow-text flex min-w-0 flex-col';
const TITLE = 'srow-title flex items-center gap-1.5 text-body text-ink [&_svg]:text-ink-3';
export const SROW_TITLE = `${TITLE} font-medium`;
/** The title of a standing answer: the answer's own words, in the plain weight. */
export const SROW_TITLE_PLAIN = `${TITLE} font-normal`;
export const SROW_HINT = 'srow-hint text-label text-ink-2';
export const SROW_CTL = 'srow-ctl flex min-w-0 items-center justify-end gap-2';
