// The message box: Enter sends, Shift+Enter breaks the line, the up arrow recalls what was sent, `/` and `#` open menus, Ctrl+R lists the history,
// and an unsent draft is kept per agent. A message taken back (cancel) puts its text here again.
import { useEffect, useRef, useState } from 'react';
import { isOpenAsk, type KV, type Verb } from '@handline/app-core';
import type { AgentDetail, ChatMessage } from '@handline/client';
import { useAgents, useChatActions } from '@handline/app-data';
import { setting } from './daemon.ts';
import { EditorButton } from './EditorButton.tsx';
import { replyTo } from './ChatMessages.tsx';
import { menuOf, useEntries, useOpenTerminal, type Entry, type MenuKind } from './ChatMenus.tsx';
import { VerbSheet } from './AgentVerbs.tsx';
import { Button, Icon } from './ui/index.ts';

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
}) {
  const { agent, detail, storage, canSend, history, messages, target, onClear, restore } = props;
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

  const asked = ctrlR ? { kind: 'history' as MenuKind, query: '' } : menuOf(text);
  const open = asked && shut !== text ? asked : null;
  const entries = useEntries(agent, detail, open?.kind ?? null, open?.query ?? '', messages, history);
  const menu = open && entries.length ? entries : null;

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
  const send = () => {
    if (!text.trim()) return;
    // Replying to a question that waits for you answers it; to anything else sends a linked message to whoever said it.
    if (target && isOpenAsk(target, agent)) act.answer(target.id, text);
    else if (target) act.send(text, { parent: target.id, to: replyTo(target, (n) => names.has(n)) });
    else act.send(text);
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
    <div className="compose-wrap">
      {target ? (
        <div className="compose-ref" role="status">
          <Icon name="reference" size={14} label={`Replying to #${target.id}`} />
          <span className="mono">#{target.id}</span>
          <span className="compose-ref-body muted">{target.body}</span>
          <button className="icon" aria-label="Drop the reply" title="Drop the reply (Esc)" onClick={onClear}>
            <Icon name="close" size={14} label="Drop the reply" />
          </button>
        </div>
      ) : null}
      {verb && view ? <VerbSheet a={view} verb={verb} onClose={() => setVerb(null)} /> : null}
      <div className="compose-row">
        {menu ? (
          <ul
            className="compose-menu"
            role="listbox"
            aria-label={open!.kind === 'slash' ? 'Verbs' : open!.kind === 'hash' ? 'References' : 'History'}
          >
            {menu.map((e, i) => (
              <li key={e.key} role="option" aria-selected={i === Math.min(at, menu.length - 1)}>
                <button type="button" className="ui-prow" onMouseDown={(ev) => ev.preventDefault()} onClick={() => choose(e)}>
                  <Icon name={e.icon} size={14} label={e.label} aria-hidden />
                  <span className="ui-prow-name">{e.label}</span>
                  {e.hint ? <span className="ui-prow-hint">{e.hint}</span> : null}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <textarea
          ref={box}
          className="compose"
          rows={2}
          value={text}
          disabled={!canSend}
          aria-label="Message to the agent"
          placeholder={canSend ? 'Send a message…' : 'This daemon does not accept chat messages'}
          onChange={(e) => {
            back.current = -1;
            setCtrlR(false);
            setAt(0);
            set(e.target.value);
          }}
          onKeyDown={onKey}
        />
        {clearing ? (
          <span className="compose-clear" role="status" title="Esc again clears the draft">
            <Icon name="warn" size={14} label="Esc again clears the draft" />
          </span>
        ) : null}
        {canSend ? <EditorButton text={text} onText={set} /> : null}
        <Button
          className="compose-send"
          variant="solid"
          icon="send"
          label="Send"
          title="Send (Enter)"
          disabled={!canSend || !text.trim()}
          onClick={send}
        />
      </div>
    </div>
  );
}
