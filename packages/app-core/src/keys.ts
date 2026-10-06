// Key tables: what the daemon's session.keys takes, how browser keys map onto it, and the phone's key strip.

/** The key names `session.keys` takes (contract/protocol.json). Add the editing keys here when the contract names them. */
export const DAEMON_KEYS: ReadonlySet<string> = new Set(['esc', 'enter', 'up', 'down', 'tab', 'shift-tab', 'ctrl-c', ...'0123456789']);

/** KeyboardEvent.key to a daemon key name; names the daemon does not take yet are still listed so the screen can say so. */
export const NAMED_KEYS: Record<string, string> = {
  Escape: 'esc', Enter: 'enter', ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
  Backspace: 'backspace', Delete: 'delete', Home: 'home', End: 'end',
};

/** The phone's key strip: the label on the key and the key name sent. */
export const KEY_STRIP: { label: string; key: string }[] = [
  { label: 'Esc', key: 'esc' }, { label: '^C', key: 'ctrl-c' }, { label: '↵', key: 'enter' },
  { label: '↑', key: 'up' }, { label: '↓', key: 'down' }, { label: '⇥', key: 'tab' }, { label: '⇧⇥', key: 'shift-tab' },
  { label: '1', key: '1' }, { label: '2', key: '2' }, { label: '3', key: '3' },
];
