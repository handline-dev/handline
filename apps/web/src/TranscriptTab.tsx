// What a structured agent did, in place of its terminal: the message that began each turn, its text, tool calls and results (collapsed until
// opened), permission cards, what the turn cost, and errors. It follows the stream while the person is at the end and offers a jump back when not.
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Skeleton, useTranscript, classify, sentence, useWrite, type Transcript, type Turn } from '@handline/app-data';
import { PromptCard } from './PromptCard.tsx';
import { Button, Empty } from './ui/index.ts';

type Entry = Turn['entries'][number];
type Tool = Extract<Entry, { type: 'tool' }>;

const oneLine = (s: string) => s.replace(/\s+/g, ' ').trim().slice(0, 100);
const inputLine = (input: Record<string, unknown>) => {
  const first = Object.values(input).find((v) => typeof v === 'string');
  return oneLine(typeof first === 'string' ? first : JSON.stringify(input));
};

function Fold({ head, tone, children }: { head: ReactNode; tone?: 'bad'; children: ReactNode }) {
  return (
    <details className="tr-fold" data-tone={tone}>
      <summary>{head}</summary>
      <pre>{children}</pre>
    </details>
  );
}

function ToolCall({ agent, t }: { agent: string; t: Tool }) {
  const { result, permission, answer } = t;
  return (
    <div className="tr-tool" data-tool={t.id}>
      <Fold
        head={
          <>
            <b>{t.tool ?? 'tool'}</b> {inputLine(t.input)}
          </>
        }
      >
        {JSON.stringify(t.input, null, 2)}
      </Fold>
      {permission?.open ? <PromptCard agent={agent} prompt={permission.prompt} /> : null}
      {answer ? (
        <div className="tr-note" data-answer={answer.behavior}>
          {answer.behavior === 'allow' ? 'Allowed' : 'Denied'} by {answer.by}
          {answer.message ? `: ${answer.message}` : ''}
        </div>
      ) : null}
      {result ? (
        <Fold
          head={
            <>
              {result.ok ? 'returned' : 'failed'}: {oneLine(result.text)}
            </>
          }
          tone={result.ok ? undefined : 'bad'}
        >
          {result.text + (result.truncated ? '\n… cut' : '')}
        </Fold>
      ) : null}
    </div>
  );
}

function Item({ agent, e }: { agent: string; e: Entry }) {
  switch (e.type) {
    case 'text':
      return <p className="tr-text">{e.text}</p>;
    case 'tool':
      return <ToolCall agent={agent} t={e} />;
    case 'error':
      return (
        <div className="tr-error" role="alert">
          {e.code}: {e.text}
        </div>
      );
    case 'gap':
      return <div className="tr-note">Earlier events are no longer kept</div>;
  }
}

function TurnView({ agent, turn }: { agent: string; turn: Turn }) {
  const { message, end } = turn;
  return (
    <section className="tr-turn" data-turn={turn.id ?? ''} data-running={turn.running || undefined}>
      {message ? (
        <div className="tr-said">
          <span>{message.from ?? 'someone'}</span> {message.text}
        </div>
      ) : null}
      {turn.entries.map((e) => (
        <Item key={`${e.type}-${e.seq}`} agent={agent} e={e} />
      ))}
      {end ? (
        <div className="tr-end" data-reason={end.reason}>
          {end.reason} · ${end.cost_usd.toFixed(2)} · {(end.duration_ms / 1000).toFixed(1)} s
        </div>
      ) : null}
    </section>
  );
}

/** The line over a running turn: Interrupt ends it, and a refusal says why beside the button. */
function Working({ agent }: { agent: string }) {
  const interrupt = useWrite('session.interrupt', agent);
  return (
    <div className="tr-working" role="status">
      <span>Working…</span>
      <Button
        label={interrupt.isPending ? 'Interrupting…' : 'Interrupt'}
        onClick={() => interrupt.mutate({ agent })}
        disabled={interrupt.isPending}
      />
      {interrupt.isError ? <span className="muted">{sentence(classify(interrupt.error))}</span> : null}
    </div>
  );
}

/** The transcript of a structured agent. */
export function TranscriptView({ agent, t }: { agent: string; t: Transcript }) {
  const box = useRef<HTMLDivElement>(null);
  const [atEnd, setAtEnd] = useState(true);
  const follow = useRef(true);
  const toEnd = () => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  };
  useLayoutEffect(() => {
    if (follow.current) toEnd();
  }, [t.turns, t.status]);

  if (t.status === 'error')
    return (
      <div className="tr">
        <Empty icon="terminal" name="The transcript could not be read" />
        <Button label="Retry" onClick={t.refetch} />
      </div>
    );
  return (
    <div className="tr" data-status={t.status}>
      {t.running ? <Working agent={agent} /> : null}
      <div
        className="tr-scroll"
        ref={box}
        data-testid="transcript"
        onScroll={(ev) => {
          const el = ev.currentTarget;
          follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
          setAtEnd(follow.current);
        }}
      >
        {t.hasOlder ? (
          <Button
            label={t.loadingOlder ? 'Loading…' : t.olderError ? 'Could not load older, try again' : 'Load older'}
            onClick={t.loadOlder}
            disabled={t.loadingOlder}
          />
        ) : null}
        <Skeleton shape="chat" loading={t.status === 'loading'} label="the transcript" />
        {t.status === 'ready' && t.turns.length === 0 ? <Empty icon="chat" name="Nothing yet" /> : null}
        {t.turns.map((turn) => (
          <TurnView key={turn.seq} agent={agent} turn={turn} />
        ))}
      </div>
      {atEnd ? null : (
        <button
          className="tr-newest"
          onClick={() => {
            follow.current = true;
            toEnd();
            setAtEnd(true);
          }}
        >
          Jump to newest
        </button>
      )}
    </div>
  );
}

/** The watching tab of an agent: its transcript when the daemon runs it as a structured session, else its terminal. */
export function Watch({ agent, structured, terminal }: { agent: string; structured: boolean; terminal: ReactNode }) {
  return structured ? <Structured agent={agent} /> : <>{terminal}</>;
}

function Structured({ agent }: { agent: string }) {
  return <TranscriptView agent={agent} t={useTranscript(agent, true)} />;
}
