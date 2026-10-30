// The commands the palette lists with their keys. A command is a window event: the part of the app that owns the thing listens for it,
// so the key and the palette row do the same.
import type { IconName } from '#src/ui/index.ts';

export type CommandName = 'tab-new' | 'tab-close' | 'split' | 'sidebar' | 'back' | 'forward' | 'keys';
export const COMMAND_EVENT = 'handline:command';

export const COMMANDS: { name: CommandName; label: string; icon: IconName; keys: string }[] = [
  { name: 'tab-new', label: 'New tab', icon: 'plus', keys: '⌘T' },
  { name: 'tab-close', label: 'Close tab', icon: 'close', keys: '⌘W' },
  { name: 'split', label: 'Split pane', icon: 'split', keys: '⌘\\' },
  { name: 'sidebar', label: 'Show or hide the sidebar', icon: 'sidebar', keys: '⌘B' },
  { name: 'back', label: 'Back', icon: 'back', keys: '⌘[' },
  { name: 'forward', label: 'Forward', icon: 'chevronRight', keys: '⌘]' },
  { name: 'keys', label: 'Keyboard shortcuts', icon: 'search', keys: '?' },
];

export const runCommand = (name: CommandName): void => void dispatchEvent(new CustomEvent(COMMAND_EVENT, { detail: name }));

/** Calls `on` for each command run from the palette. */
export function onCommand(on: (name: CommandName) => void): () => void {
  const h = (e: Event) => on((e as CustomEvent<CommandName>).detail);
  addEventListener(COMMAND_EVENT, h);
  return () => removeEventListener(COMMAND_EVENT, h);
}
