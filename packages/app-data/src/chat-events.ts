// The `chat` topic as cache edits: a transcript item or the working line is added as sent; a message replaces its own row,
// or the optimistic row this person's send made, or is added when it is new.
import type { ChatMessage, Event as WireEvent, TranscriptItem, Working } from '@handline/client';
import type { AgentView } from '@handline/client';
import { edit, mapPages, type Ctx, type Pages } from './cache.ts';
import { qk } from './reads.ts';

export interface MsgPage {
  messages: ChatMessage[];
  more: boolean;
}
export interface TrPage {
  items: TranscriptItem[];
  more: boolean;
  working: Working;
}
export const NOT_WORKING: Working = { turn_running: false, line: null, since: null };

/** A row the person's own send put in the cache and the daemon has not numbered yet. */
export const isProvisional = (m: ChatMessage) => m.id < 0;
export const patchMessage = (data: Pages<MsgPage>, id: number, patch: Partial<ChatMessage>) =>
  mapPages(data, (p) => ({ ...p, messages: p.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)) }));

/** The message as a page list takes it: in place if known, in place of this person's waiting row if it is that send, else first. */
function takeMessage(pages: Pages<MsgPage>, m: ChatMessage, me: string): Pages<MsgPage> {
  const known = pages.pages.some((p) => p.messages.some((x) => x.id === m.id));
  const mine = (x: ChatMessage) => isProvisional(x) && x.state !== 'failed' && x.from === me && x.body === m.body;
  const waiting = pages.pages.some((p) => p.messages.some(mine));
  if (known) return patchMessage(pages, m.id, m);
  if (waiting) {
    let swapped = false;
    return mapPages(pages, (p) => ({
      ...p,
      messages: p.messages.flatMap((x) => (!swapped && mine(x) ? ((swapped = true), [m]) : [x])),
    }));
  }
  return mapPages(pages, (p, i) => (i === 0 ? { ...p, messages: [m, ...p.messages] } : p));
}

export function chatEvent({ qc, d, me }: Ctx, e: WireEvent): void {
  const agent = e.agent;
  if (!agent) return;
  if (e.type === 'chat.transcript') {
    const item = e.data as unknown as TranscriptItem;
    edit<Pages<TrPage>>(qc, qk.transcript(d, agent), (data) =>
      mapPages(data, (p, i) =>
        i === 0 && !data.pages.some((x) => x.items.some((y) => y.id === item.id)) ? { ...p, items: [...p.items, item] } : p,
      ),
    );
  } else if (e.type === 'chat.working') {
    const working = e.data as unknown as Working;
    edit<Pages<TrPage>>(qc, qk.transcript(d, agent), (data) => mapPages(data, (p, i) => (i === 0 ? { ...p, working } : p)));
  } else if (e.type === 'chat.message') {
    const m = e.data as unknown as ChatMessage;
    const names = new Set(qc.getQueryData<{ agents: AgentView[] }>(qk.agents(d))?.agents.map((a) => a.name));
    const talkBetweenAgents = names.has(m.from) && names.has(m.to);
    for (const [key, cached] of qc.getQueriesData<Pages<MsgPage>>({ queryKey: qk.chatMessagesOf(d, agent) })) {
      const scope = key[4];
      const known = cached?.pages.some((p) => p.messages.some((x) => x.id === m.id));
      // The *mine* tab holds no talk between two agents; a message already in it is still updated.
      if (cached && (known || scope !== 'mine' || !talkBetweenAgents)) edit<Pages<MsgPage>>(qc, key, (data) => takeMessage(data, m, me));
    }
  }
}
