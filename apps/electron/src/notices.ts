import type { Event } from '@handline/client';

export interface Notice {
  title: string;
  body: string;
  /** The agent whose card a click opens; null when the event names none. */
  agent: string | null;
}

const text = (v: unknown): string => (typeof v === 'string' || typeof v === 'number' ? String(v) : '');

/** The notice a `notifications` event raises, or null for an event that is not one of the four. */
export function noticeFor(e: Event): Notice | null {
  if (e.topic !== 'notifications') return null;
  const who = e.agent ?? text(e.data.agent);
  switch (e.type) {
    case 'prompt_asked':
      return { title: `${who || 'An agent'} is asking`, body: e.title ?? 'A prompt is waiting', agent: e.agent };
    case 'task_done':
      return { title: `Task ${text(e.data.task)} done`, body: e.title ?? who, agent: e.agent };
    case 'stall':
      return { title: `${who || 'An agent'} is stalled`, body: e.title ?? 'It has stopped making progress', agent: e.agent };
    case 'account_out':
      return { title: `Account ${text(e.data.account)} is out`, body: e.title ?? 'It has hit its limit', agent: e.agent };
    default:
      return null;
  }
}

/** The agent a route hash names (`#/agents/:name`), or ''. The web app owns this route. */
export const agentOfHash = (hash: string): string => decodeURIComponent(hash.match(/^#\/agents\/(.+)$/)?.[1] ?? '');
export const hashOfAgent = (agent: string): string => `#/agents/${encodeURIComponent(agent)}`;

/** No notice while the window is on screen showing that agent's workspace. */
export const suppressed = (n: Notice, screen: { focused: boolean; hash: string }): boolean =>
  screen.focused && n.agent !== null && agentOfHash(screen.hash) === n.agent;

/** The dock / taskbar badge text for an unread count: empty clears it. */
export const badgeText = (unread: number): string => (unread <= 0 ? '' : unread > 99 ? '99+' : String(unread));
