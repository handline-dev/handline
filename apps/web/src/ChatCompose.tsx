// The message box: Enter sends, Shift+Enter breaks the line, the up arrow recalls what was sent, `/` and `#` open menus, Ctrl+R lists the history,
// and an unsent draft is kept per agent. A message taken back (cancel) puts its text here again.
import { useEffect, useRef, useState } from 'react';
import { isOpenAsk, type KV, type Verb } from '@handline/app-core';
import type { AgentDetail, ChatMessage } from '@handline/client';
import { useAgents, useChatActions } from '@handline/app-data';
import { setting } from './daemon.ts';
import { replyTo } from './ChatMessages.tsx';
import { menuOf, useEntries, useOpenTerminal, type Entry, type MenuKind } from './ChatMenus.tsx';
import { VerbSheet } from './AgentVerbs.tsx';
import { useScreenKeys, type KeyRows } from './screenKeys.ts';
import { Button, Icon, PROW, PROW_HINT, PROW_NAME, Textarea, ComposeRow } from './ui/index.ts';

const COMPOSE_KEYS: KeyRows = [
  ['Enter', 'send the message'],
  ['Shift+Enter', 'a new line in the message'],
  ['Up Down', 'walk through what you sent before'],
  ['Ctrl+R', 'search what you sent'],
  ['/ Tab', 'the verbs of this agent'],
  ['#', 'refer to a task or a message'],
  ['Esc Esc', 'clear the box (the draft is kept)'],
];

export function Compose(props: {
  agent: string;
  detail: AgentDetail;
  storage: KV | null;
  canSend: boolean;
  history: string[];
  /** The messages the chat holds, for the `#` menu. */
  messages: ChatMessage[];
  /** The message this one answers or replies to; Escape or the close mark drops it. */
  target?: ChatMessage | null;
  onClear?: () => void;
  /** Text to put back in the box (a message that was taken back); `n` tells one from the next. */
  restore?: { text: string; n: number } | null;
  /** The agent is mid-turn: a message to a structured agent is then queued behind it or joins the turn. */
  running?: boolean;
}) {
  const { agent, detail, storage, canSend, history, messages, target, onClear, restore, running } = props;
  useScreenKeys('In the message box', COMPOSE_KEYS);
  const act = useChatActions(agent);
  const agents = useAgents().data?.agents ?? [];
  const names = new Set(agents.map((a) => a.name));
  const view = agents.find((a) => a.name === agent);
  const draft = setting(storage, `handline.draft.${agent}`, '');
  const [text, setText] = useState(draft.value);
  const [ctrlR, setCtrlR] = useState(false);
  const [shut, setShut] = useState<string | null>(null); // the text a menu was closed on: it stays closed until the text changes
  const [at, setAt] = useState(0);
  const [verb, setVerb] = useState<Verb | null>(null);
  const [clearing, setClearing] = useState(false);
  const [refused, setRefused] = useState<string | null>(null); // a /word this agent does not take: said, never sent
  const [now, setNow] = useState(false); // join the running turn instead of queueing behind it; back to queue after each send
  const back = useRef(-1);
  const box = useRef<HTMLTextAreaElement>(null);
  const openTerminal = useOpenTerminal(detail);
  const set = (v: string) => {
    setText(v);
    draft.save(v);
    setClearing(false);
  };

  const seen = useRef(0);
  useEffect(() => {
    if (!restore || restore.n === seen.current) return;
    seen.current = restore.n;
    set(text.trim() ? `${restore.text}\n${text}` : restore.text);
    box.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restore]);

  const asked = ctrlR ? { kind: 'history' as MenuKind, query: text } : menuOf(text);
  const open = asked && shut !== text ? asked : null;
  const entries = useEntries(agent, detail, open?.kind ?? null, open?.query ?? '', messages, history);
  const menu = open && entries.length ? entries : null;
  // The verb list again, for a typed /word with the menu closed.
  const typed = /^\/(\w+)(?:\s+(.*))?$/.exec(text.trim());
  const slashEntries = useEntries(agent, detail, typed ? 'slash' : null, typed?.[1] ?? '', messages, history);

  const choose = (e: Entry) => {
    setCtrlR(false);
    if (e.verb) {
      set('');
      setVerb(e.verb);
    } else if (e.terminal) {
      set('');
      openTerminal();
    } else if (e.insert !== undefined) {
      set(open?.kind === 'hash' ? text.replace(/#\w*$/, e.insert) : e.insert);
    }
    box.current?.focus();
  };

  // The message shows at once and the box clears; a refused one stays in the list as a failed row with Retry, so its words are not lost.
  const choosing = !!running && !!view?.structured;
  const send = () => {
    if (!text.trim() || ctrlR) return;
    // A / line is a command, never words to the agent: run it or refuse it here.
    if (typed) {
      const id = typed[2] === 'fresh' ? `${typed[1]}_fresh` : typed[1]!;
      const hit = slashEntries.find((e) => e.key === id);
      if (hit?.verb || hit?.terminal) {
        set('');
        setRefused(null);
        if (hit.verb) setVerb(hit.verb);
        else openTerminal();
        return;
      }
      setRefused(`No command /${typed[1]}. / lists what this agent takes.`);
      return;
    }
    setRefused(null);
    const delivery = choosing && now ? ('now' as const) : undefined;
    // Replying to a question that waits for you answers it; to anything else sends a linked message to whoever said it.
    if (target && isOpenAsk(target, agent)) act.answer(target.id, text);
    else if (target) act.send(text, { parent: target.id, to: replyTo(target, (n) => names.has(n)), delivery });
    else act.send(text, { delivery });
    setNow(false);
    onClear?.();
    set('');
    back.current = -1;
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (menu) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        return setAt((a) => (a + (e.key === 'ArrowDown' ? 1 : menu.length - 1)) % menu.length);
      }
      if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') {
        e.preventDefault();
        return choose(menu[Math.min(at, menu.length - 1)]!);
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setCtrlR(false);
        return setShut(text);
      }
    }
    if (e.key === 'r' && e.ctrlKey && history.length) {
      e.preventDefault();
      setAt(0);
      return setCtrlR(!ctrlR);
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      if (target) onClear?.();
      else if (text && !clearing)
        setClearing(true); // a draft is not lost to one Esc
      else if (text) set('');
    } else if (e.key === 'ArrowUp' && (text === '' || back.current >= 0) && history.length) {
      e.preventDefault();
      back.current = Math.min(back.current + 1, history.length - 1);
      setText(history[back.current]!);
    } else if (e.key === 'ArrowDown' && back.current >= 0) {
      e.preventDefault();
      back.current -= 1;
      setText(back.current >= 0 ? history[back.current]! : '');
    }
  };
  return (
    <div className="compose-wrap flex flex-col">
      {/* The reply bar's row is always this tall, so picking a message never shifts the stream to make room. */}
      <div className="mx-3 mb-1 flex h-[26px] items-center">
        {target ? (
          <div className="compose-ref flex min-w-0 flex-1 items-center gap-1.5 rounded-chip bg-panel px-2 py-1 text-[12px]" role="status">
            <Icon
              name="reference"
              size={14}
              label={isOpenAsk(target, agent) ? `Answering #${target.id}` : `Replying to #${target.id}`}
            />
            <span className="mono">#{target.id}</span>
            <span className="compose-ref-body muted min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{target.body}</span>
            <button className="icon" aria-label="Drop the reply" title="Drop the reply (Esc)" onClick={onClear}>
              <Icon name="close" size={14} label="Drop the reply" />
            </button>
          </div>
        ) : null}
      </div>
      {verb && view ? <VerbSheet a={view} verb={verb} onClose={() => setVerb(null)} /> : null}
      {refused ? (
        <p role="status" className="mx-3 flex items-center gap-2 px-1 pb-1 text-label text-bad">
          <Icon name="warn" size={14} aria-hidden /> {refused}
        </p>
      ) : null}
      <ComposeRow>
        {menu ? (
          <ul
            className="compose-menu absolute inset-x-0 bottom-full z-10 m-0 mb-1 max-h-60 list-none overflow-auto rounded-card border border-line-strong bg-panel px-0 py-1 shadow-[var(--shadow-pop)]"
            role="listbox"
            aria-label={open!.kind === 'slash' ? 'Verbs' : open!.kind === 'hash' ? 'References' : 'History'}
          >
            {menu.map((e, i) => (
              <li key={e.key} role="option" aria-selected={i === Math.min(at, menu.length - 1)}>
                <button
                  type="button"
                  className={`${PROW}${i === Math.min(at, menu.length - 1) ? ' [background:var(--bg-3)] shadow-[inset_2px_0_0_var(--ink)]' : ''}`}
                  onMouseDown={(ev) => ev.preventDefault()}
                  onClick={() => choose(e)}
                >
                  <Icon name={e.icon} size={14} label={e.label} aria-hidden />
                  <span className={PROW_NAME}>{e.label}</span>
                  {e.hint ? <span className={PROW_HINT}>{e.hint}</span> : null}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <Textarea
          ref={box}
          className="compose flex-1 resize-none border-0 bg-transparent px-1.5"
          rows={2}
          value={text}
          disabled={!canSend}
          aria-label="Message to the agent"
          placeholder={canSend ? 'Send a message…' : 'This daemon does not accept chat messages'}
          onChange={(e) => {
            back.current = -1;
            setAt(0);
            setRefused(null);
            // Ctrl+R stays on while typing: the words narrow the search.
            set(e.target.value);
          }}
          onKeyDown={onKey}
        />
        {clearing ? (
          <span className="compose-clear self-center text-wait" role="status" title="Esc again clears the draft">
            <Icon name="warn" size={14} label="Esc again clears the draft" />
          </span>
        ) : null}
        {choosing ? (
          <label className="compose-now inline-flex gap-0.5 self-center text-ink-2" title="Join the running turn now; otherwise it waits and is sent when the turn ends">
            <input type="checkbox" checked={now} onChange={(e) => setNow(e.target.checked)} /> Now
          </label>
        ) : null}
        <Button
          className="compose-send rounded-pill"
          variant="solid"
          icon="send"
          label="Send"
          title="Send (Enter)"
          disabled={!canSend || !text.trim()}
          onClick={send}
        />
      </ComposeRow>
    </div>
  );
}
