// What the daemon recorded about an agent, newest first, with Older fetching the page before the oldest shown. A stalled or blocked badge opens it.
import { ago } from '@handline/app-core';
import { classify, sentence, useServes, useTimeline } from '@handline/app-data';
import { Button } from './ui/index.ts';
import { View } from './View.tsx';
import { useNow } from './taskui.tsx';

export function TimelineTab({ agent }: { agent: string }) {
  const { query: q, items, more, loadOlder, loadingOlder, olderError } = useTimeline(agent);
  const { served } = useServes('agent.timeline');
  const now = useNow();
  return (
    <section className="timeline-tab h-full overflow-auto p-2" aria-label={`Timeline of ${agent}`}>
      <View q={q} served={served} shape="inbox" what="the timeline" empty={!items.length && `Nothing is recorded for ${agent} yet.`}>
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {items.map((e) => (
            <li key={e.id} className="flex items-baseline gap-2 rounded-card border border-line bg-panel px-2.5 py-1.5" data-entry={e.id}>
              <span className="mono flex-none text-hint text-ink-2">{e.kind}</span>
              <span className="min-w-0 flex-1 text-body">{e.text}</span>
              {e.actor ? <span className="flex-none text-hint text-ink-2">{e.actor.name}</span> : null}
              <time className="flex-none text-hint text-ink-2" dateTime={e.at} title={e.at}>
                {ago(e.at, now)}
              </time>
            </li>
          ))}
        </ul>
        {olderError ? (
          <p className="banner" role="alert">
            {sentence(classify(olderError))}
          </p>
        ) : null}
        {more ? (
          <p className="pad">
            <Button
              variant="outline"
              size="sm"
              label={loadingOlder ? 'Loading…' : 'Older'}
              disabled={loadingOlder}
              onClick={() => void loadOlder()}
            />
          </p>
        ) : null}
      </View>
    </section>
  );
}
