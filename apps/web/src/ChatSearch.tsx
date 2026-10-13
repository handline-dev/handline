// Search box in the Chat tab head: chat.search in the scope the tab shows; a hit goes to its message through the tab's jump (chat.locate).
import { useEffect, useState } from 'react';
import type { ChatMessage } from '@handline/client';
import { classify, sentence, useChatSearch, useServes, type ChatScope } from '@handline/app-data';
import { BarInput, Empty, Icon } from './ui/index.ts';

const SNIPPET = 90;

/** The words around the first match: ‘…’ marks a cut end. */
function snippet(body: string, query: string): string {
  const flat = body.replace(/\s+/g, ' ').trim();
  const at = flat.toLowerCase().indexOf(query.toLowerCase());
  const from = Math.max(0, at - 30);
  const cut = flat.slice(from, from + SNIPPET);
  return `${from > 0 ? '…' : ''}${cut}${from + SNIPPET < flat.length ? '…' : ''}`;
}

const when = (at: string) => {
  const d = new Date(at);
  return Number.isNaN(+d) ? '' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};

function Hit({ m, query, onGo }: { m: ChatMessage; query: string; onGo: (id: number) => void }) {
  return (
    <button
      type="button"
      className="hit flex w-full flex-col items-start gap-0.5 border-0 bg-transparent px-2 py-1.5 text-left hover:bg-surface"
      onClick={() => onGo(m.id)}
    >
      <span className="flex w-full items-baseline gap-2 text-label text-ink-2">
        <strong className="font-medium text-ink">{m.from}</strong>
        <span>{when(m.at)}</span>
      </span>
      <span className="text-body text-ink">{snippet(m.body, query)}</span>
    </button>
  );
}

export function ChatSearch({ agent, scope, onGo }: { agent: string; scope: ChatScope; onGo: (id: number) => void }) {
  const { served } = useServes('chat.search');
  const [text, setText] = useState('');
  const [query, setQuery] = useState(''); // the text once the person stops typing
  useEffect(() => {
    const t = setTimeout(() => setQuery(text.trim()), 250);
    return () => clearTimeout(t);
  }, [text]);
  const [shut, setShut] = useState(false); // Escape or a click elsewhere hides the results; typing brings them back
  const found = useChatSearch({ agent, query, scope }, query !== '');
  if (!served) return null;
  const hits = found.data?.messages ?? [];
  const go = (id: number) => (setText(''), onGo(id));
  // Results show for the text as it stands, not for the query of a moment ago.
  const showing = !shut && text.trim() !== '' && query === text.trim();
  return (
    <div
      className="chat-search relative"
      role="search"
      onBlur={(e) => !e.currentTarget.contains(e.relatedTarget) && setShut(true)}
      onFocus={() => setShut(false)}
    >
      <BarInput
        type="search"
        className="w-44"
        placeholder="Search this chat"
        aria-label="Search messages"
        value={text}
        onChange={(e) => (setText(e.target.value), setShut(false))}
        onKeyDown={(e) => e.key === 'Escape' && setShut(true)}
      />
      {showing ? (
        <div
          className="results absolute top-full right-0 z-10 mt-1 max-h-80 w-96 max-w-[80vw] overflow-auto rounded-dialog border border-line bg-panel shadow-[var(--shadow-pop)]"
          aria-live="polite"
        >
          {found.isError ? (
            <p className="p-2 text-body text-ink-2" role="alert">
              {sentence(classify(found.error)) ?? 'Search failed'}
            </p>
          ) : found.data && !hits.length ? (
            <Empty icon="search" name="No messages found" hint={`Nothing in this chat says “${query}”`} />
          ) : (
            hits.map((m) => <Hit key={m.id} m={m} query={query} onGo={go} />)
          )}
          {found.isFetching ? (
            <p className="flex items-center gap-1.5 p-2 text-body text-ink-3">
              <Icon name="search" size={14} aria-hidden /> Searching…
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
