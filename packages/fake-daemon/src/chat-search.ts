// chat.search's filter for FakeChat: the hits before they are drawn.
import type { ChatMessage } from '@handline/contract';
import { invalidParams } from '#src/errors.ts';

type Held = ChatMessage & { agent: string };

/** Messages whose body holds the text, newest first, after agent, with, since and scope; at most limit. */
export function searchHits(messages: Held[], p: Record<string, unknown>, shownInMine: (m: Held) => boolean): Held[] {
  const query = typeof p.query === 'string' ? p.query.trim().toLowerCase() : '';
  if (!query) throw invalidParams('query is text and not empty', 'query');
  if (p.limit !== undefined && !(Number.isInteger(p.limit) && (p.limit as number) > 0))
    throw invalidParams('limit is a positive integer', 'limit');
  const limit = typeof p.limit === 'number' ? p.limit : 50;
  const since = typeof p.since === 'string' ? Date.parse(p.since) : 0;
  if (Number.isNaN(since) || (p.since !== undefined && typeof p.since !== 'string'))
    throw invalidParams('since is an RFC 3339 timestamp', 'since');
  return messages
    .filter(
      (m) =>
        (typeof p.agent !== 'string' || m.agent === p.agent) &&
        (typeof p.with !== 'string' || (m.from === p.with) !== (m.to === p.with)) &&
        Date.parse(m.at) >= since &&
        !(m.receipt && m.landing === 'withdrawn') &&
        m.body.toLowerCase().includes(query) &&
        (p.scope !== 'mine' || shownInMine(m)),
    )
    .sort((a, b) => b.id - a.id)
    .slice(0, limit);
}
