// The card for a prompt the agent is stuck on: a permission (Yes or No, never "always"), a dialog (its own options, "always" ones only when the
// daemon can keep a rule), a question with numbered options, free text and, for a multi prompt, checkboxes with a Submit.
// The answer is a decided write: the clicked option shows "…", the rest are disabled, and a prompt that changed sends nothing.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import type { Prompt } from '@handline/client';
import { isAlways, promptChoices, promptTitle } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { usePending, useWrite } from '@handline/app-data';
import { AnimatedItem, AnimatedList } from './AnimatedList.tsx';
import { Card, CardActions, CodeBlock, Input, Kbd } from './ui/index.ts';

const code = (e: unknown) => (e as { code?: string } | null)?.code;

export function PromptCard({ agent, prompt: p, onStale }: { agent: string; prompt: Prompt; onStale?: () => void }) {
  const answer = useWrite('prompt.answer', agent);
  const answering = usePending('prompt.answer').some((v) => v.agent === agent);
  const rules = useSelector((s) => s.can('answer.add'));
  const [clicked, setClicked] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState('');
  const [changed, setChanged] = useState(false);
  const [slow, setSlow] = useState(false);
  const card = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // a new prompt starts clean; focus stays where it was
    setPicked([]);
    setTyping(false);
    setText('');
    setClicked(null);
  }, [p.id]);
  useEffect(() => {
    if (!answering) return setSlow(false);
    const t = setTimeout(() => setSlow(true), 10_000);
    return () => clearTimeout(t);
  }, [answering]);

  const all = promptChoices(p, rules);
  const cancel = p.kind === 'question' ? all.find((o) => o.key === 'esc') : undefined; // its own button, never a checkbox or a numbered row
  const opts = cancel ? all.filter((o) => o !== cancel) : all;
  const multi = p.multi === true;
  const question = p.kind === 'question';
  const send = (choice: string | string[] | { text: string }, mark: string) => {
    setClicked(mark);
    setChanged(false);
    // mutateAsync: the card unmounts when a refused answer finds no prompt left, and the slot must still hear of it
    answer.mutateAsync({ agent, prompt: p.id, choice }).catch((e) => {
      setClicked(null);
      if (code(e) !== 'stale_prompt') return;
      setChanged(true);
      onStale?.();
    });
  };
  const toggle = (key: string) => setPicked((l) => (l.includes(key) ? l.filter((k) => k !== key) : [...l, key]));
  const submit = () =>
    picked.length &&
    send(
      opts.map((o) => o.key).filter((k) => picked.includes(k)),
      'submit',
    );

  const onKey = (e: KeyboardEvent) => {
    const t = e.target as HTMLElement;
    if (answering || e.metaKey || e.ctrlKey || e.altKey) return;
    if (t.tagName === 'INPUT' && (t as HTMLInputElement).type === 'text') return;
    if (e.key === 'Escape') return t.blur();
    if (multi) return;
    const hit = all.find((o) => o.key === e.key.toLowerCase());
    const choice = hit ?? (e.key === 'Enter' && t.tagName !== 'BUTTON' ? all.find((o) => o.highlighted) : undefined);
    if (choice) {
      e.preventDefault();
      send(choice.key, choice.key);
    }
  };

  const mark = (key: string, label: string) => (answering && clicked === key ? '…' : label);
  return (
    <Card variant="chat" className="prompt" role="alert" ref={card} tabIndex={-1} onKeyDown={onKey} aria-busy={answering}>
      <div className="card-title mb-1.5 font-semibold">
        ⚠ {promptTitle(p)}
        <span className="muted since">
          {p.since ? ` · waiting since ${new Date(p.since).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}
        </span>
      </div>
      {changed ? (
        <p className="stale" role="status">
          This prompt changed
        </p>
      ) : null}
      {p.command ? <CodeBlock cmd>{p.command}</CodeBlock> : <CodeBlock>{p.lines.join('\n')}</CodeBlock>}
      {p.reason ? <p className="muted">{p.reason}</p> : null}
      {question || multi ? (
        <ol className="opts">
          {opts.map((o, i) => (
            <li key={o.key}>
              {multi ? (
                <label className="opt">
                  <input type="checkbox" checked={picked.includes(o.key)} disabled={answering} onChange={() => toggle(o.key)} /> {o.label}
                </label>
              ) : (
                <button className="btn opt" disabled={answering} onClick={() => send(o.key, o.key)}>
                  <Kbd>{o.key || i + 1}</Kbd> {mark(o.key, o.label)}
                </button>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <CardActions>
          {opts.map((o) => (
            <button
              key={o.key}
              className={`btn${o.answer === 'yes' || (p.kind !== 'permission' && o.highlighted) ? ' primary' : ''}`}
              disabled={answering}
              onClick={() => send(o.key, o.key)}
            >
              {isAlways(o) ? '↻ ' : ''}
              {mark(o.key, o.label)} <Kbd>{o.key}</Kbd>
            </button>
          ))}
        </CardActions>
      )}
      {opts.some(isAlways) && p.kind === 'dialog' ? <p className="muted note border-b border-line px-3 py-1.5">↻ saves a rule for next time</p> : null}
      {multi ? (
        <CardActions>
          <button className="btn primary" disabled={answering || !picked.length} onClick={submit}>
            {mark('submit', 'Submit')}
          </button>
        </CardActions>
      ) : null}
      {question && !multi ? (
        typing ? (
          <CardActions
            as="form"
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim()) send({ text: text.trim() }, 'text');
            }}
          >
            <Input
              type="text"
              className="compose flex-1"
              autoFocus
              aria-label="Your own answer"
              value={text}
              disabled={answering}
              onChange={(e) => setText(e.target.value)}
            />
            <button className="btn primary" disabled={answering || !text.trim()}>
              {mark('text', 'Send')}
            </button>
          </CardActions>
        ) : (
          <button className="btn opt" disabled={answering} onClick={() => setTyping(true)}>
            Type something
          </button>
        )
      ) : null}
      {cancel ? (
        <CardActions>
          <button className="btn" disabled={answering} onClick={() => send(cancel.key, cancel.key)}>
            {mark(cancel.key, 'Cancel')} <Kbd>esc</Kbd>
          </button>
        </CardActions>
      ) : null}
      {answering ? <p className="muted">{slow ? 'still waiting' : 'answering…'}</p> : null}
    </Card>
  );
}

/** The card, and what stays when the prompt goes after a refused answer: "Answered elsewhere" for 4 s. */
export function PromptSlot({ agent, prompt }: { agent: string; prompt: Prompt | null }) {
  const [gone, setGone] = useState(false);
  const [refused, setRefused] = useState(false);
  useEffect(() => {
    if (!prompt && refused) setGone(true);
    setRefused(false); // a refusal that left a prompt open is over once the card shows it
  }, [prompt, refused]);
  useEffect(() => {
    if (!gone) return;
    const t = setTimeout(() => setGone(false), 4000);
    return () => clearTimeout(t);
  }, [gone]);
  return (
    <AnimatedList>
      {prompt ? (
        <AnimatedItem key="prompt">
          <PromptCard agent={agent} prompt={prompt} onStale={() => setRefused(true)} />
        </AnimatedItem>
      ) : gone ? (
        <AnimatedItem key="gone">
          <p className="muted">Answered elsewhere</p>
        </AnimatedItem>
      ) : null}
    </AnimatedList>
  );
}
