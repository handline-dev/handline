import type { Event } from '@handline/client';

export interface Notice {
  title: string;
  body: string;
  /** The agent the event is about, for holding the notice back while that agent is on screen; null when it names none. */
  agent: string | null;
  /** The daemon's deep link for the event; a click hands it to the page. Null: a click only shows the window. */
  target: { screen: string; id: string } | null;
  /** The notifications event's type, which the page needs to pick the route. */
  event: string;
}

const text = (v: unknown): string => (typeof v === 'string' || typeof v === 'number' ? String(v) : '');

/** The notice a `notifications` event raises, or null for an event that is not one of the four. */
export function noticeFor(e: Event): Notice | null {
  if (e.topic !== 'notifications') return null;
  const who = e.agent ?? text(e.data.agent);
  switch (e.type) {
    case 'prompt_asked':
      return {
        title: `${who || 'An agent'} is asking`,
        body: e.title ?? 'A prompt is waiting',
        agent: e.agent,
        target: e.target,
        event: e.type,
      };
    case 'task_done':
      return { title: `Task ${text(e.data.task)} done`, body: e.title ?? who, agent: e.agent, target: e.target, event: e.type };
    case 'stall':
      return {
        title: `${who || 'An agent'} is stalled`,
        body: e.title ?? 'It has stopped making progress',
        agent: e.agent,
        target: e.target,
        event: e.type,
      };
    case 'account_out':
      return {
        title: `Account ${text(e.data.account)} is out`,
        body: e.title ?? 'It has hit its limit',
        agent: e.agent,
        target: e.target,
        event: e.type,
      };
    default:
      return null;
  }
}

/** No notice while the window is focused and the page says that agent's workspace is on screen. */
export const suppressed = (n: Notice, screen: { focused: boolean; agent: string | null }): boolean =>
  screen.focused && n.agent !== null && screen.agent === n.agent;

/** The dock / taskbar badge text for an unread count: empty clears it. */
export const badgeText = (unread: number): string => (unread <= 0 ? '' : unread > 99 ? '99+' : String(unread));
