// Utilities the chat's cards and lines share. `card` itself (chat.css) stays beside them: the prompt card and the agent-terminal base use it too.
/** The one-line body of a card: it takes the room the icons leave and cuts with an ellipsis. */
export const CARD_WHAT = 'card-what flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden text-ellipsis whitespace-nowrap';
/** The same body stacked, for a card whose title sits over its reason. */
export const CARD_WHAT_COL = 'card-what flex min-w-0 flex-1 flex-col items-start overflow-hidden text-ellipsis whitespace-nowrap';
export const CARD_CODE = 'card-code overflow-hidden text-ellipsis font-mono';
/** An open prompt is told by a thicker frame and its clock icon, not by colour. */
export const CARD_OPEN = 'data-[open]:border-2 data-[open]:border-line-strong';
