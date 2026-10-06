// Terminal tab: the agent session's live text grid. Attached only while this tab is rendered. The tab's own strip holds who may
// type, Take control and Release, and the key chips; Start, Stop and Resume are in the sidebar.
import { useEffect, useRef, useState } from 'react';
import type { AgentDetail, TerminalView } from '@handline/client';
import { controlsOf, holderLabel, keyFrom } from '@handline/app-core';
import { useStore } from '@handline/app-react';

const CHIPS: [string, string][] = [['esc', 'Esc'], ['ctrl-c', '^C'], ['enter', '↵'], ['up', '↑'], ['down', '↓'], ['tab', 'Tab'], ['shift-tab', '⇧Tab'], ['1', '1'], ['2', '2'], ['3', '3']];
const TAKE = 'Press "Take control" to type here; the daemon stops typing while you hold the keyboard.';

export function TerminalTab({ agent, detail }: { agent: string; detail: AgentDetail }) {
  const store = useStore();
  const grid = useRef<HTMLPreElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const view = useRef<TerminalView | null>(null);
  const [status, setStatus] = useState('');
  const latest = useRef(detail); latest.current = detail; // handlers read the current detail without re-binding
  const hasTerminal = !!detail.terminal;
  const c = controlsOf(detail, store.me);
  const dialogOpen = !!detail.prompt || !!detail.has_dialog;

  useEffect(() => {
    const g = grid.current!;
    if (!hasTerminal) { g.textContent = ''; setStatus('No terminal. Start the agent to open one.'); return; }
    let cancelled = false, off: (() => void) | null = null, frame = 0;
    const paint = (stick = false) => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (!view.current) return;
        const atBottom = stick || g.scrollHeight - g.scrollTop - g.clientHeight < 4, before = g.scrollHeight;
        g.textContent = view.current.text().join('\n');
        if (atBottom) g.scrollTop = g.scrollHeight; else g.scrollTop += g.scrollHeight - before; // older lines arrived above
      });
    };
    store.client.attachTerminal(agent).then((v) => {
      if (cancelled) { void store.client.detachTerminal(agent).catch(() => {}); return; }
      view.current = v; setStatus(''); off = v.subscribe(() => paint()); paint(true);
    }, (e) => { if (!cancelled) setStatus((e as { reason?: string }).reason ?? (e as Error).message); });
    return () => {
      cancelled = true; off?.(); cancelAnimationFrame(frame);
      if (view.current) { view.current = null; void store.client.detachTerminal(agent).catch(() => {}); }
    };
  }, [store, agent, hasTerminal]);

  const holding = (): boolean => {
    const yes = controlsOf(latest.current, store.me).typing;
    if (!yes) setStatus(TAKE);
    return yes;
  };
  /** Sends what the textarea collected (typed, pasted or composed) as text without Enter; Enter is its own key. */
  const drain = async () => {
    const el = input.current!, text = el.value; el.value = '';
    if (!text || dialogOpen || !holding()) return; // a dialog is a menu: only keys pass
    await store.act('typing', () => store.client.sendInput(agent, text, false));
  };
  const onKey = async (e: React.KeyboardEvent) => {
    const k = keyFrom(e.nativeEvent, dialogOpen);
    if (!k) return;
    e.preventDefault();
    if ('swallow' in k || !holding()) return;
    if ('unavailable' in k) { setStatus(`The daemon cannot take ${k.unavailable} yet.`); return; }
    await drain();
    await store.act('key', () => store.client.sendKeys(agent, k.name));
  };
  /** A chip is typing, so it takes the keyboard first, then sends the key. */
  const press = async (key: string) => {
    if (!controlsOf(latest.current, store.me).typing && !(await store.act('take control', () => store.client.takeInput(agent)))) return;
    await store.act('key', () => store.client.sendKeys(agent, key));
  };
  const chipsOn = detail.session_open && (c.typing || c.take);

  return (
    <div className="terminal-tab">
      <div className="term-bar">
        <span className="holder" title="Who may type now">input: {holderLabel(detail.input_holders?.[0], store.me)}</span>
        {c.take ? <button className="btn" onClick={() => void store.act('take control', () => store.client.takeInput(agent))}>Take control</button> : null}
        {c.release ? <button className="btn" onClick={() => void store.act('release', () => store.client.releaseInput(agent))}>Release</button> : null}
      </div>
      <div className="terminal">
        <pre className="grid" ref={grid} aria-label={`Terminal of ${agent}`}
          onClick={() => { if (!getSelection()?.toString()) input.current?.focus(); }}
          onScroll={(e) => { const v = view.current; if (e.currentTarget.scrollTop === 0 && v?.historyMore) void store.client.loadOlder(v).catch(() => {}); }} />
        {/* Typing goes through a focused textarea, as in xterm: the browser's input and composition events carry IME text and paste. */}
        <textarea className="grid-input" ref={input} rows={1} autoCapitalize="off" autoComplete="off" spellCheck={false} aria-label={`Type into the terminal of ${agent}`}
          onKeyDown={(e) => void onKey(e)} onInput={(e) => { if (!(e.nativeEvent as InputEvent).isComposing) void drain(); }} onCompositionEnd={() => void drain()} />
        <div className="grid-status">{status}</div>
      </div>
      <div className="chips">
        {CHIPS.map(([key, label]) => <button key={key} className="chip" disabled={!chipsOn} title={`Send ${label}`} onClick={() => void press(key)}>{label}</button>)}
      </div>
    </div>
  );
}
