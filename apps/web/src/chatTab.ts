/** The class of a page tab (team, account): an underlined button, the current one in ink. */
export const chatTab = (on: boolean): string =>
  `chat-tab border-0 border-b-2 bg-transparent px-3 py-1 ${on ? 'on border-b-primary text-ink' : 'border-b-transparent text-ink-2'}`;
