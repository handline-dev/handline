// The classes of a chat message bubble and its parts. `msg`, `person`, `agent`, `other`, `failed`, `picked` and `pickable` stay as
// hooks: the page and the tests query them. Sides split by surface, not border: the person's is tinted, the agent's is bare.
const BUBBLE =
  'msg group px-3 py-2 break-keep wrap-break-word rounded-dialog data-[landing=dismissed]:opacity-55 data-[landing=withdrawn]:opacity-55 data-[pending=true]:opacity-70 data-[flash]:outline-2 data-[flash]:-outline-offset-2 data-[flash]:outline-dashed data-[flash]:outline-focus';
const SIDE = {
  // The person's message is the primary itself — ink in this design — so it flips black/white with the theme and everything inside reads on-primary.
  person:
    'person self-end bg-primary text-on-primary [&_a]:text-inherit [&_.msg-meta]:text-on-primary/60 [&_.msg-ref]:border-l-on-primary/30 [&_.msg-ref]:text-on-primary/60 [&_.ui-btn]:border-on-primary/30 [&_.ui-btn]:text-on-primary [&_.ui-btn:hover:enabled]:bg-on-primary/10',
  agent: 'agent self-start bg-surface',
  other: 'other self-start bg-surface opacity-80',
} as const;

export interface BubbleState {
  failed?: boolean;
  picked?: boolean;
  pickable?: boolean;
  /** An answer drawn inside the question it answers. */
  nested?: boolean;
}

export const bubbleClass = (side: keyof typeof SIDE, s: BubbleState = {}): string =>
  [
    BUBBLE,
    SIDE[side],
    s.nested ? 'answer mt-2 max-w-full' : '',
    // The person's bubble stays solid when failed — the failed line inside carries the state.
    s.failed && side !== 'person' ? 'failed bg-[color-mix(in_srgb,var(--s-bad)_10%,transparent)]' : '',
    // The ring sits inside the bubble: the stream's animated wrapper clips overflow, and an outside ring loses its edges.
    s.picked ? 'picked outline-2 -outline-offset-2 outline-focus' : '',
    s.pickable ? 'pickable cursor-pointer' : '',
  ]
    .filter(Boolean)
    .join(' ');

export const MSG_BODY = 'msg-body whitespace-pre-wrap group-data-[landing=withdrawn]:line-through';
export const MSG_META = 'msg-meta mt-0.5 text-[12px] [&_svg]:align-middle';
export const MSG_FAILED = `${MSG_META} failed-line text-bad`;
export const MSG_FROM = 'msg-from mb-0.5 text-[12px]';
