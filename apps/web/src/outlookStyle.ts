// The looks of the usage outlook: the SVG strokes, the legend, the cost bars with their patterns, and the data table. The class names stay as
// hooks beside the utilities (tests and the legend read them).
export const OUTLOOK_END = 'outlook-end absolute right-0 -translate-y-1/2 text-label whitespace-nowrap text-ink';
export const GRID_LINE = 'grid stroke-line';
export const LINE = 'line stroke-ink stroke-2 [vector-effect:non-scaling-stroke]';
export const NOW_LINE = 'now stroke-ink-3 [stroke-dasharray:2_3] [vector-effect:non-scaling-stroke]';
export const LEGEND = 'outlook-legend muted m-0 mt-1.5 flex list-none gap-4 p-0 text-hint';
export const LEGEND_ITEM = 'inline-flex items-center gap-1.5';

/** A table of numbers: left-aligned cells under a rule, a small caps head. */
export const DATA_TABLE =
  'data-table mt-2 w-full border-collapse text-label [&_td]:border-b [&_td]:border-line [&_td]:px-1.5 [&_td]:py-0.5 [&_td]:text-left [&_th]:border-b [&_th]:border-line [&_th]:px-1.5 [&_th]:py-0.5 [&_th]:text-left [&_th]:text-hint [&_th]:font-normal [&_th]:tracking-[0.04em] [&_th]:text-ink-3 [&_th]:uppercase';

/** One look per series, told apart by a pattern and not only a tone: solid, stripes, dots, a grid. The index picks it, four in rotation. */
export const COST_TONES = [
  'bg-ink-2',
  '[background:repeating-linear-gradient(45deg,var(--ink-2)_0_2px,var(--bg-1)_2px_4px)]',
  '[background:radial-gradient(var(--ink-2)_30%,var(--bg-1)_35%)_0_0/4px_4px]',
  '[background:repeating-linear-gradient(0deg,var(--ink-2)_0_1px,transparent_1px_4px),repeating-linear-gradient(90deg,var(--ink-2)_0_1px,var(--bg-1)_1px_4px)]',
] as const;
export const tone = (i: number) => `c${i % 4} ${COST_TONES[i % 4]}`;

export const COST_BARS = 'cost flex h-[120px] items-end gap-1';
export const COST_DAY = 'cost-day flex h-full min-w-0 flex-1 flex-col justify-end';
export const COST_STACK = 'cost-stack flex min-h-0.5 flex-col-reverse';
export const COST_LABEL = 'cost-label muted overflow-hidden text-center text-hint';
export const COST_SWATCH = 'cost-swatch inline-block size-[var(--icon)] rounded-chip border border-line-strong align-middle';
export const COST_PICK =
  'cost-pick inline-flex items-center gap-1.5 border-0 bg-transparent p-0 text-left aria-pressed:font-semibold aria-pressed:underline';
