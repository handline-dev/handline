// The classes of a chat message bubble and its parts. `msg`, `person`, `agent`, `other`, `failed`, `picked` and `pickable` stay as
// hooks: the page and the tests query them.
const BUBBLE =
  'msg group border border-line px-3 py-2 break-keep wrap-break-word rounded-[10px] data-[landing=dismissed]:opacity-55 data-[landing=withdrawn]:opacity-55 data-[pending=true]:opacity-70 data-[flash]:outline-2 data-[flash]:outline-offset-2 data-[flash]:outline-dashed data-[flash]:outline-ink';
const SIDE = {
  person: 'person self-end bg-[color-mix(in_srgb,var(--primary)_14%,var(--panel))]',
  agent: 'agent self-start bg-panel',
  other: 'other self-start border-dashed bg-panel opacity-80',
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
    s.nested ? 'answer mt-2 max-w-full' : 'max-w-[80%]',
    s.failed ? 'failed border-bad' : '',
    s.picked ? 'picked outline-2 outline-offset-2 outline-primary' : '',
    s.pickable ? 'pickable cursor-pointer' : '',
  ]
    .filter(Boolean)
    .join(' ');

export const MSG_BODY = 'msg-body whitespace-pre-wrap group-data-[landing=withdrawn]:line-through';
export const MSG_META = 'msg-meta mt-0.5 text-[12px] [&_svg]:align-middle';
export const MSG_FAILED = `${MSG_META} failed-line text-bad`;
export const MSG_FROM = 'msg-from mt-0.5 text-[12px]';
