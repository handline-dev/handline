// Terminal tab: the agent session's live text grid. Attached only while this tab is rendered. The tab's own strip holds who may
// type (it takes the input when typing starts and gives it back when typing stops), and the key chips; Start, Stop and Resume are in the sidebar.
import { useEffect, useRef, useState } from 'react';
import type { AgentDetail, TerminalView } from '@handline/client';
import { controlsOf, keyFrom } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { Skeleton, useWrite } from '@handline/app-data';
import { fontFor } from './terminal/core.ts';
import { drawRows } from './terminal/spans.ts';
import { useInputHold } from './useInputHold.ts';
import { Chips, TermBar } from './TermBar.tsx';

/** A write's outcome as a flag: a refusal is already shown by the write (the toast), so the caller only needs to know whether to go on. */
const done = (write: Promise<unknown>) =>
  write.then(
    () => true,
    () => false,
  );

export function TerminalTab({ agent, detail }: { agent: string; detail: AgentDetail }) {
  const store = useStore();
  const grid = useRef<HTMLPreElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const view = useRef<TerminalView | null>(null);
  const [status, setStatus] = useState('');
  const [attaching, setAttaching] = useState(false); // the grid's skeleton shows until the terminal is attached or refused
  const typing = useWrite('session.input', agent);
  const typingBytes = useWrite('terminal.input', agent);
  const keys = useWrite('session.keys', agent);
  const hasTerminal = !!detail.terminal;
  const c = controlsOf(detail, store.me);
  const hold = useInputHold(agent, detail);
  const line = useRef<Promise<unknown>>(Promise.resolve()); // keys go out in the order they were typed, even while the first one takes the input
  const inOrder = (f: () => Promise<unknown>) => (line.current = line.current.then(f, f));
  const dialogOpen = !!detail.prompt || !!detail.has_dialog;

  useEffect(() => {
    const g = grid.current!;
    if (!hasTerminal) {
      g.textContent = '';
      setAttaching(false);
      setStatus('No terminal. Start the agent to open one.');
      return;
    }
    let cancelled = false,
      off: (() => void) | null = null,
      frame = 0;
    setAttaching(true);
    /** The host's columns scaled into the pane's width, as the byte view does when it does not hold the input. */
    const fit = () => {
      if (view.current) g.style.fontSize = `${fontFor(g.clientWidth - 20, view.current.cols)}px`;
    };
    const watch = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(fit);
    watch?.observe(g);
    const paint = (stick = false) => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (!view.current) return;
        const atBottom = stick || g.scrollHeight - g.scrollTop - g.clientHeight < 4,
          before = g.scrollHeight;
        fit();
        g.replaceChildren(drawRows(document, view.current.lines()));
        if (atBottom) g.scrollTop = g.scrollHeight;
        else g.scrollTop += g.scrollHeight - before; // older lines arrived above
      });
    };
    store.client.attachTerminal(agent).then(
      (v) => {
        if (cancelled) {
          void store.client.detachTerminal(agent).catch(() => {});
          return;
        }
        view.current = v;
        setAttaching(false);
        setStatus('');
        off = v.subscribe(() => paint());
        paint(true);
      },
      (e) => {
        if (cancelled) return;
        setAttaching(false);
        setStatus((e as { reason?: string }).reason ?? (e as Error).message);
      },
    );
    return () => {
      cancelled = true;
      watch?.disconnect();
      off?.();
      cancelAnimationFrame(frame);
      if (view.current) {
        view.current = null;
        void store.client.detachTerminal(agent).catch(() => {});
      }
    };
  }, [store, agent, hasTerminal]);

  /** Sends what the textarea collected (typed, pasted or composed) as text without Enter; Enter is its own key. */
  const drain = () => {
    const el = input.current!,
      text = el.value;
    el.value = '';
    if (!text || dialogOpen) return; // a dialog is a menu: only keys pass
    // session.input is refused while the screen is unread; the bytes path is not, so it is used whenever the daemon has one.
    const send = () => {
      const v = view.current;
      return v?.terminal && store.client.typesBytes
        ? typingBytes.mutateAsync(store.client.typedBytes(v.terminal, text, false))
        : typing.mutateAsync({ agent, text, enter: false });
    };
    void inOrder(
      async () =>
        (await hold.ensure()) &&
        (await done(
          send().then(
            () => setStatus(''),
            (e) => {
              el.value = text + el.value; // kept for a retry
              setStatus((e as { reason?: string }).reason ?? (e as Error).message);
              throw e;
            },
          ),
        )),
    );
  };
  const onKey = (e: React.KeyboardEvent) => {
    const k = keyFrom(e.nativeEvent, dialogOpen);
    if (!k) return;
    e.preventDefault();
    if ('swallow' in k) return;
    if ('unavailable' in k) {
      setStatus(`The daemon cannot take ${k.unavailable} yet.`);
      return;
    }
    drain();
    void inOrder(async () => (await hold.ensure()) && (await done(keys.mutateAsync({ agent, keys: [k.name] }))));
  };
  /** A chip is typing too: it takes the input first, then sends the key. */
  const press = async (key: string) => {
    if (await hold.ensure()) await done(keys.mutateAsync({ agent, keys: [key] }));
  };
  const chipsOn = detail.session_open && (c.typing || c.take);

  return (
    <div className="terminal-tab">
      <TermBar agent={agent} detail={detail} />
      <div className="terminal">
        <pre
          className="grid"
          ref={grid}
          aria-label={`Terminal of ${agent}`}
          onClick={() => {
            if (!getSelection()?.toString()) input.current?.focus();
          }}
          onScroll={(e) => {
            const v = view.current;
            if (e.currentTarget.scrollTop === 0 && v?.historyMore) void store.client.loadOlder(v).catch(() => {});
          }}
        />
        {/* Typing goes through a focused textarea, as in xterm: the browser's input and composition events carry IME text and paste. */}
        <textarea
          className="grid-input"
          ref={input}
          rows={1}
          autoCapitalize="off"
          autoComplete="off"
          spellCheck={false}
          aria-label={`Type into the terminal of ${agent}`}
          onKeyDown={(e) => onKey(e)}
          onInput={(e) => {
            if (!(e.nativeEvent as InputEvent).isComposing) drain();
          }}
          onCompositionEnd={drain}
        />
        <div className="grid-skeleton">
          <Skeleton shape="terminalGrid" loading={attaching} label="the terminal" />
        </div>
        <div className="grid-status">{status}</div>
      </div>
      <Chips enabled={chipsOn} onPress={(key) => void press(key)} />
    </div>
  );
}
