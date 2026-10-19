// The Activity of a task as one list: its comments, and its events in words (a handoff is one line, its note opens on "Read note").
// Comments come from the memos, which a write patches at once; the other entries from the daemon's timeline.
import type { TaskDetail } from '@handline/client';

type Entry = TaskDetail['timeline'][number];

export interface ActivityItem {
  kind: 'comment' | 'event';
  at: string;
  who: string;
  /** What happened, as a sentence; empty for a comment. */
  line: string;
  /** The text behind an event (a handoff's note), shown on request. */
  note: string;
  memo: number | null;
  edited: boolean;
}

const STATE_WORD: Record<string, string> = {
  opened: 'opened it',
  doing: 'started it',
  done: 'marked it done',
  discarded: 'discarded it',
  queued: 'queued it',
  draft: 'moved it back to draft',
  review: 'reported it',
  blocked: 'blocked it',
};

/** "handline.pc marked it done" from a state entry whose text is `from -> to`. */
const stateLine = (who: string, text: string): string => {
  const to = text.split('->').pop()?.trim() ?? '';
  return `${who} ${STATE_WORD[to] ?? `moved it to ${to || 'another state'}`}`;
};

/** The memos as comments and the rest of the timeline as events in words, oldest first. */
export function activityOf(memos: TaskDetail['memos'], timeline: readonly Entry[]): ActivityItem[] {
  const comments: ActivityItem[] = memos.map((m) => ({
    kind: 'comment',
    at: m.at,
    who: m.author,
    line: '',
    note: m.text,
    memo: m.id,
    edited: !!m.edited_at,
  }));
  const events = timeline.flatMap((e): ActivityItem[] => {
    const base = { kind: 'event' as const, at: e.at, who: e.who, memo: e.memo, edited: false };
    switch (e.kind) {
      case 'memo':
        return [];
      case 'handoff': {
        const [from = e.who, to = ''] = e.who.split('→').map((x) => x.trim());
        return [{ ...base, who: from, line: to ? `${from} handed it to ${to}` : `${from} handed it over`, note: e.text }];
      }
      case 'state':
        return [{ ...base, line: stateLine(e.who, e.text), note: '' }];
      case 'edit':
        return [{ ...base, line: `${e.who} changed the brief`, note: e.text }];
      case 'reject':
        return [{ ...base, line: `${e.who} sent it back`, note: e.text }];
      case 'request':
        return [{ ...base, line: `${e.who} sent a request`, note: e.text }];
    }
  });
  return [...comments, ...events].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
}

/** Comments stay; of the events only the last `keep` show until the rest are asked for. */
export function foldEvents(items: readonly ActivityItem[], open: boolean, keep = 3): { items: ActivityItem[]; hidden: number } {
  const events = items.filter((i) => i.kind === 'event');
  const hide = new Set(open ? [] : events.slice(0, Math.max(0, events.length - keep)));
  return { items: items.filter((i) => !hide.has(i)), hidden: hide.size };
}
