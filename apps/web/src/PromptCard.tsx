// The card for a prompt the agent is stuck on: a permission (Yes or No, never "always"), a dialog (its own options, "always" ones only when the
// daemon can keep a rule), a question with numbered options, free text and, for a multi prompt, checkboxes with a Submit.
// The answer is a decided write: the clicked option shows "…", the rest are disabled, and a prompt that changed sends nothing.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import type { Prompt } from '@handline/client';
import { clockTime, isAlways, promptTitle } from '@handline/app-core';
import { usePromptAnswer } from '@handline/app-data';
import { AnimatedItem, AnimatedList } from '#src/AnimatedList.tsx';
import { Icon } from '#src/ui/Icon.tsx';
import { Button, CardActions, CodeBlock, Input, Kbd, Check } from '#src/ui/index.ts';

export function PromptCard({ agent, prompt: p, onStale }: { agent: string; prompt: Prompt; onStale?: () => void }) {
  const { answering, picked, typing, setTyping, text, setText, changed, all, cancel, opts, multi, question, send, toggle, mark } =
    usePromptAnswer(agent, p, onStale);
  const [slow, setSlow] = useState(false);
  const card = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!answering) return setSlow(false);
    const t = setTimeout(() => setSlow(true), 10_000);
    return () => clearTimeout(t);
  }, [answering]);

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

  return (
    <div
      className="card prompt my-2 flex flex-col border-t border-line pt-3"
      role="alert"
      ref={card}
      tabIndex={-1}
      onKeyDown={onKey}
      aria-busy={answering}
    >
      <div className="card-title mb-1.5 font-semibold">
        <Icon name="warn" size={13} aria-hidden className="mr-1 inline-block align-[-2px]" /> {promptTitle(p)}
        <span className="muted font-normal">{p.since ? ` · waiting since ${clockTime(p.since)}` : ''}</span>
      </div>
      {changed ? (
        <p className="text-wait m-0 mb-1.5" role="status">
          This prompt changed
        </p>
      ) : null}
      {p.command ? <Folded cmd text={p.command} /> : <CodeBlock scroll>{p.lines.join('\n')}</CodeBlock>}
      {p.reason ? <p className="muted">{p.reason}</p> : null}
      {question || multi ? (
        <ol className="opts m-0 mt-2 grid list-none gap-1.5 p-0">
          {opts.map((o, i) => (
            <li key={o.key}>
              {multi ? (
                <label className="flex items-center gap-2 text-left">
                  <Check checked={picked.includes(o.key)} disabled={answering} onChange={() => toggle(o.key)} /> {o.label}
                </label>
              ) : (
                <Button
                  variant="outline"
                  label={o.label}
                  className="w-full justify-start text-left max-sm:min-h-12"
                  disabled={answering}
                  onClick={() => send(o.key, o.key)}
                >
                  <Kbd>{o.key || i + 1}</Kbd> {mark(o.key, o.label)}
                </Button>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <CardActions>
          {opts.map((o) => (
            <Button
              key={o.key}
              variant={o.answer === 'yes' || (p.kind !== 'permission' && o.highlighted) ? 'solid' : 'outline'}
              label={o.label}
              className="max-sm:min-h-12 max-sm:w-full"
              disabled={answering}
              onClick={() => send(o.key, o.key)}
            >
              {isAlways(o) ? <Icon name="retry" size={11} aria-hidden className="mr-0.5 inline-block align-[-1px]" /> : null}
              {mark(o.key, o.label)} <Kbd>{o.key}</Kbd>
            </Button>
          ))}
        </CardActions>
      )}
      {opts.some(isAlways) && p.kind === 'dialog' ? (
        <p className="muted note mt-1.5 mb-0 border-b border-line px-3 py-1.5 text-sm">
          <Icon name="retry" size={11} aria-hidden className="mr-1 inline-block align-[-1px]" /> saves a rule for next time
        </p>
      ) : null}
      {multi ? (
        <CardActions>
          <Button
            variant="solid"
            label="Submit"
            className="max-sm:min-h-12 max-sm:w-full"
            disabled={answering || !picked.length}
            onClick={submit}
          >
            {mark('submit', 'Submit')}
          </Button>
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
            <Button
              type="submit"
              variant="solid"
              label="Send"
              className="max-sm:min-h-12 max-sm:w-full"
              disabled={answering || !text.trim()}
            >
              {mark('text', 'Send')}
            </Button>
          </CardActions>
        ) : (
          <Button
            variant="outline"
            label="Type something"
            className="w-full justify-start text-left max-sm:min-h-12"
            disabled={answering}
            onClick={() => setTyping(true)}
          />
        )
      ) : null}
      {cancel ? (
        <CardActions>
          <Button
            variant="outline"
            label="Cancel"
            className="max-sm:min-h-12 max-sm:w-full"
            disabled={answering}
            onClick={() => send(cancel.key, cancel.key)}
          >
            {mark(cancel.key, 'Cancel')} <Kbd>esc</Kbd>
          </Button>
        </CardActions>
      ) : null}
      {answering ? <p className="muted">{slow ? 'still waiting' : 'answering…'}</p> : null}
    </div>
  );
}

const FOLD_LINES = 5;

/** A command at full card width, folded to a few lines with a toggle when it runs long. */
function Folded({ cmd, text }: { cmd?: boolean; text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.split('\n').length > FOLD_LINES || text.length > 400;
  return (
    <>
      <CodeBlock cmd={cmd} scroll={open || !long} className={long && !open ? 'max-h-[calc(5lh+0.375rem)] overflow-hidden' : undefined}>
        {text}
      </CodeBlock>
      {long ? (
        <Button
          variant="ghost"
          size="sm"
          label={open ? 'Show less' : 'Show all'}
          aria-label={open ? 'Show less' : 'Show all'}
          className="mt-1 self-start"
          onClick={() => setOpen(!open)}
        />
      ) : null}
    </>
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
