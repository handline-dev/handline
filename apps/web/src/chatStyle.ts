// Utilities the chat's cards, lines and message box share.
/** The base of every chat card; the prompt card is one too. */
export const CARD =
  'card my-2 flex items-center gap-2 rounded-[8px] border border-transparent bg-surface px-2.5 py-1.5 text-[13px] shadow-[var(--shadow-card)] [&_svg]:align-middle [&_a]:text-inherit';
export const CARD_ACTIONS = 'card-actions mt-2 flex gap-2';
const BLOCK = 'm-0 block rounded-[6px] bg-term-bg px-2 py-1.5 font-mono text-[13px] whitespace-pre-wrap text-term-ink';
/** The terminal-dark block of a few lines, and of a command. */
export const LINES = `lines ${BLOCK}`;
export const CMD = `cmd ${BLOCK}`;
export const COMPOSE_ROW = 'compose-row relative flex items-end gap-1.5 border-t border-line px-3 py-2.5';
export const COMPOSE = 'compose flex-1 resize-none rounded-[6px] border border-line bg-canvas px-2 py-1.5';
/** The one-line body of a card: it takes the room the icons leave and cuts with an ellipsis. */
export const CARD_WHAT = 'card-what flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden text-ellipsis whitespace-nowrap';
/** The same body stacked, for a card whose title sits over its reason. */
export const CARD_WHAT_COL = 'card-what flex min-w-0 flex-1 flex-col items-start overflow-hidden text-ellipsis whitespace-nowrap';
export const CARD_CODE = 'card-code overflow-hidden text-ellipsis font-mono';
/** An open prompt is told by a thicker frame and its clock icon, not by colour. */
export const CARD_OPEN = 'data-[open]:border-2 data-[open]:border-line-strong';
