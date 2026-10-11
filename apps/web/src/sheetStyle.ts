// The small shared looks of the agent and settings pages. The classes `verb-sheet`, `shead` and `fnote` stay as hooks beside the utilities.
const SHEET = 'verb-sheet mt-0.5 mb-1 flex flex-wrap items-center gap-x-2.5 gap-y-1.5 rounded-card border bg-panel px-2.5 py-2 text-[12px]';

/** An inline confirm sheet; the danger one takes the whole row and a bad-state frame. */
export const VERB_SHEET = `${SHEET} border-line`;
export const VERB_SHEET_DANGER = `${SHEET} danger basis-full border-bad`;
/** The sheet's buttons, pushed to the end; the phone rules for them stay in inbox.css. */
export const VERB_SHEET_ACTIONS = 'verb-sheet-actions ml-auto flex gap-1.5';
/** A section heading. */
export const SHEAD = 'shead mt-[18px] mb-1.5 text-[11px] tracking-[0.06em] text-ink-2 [font-variant:small-caps]';
/** A line of feedback under a control, indented past the label column. */
export const FNOTE_BAD = 'fnote bad ml-[170px] basis-full text-[12px] text-bad max-sm:ml-0';
export const FNOTE_WARN = 'fnote warn ml-[170px] basis-full text-[12px] text-wait max-sm:ml-0';
/** The same heading on a settings page: a flat caps label with its icon. */
export const SHEAD_SETTINGS =
  'shead mx-0 mt-0 mb-1.5 flex items-center gap-1.5 text-hint font-normal tracking-[0.04em] text-ink-3 uppercase';
