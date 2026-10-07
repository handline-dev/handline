import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { Terminal } from '@xterm/headless';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store } from '@handline/app-core';
import { DaemonProvider } from '@handline/app-react';
import { DataProvider, useAgentDetail, type BoxComponent } from '@handline/app-data';
import { BytesChannel, fromBase64, toBase64, type ByteEvent } from '@handline/client';
import { setup } from '@handline/fake-daemon/helpers';
import { IDLE_MS } from '../src/useInputHold.ts';
import { BytesTab } from '../src/BytesTab.tsx';
import { guard, paint, type CreateTerm } from '../src/terminal/core.ts';
import { KEEP_MS, dropKept, keep } from '../src/terminal/keep.ts';
import { linkHandler, openLink } from '../src/terminal/xterm.ts';
import { wantsBytes } from '../src/terminal/choose.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const Box: BoxComponent = (p) => <div {...p} />;
const ARCH = 'handline.architect'; // idle with a session and a terminal

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
/** The rows xterm holds, scrollback included, without trailing blanks. */
function rows(t: Terminal): string[] {
  const b = t.buffer.active;
  const all = Array.from({ length: b.length }, (_, i) => b.getLine(i)!.translateToString(true));
  while (all.length && all[all.length - 1] === '') all.pop();
  return all;
}
/** A headless terminal fed by a bytes view, as the pane feeds xterm. */
async function follow(s: ReturnType<typeof setup>) {
  const t = new Terminal({ cols: 80, rows: 24, allowProposedApi: true });
  const { view } = await s.client.bytes.open(ARCH, (e: ByteEvent, v) => paint(t, v, e, () => {}));
  return { t, view };
}

describe('the bytes stream in xterm', () => {
  it('a snapshot plus the stream draws the same screen as a fresh attach, colour and all', async () => {
    const s = setup();
    await s.client.start();
    const a = await follow(s);
    for (let i = 0; i < 30; i++) s.daemon.daemonTypes(ARCH, `line ${i}`); // scrolls the screen: rows rewritten in place
    await until(() => rows(a.t).some((l) => l.includes('line 29')), 'the last line');
    const b = await follow({ ...s, client: s.client });
    await until(() => rows(b.t).some((l) => l.includes('line 29')), 'a fresh attach');
    expect(rows(a.t).slice(-24)).toEqual(rows(b.t).slice(-24));
    const cell = a.t.buffer.active.getLine(a.t.buffer.active.baseY + 23)!.getCell(0)!; // the colour is the host's, from its own codes
    expect(rows(a.t).join('\n')).toContain('> line 29');
    expect(cell.getFgColorMode()).toBeGreaterThan(0);
    s.client.close();
  });

  it('scrollback written from the snapshot history sits above the screen', async () => {
    const s = setup();
    await s.client.start();
    for (let i = 0; i < 40; i++) s.daemon.daemonTypes(ARCH, `old ${i}`);
    const a = await follow(s);
    await until(() => rows(a.t).some((l) => l.includes('old 39')), 'the screen');
    expect(a.t.buffer.active.baseY).toBeGreaterThan(0);
    expect(rows(a.t)[0]).toContain('Claude Code'); // the first line of the session, held as scrollback
    s.client.close();
  });
});

describe('foreign bytes', () => {
  const stream = (t: Terminal, text: string) => new Promise<void>((done) => t.write(text, done));
  it('queries are not answered: onData stays silent for DA, DSR, CPR, DECRQM, window and colour queries', async () => {
    const t = new Terminal({ allowProposedApi: true });
    const sent: string[] = [];
    t.onData((d) => sent.push(d));
    guard(t, () => {});
    await stream(
      t,
      '\x1b[c\x1b[>c\x1b[=c\x1b[5n\x1b[6n\x1b[?6n\x1b[?2004$p\x1b[18t\x1b]10;?\x07\x1b]11;?\x07\x1b]4;1;?\x07\x1bP$qm\x1b\\\x1bP$qr\x1b\\\x1bP+q544e\x1b\\',
    );
    expect(sent).toEqual([]);
    const control = new Terminal({ allowProposedApi: true }); // without the guard xterm does answer, so the test can fail
    const leaked: string[] = [];
    control.onData((d) => leaked.push(d));
    await stream(control, '\x1b[c');
    expect(leaked.length).toBeGreaterThan(0);
    const dcs: string[] = [];
    control.onData((d) => dcs.push(d));
    await stream(control, '\x1bP$qm\x1b\\');
    expect(dcs.length).toBeGreaterThan(0); // DECRQSS is answered without the guard
  });

  it('OSC 52 writes nothing to the clipboard, and a title is capped and stripped, never applied', async () => {
    const write = vi.fn();
    vi.stubGlobal('navigator', { clipboard: { writeText: write } });
    const t = new Terminal({ allowProposedApi: true });
    const titles: string[] = [];
    let renamed = '';
    t.onTitleChange((x) => (renamed = x));
    guard(t, (x) => titles.push(x));
    await stream(t, `\x1b]52;c;${btoa('secret')}\x07\x1b]0;${'x'.repeat(200)}\u0007\x1b]2;\u0001ok\u007f\x07`);
    expect(write).not.toHaveBeenCalled();
    expect(titles[0]).toHaveLength(80);
    expect(titles[1]).toBe('ok');
    expect(renamed).toBe('');
    vi.unstubAllGlobals();
  });

  it('a link is opened only for http(s), after a confirm that names the real URL', () => {
    const open = vi.fn();
    vi.stubGlobal('open', open);
    const ev = new Event('click');
    const no = vi.fn(() => false);
    openLink(ev, 'javascript:alert(1)', no);
    openLink(ev, 'file:///etc/passwd', no);
    expect(no).not.toHaveBeenCalled();
    openLink(ev, 'https://example.com/a', no);
    expect(no).toHaveBeenCalledWith('Open https://example.com/a?');
    expect(open).not.toHaveBeenCalled();
    openLink(ev, 'https://example.com/a', () => true);
    expect(open).toHaveBeenCalledWith('https://example.com/a', '_blank', 'noopener,noreferrer');
    vi.unstubAllGlobals();
  });
});

describe('links and large input', () => {
  it('OSC 8 links go through the same http(s) check as matched URLs', () => {
    const open = vi.fn();
    vi.stubGlobal('open', open);
    linkHandler.activate(new MouseEvent('click'), 'javascript:alert(1)');
    expect(open).not.toHaveBeenCalled();
    vi.stubGlobal('confirm', () => true);
    linkHandler.activate(new MouseEvent('click'), 'https://example.com/');
    expect(open).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it('a 1 MiB paste is encoded whole', () => {
    const big = new Uint8Array(1 << 20).map((_, i) => i % 251);
    const back = fromBase64(toBase64(big));
    expect(back.length).toBe(big.length);
    expect(back.every((b, i) => b === big[i])).toBe(true);
  });
});

describe('re-attach', () => {
  const snap = (seq: number) => ({
    format: 'bytes',
    terminal: 't1',
    seq,
    cols: 80,
    rows: 24,
    data: '',
    alt_screen: false,
    holders: [],
    history: { lines: [], first: 0, more: false },
  });
  it('a hole followed by more notices makes one attach, not one per notice', async () => {
    let attaches = 0;
    let reply!: () => void;
    const call = (async (method: string) => {
      if (method !== 'terminal.attach') return {};
      attaches++;
      if (attaches === 1) return snap(1);
      await new Promise<void>((r) => (reply = r));
      return snap(9);
    }) as never;
    const ch = new BytesChannel(call, call);
    await ch.open('a', () => {});
    const out = (seq: number) => ({ terminal: 't1', seq, data: toBase64(new Uint8Array([65])) });
    for (const seq of [5, 6, 7, 8, 9]) ch.notice('output', out(seq)); // 2, 3, 4 were never heard
    expect(attaches).toBe(2);
    reply();
    await new Promise((r) => setTimeout(r, 0));
    expect(attaches).toBe(2);
  });
});

describe('a text and a bytes attach of one agent', () => {
  it('each keeps its stream after the other detaches, because a detach drops every attach of the terminal', async () => {
    const s = setup();
    await s.client.start();
    const heard: string[] = [];
    await s.client.bytes.open(ARCH, (e) => e.type === 'write' && heard.push('b'));
    await s.client.attachTerminal(ARCH);
    await s.client.detachTerminal(ARCH);
    s.daemon.daemonTypes(ARCH, 'after the text detach');
    await until(() => heard.includes('b'), 'bytes output after the text detach');
    const text = await s.client.attachTerminal(ARCH);
    await s.client.bytes.close(ARCH);
    s.daemon.daemonTypes(ARCH, 'after the bytes detach');
    await until(() => text.frame.some((l: string) => l.includes('after the bytes detach')), 'text output after the bytes detach');
  });
});

describe('which renderer', () => {
  const store = (v: string | null) => ({ getItem: () => v, setItem: () => {} });
  const media = (matches: boolean) => vi.stubGlobal('matchMedia', () => ({ matches }));
  afterEach(() => vi.unstubAllGlobals());
  it('xterm only when the daemon serves bytes, the viewer did not choose the text grid, and it is not a phone held upright', () => {
    media(false);
    expect(wantsBytes(['text', 'bytes'], store(null))).toBe(true);
    expect(wantsBytes(['text'], store(null))).toBe(false);
    expect(wantsBytes(['text', 'bytes'], store('text'))).toBe(false);
    media(true);
    expect(wantsBytes(['text', 'bytes'], store(null))).toBe(false);
  });
});

describe('the pane', () => {
  let root: Root | null = null;
  let box: HTMLElement;
  afterEach(async () => {
    await act(async () => root?.unmount());
    dropKept(); // unmounting keeps the terminal for 30 s; a test must not leave it for the next
    root = null;
    box?.remove();
  });
  function Probe({ create }: { create: CreateTerm }) {
    const d = useAgentDetail(ARCH).data;
    return d ? <BytesTab agent={ARCH} detail={d} create={create} /> : null;
  }
  async function mount(s = setup()) {
    let term!: Terminal;
    const marks: string[] = [];
    const made: Terminal[] = [];
    const create: CreateTerm = () => {
      term = new Terminal({ cols: 80, rows: 24, allowProposedApi: true });
      made.push(term);
      return { term, propose: () => null, setFontSize() {}, mark: (l) => marks.push(l), focus() {}, dispose: () => term.dispose() };
    };
    const store = new Store(s.client);
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(
        <DaemonProvider store={store}>
          <DataProvider daemonId="test" box={Box}>
            <Probe create={create} />
          </DataProvider>
        </DaemonProvider>,
      );
    });
    return { ...s, store, marks, made, term: () => term };
  }

  it('renders the host screen through xterm, follows the stream, and goes stale while the transport is down', async () => {
    const m = await mount();
    await until(() => m.term() && rows(m.term()).some((l) => l.includes('Claude Code v2.1')), 'the attached screen');
    m.daemon.daemonTypes(ARCH, 'hello from the daemon');
    await until(() => rows(m.term()).some((l) => l.includes('hello from the daemon')), 'the stream');
    m.net.down = true;
    m.daemon.dropConnections();
    await until(() => box.querySelector('.xterm-pane.dim'), 'the dimmed pane');
    expect(box.textContent).toContain('Reconnecting…');
    m.daemon.daemonTypes(ARCH, 'typed while away');
    m.net.down = false;
    await until(() => rows(m.term()).some((l) => l.includes('typed while away')), 'the repaint after reconnect');
    expect(box.querySelector('.xterm-pane.dim')).toBeNull();
  });

  it("takes the input by itself on the first key, sends what was typed in order, and ignores the terminal's own reports", async () => {
    const m = await mount();
    await until(() => m.term() && rows(m.term()).length > 0, 'the screen');
    await act(async () => m.term().input('\x1b[<0;5;5M\x1b[I')); // a mouse and a focus report are not typing
    expect(m.daemon.sessions.get(ARCH)!.holder.kind).toBe('daemon');
    expect(m.daemon.sessions.get(ARCH)!.received).toEqual([]);
    await act(async () => m.term().input('x'));
    await act(async () => m.term().input('y')); // typed while the take is still going through
    await until(() => m.daemon.sessions.get(ARCH)!.holder.kind !== 'daemon', 'the hold');
    await until(
      () =>
        m.daemon.sessions
          .get(ARCH)!
          .received.map((r) => r.value)
          .join('') === 'xy',
      'both keys, in order',
    );
    expect(box.querySelector('.take-confirm')).toBeNull();
    await act(async () => m.term().input('z'));
    await until(() => m.daemon.sessions.get(ARCH)!.received.some((r) => r.value === 'z'), 'a key while holding');
  });

  it('a key chip takes the input and sends its key', async () => {
    const m = await mount();
    await until(() => m.term() && rows(m.term()).length > 0, 'the screen');
    await act(async () => box.querySelector<HTMLElement>('.chips .chip')!.click());
    await until(() => m.daemon.sessions.get(ARCH)!.holder.kind !== 'daemon', 'the hold');
    await until(() => m.daemon.sessions.get(ARCH)!.received.length > 0, 'the key');
  });

  it('gives the input back after a quiet spell', async () => {
    const m = await mount();
    await until(() => m.term() && rows(m.term()).length > 0, 'the screen');
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await act(async () => m.term().input('x'));
      await until(() => m.daemon.sessions.get(ARCH)!.holder.kind !== 'daemon', 'the hold');
      await act(async () => void (await vi.advanceTimersByTimeAsync(IDLE_MS + 500)));
      await until(() => m.daemon.sessions.get(ARCH)!.holder.kind === 'daemon', 'the input given back');
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows a skeleton of the screen while the first attach is pending, never a blank pane', async () => {
    vi.useFakeTimers();
    try {
      const s = setup();
      const real = s.client.bytes.open.bind(s.client.bytes);
      s.client.bytes.open = (async (...a: Parameters<typeof real>) => {
        await new Promise((r) => setTimeout(r, 1000));
        return real(...a);
      }) as typeof real;
      await mount(s);
      await act(async () => void (await vi.advanceTimersByTimeAsync(200)));
      expect(box.querySelectorAll('.term-skeleton-row').length).toBeGreaterThan(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("puts the daemon's own typing in the gutter as a marker, not in the stream", async () => {
    const m = await mount();
    await until(() => m.term() && rows(m.term()).length > 0, 'the screen');
    m.daemon.daemonTypes(ARCH, 'run the tests');
    await until(() => m.marks.length === 1, 'the marker');
    expect(m.marks[0]).toBe('daemon typed: run the tests');
  });

  it('keeps a hidden terminal for 30 s: switching back shows the last screen at once, with no new xterm', async () => {
    const m = await mount();
    await until(() => m.term() && rows(m.term()).some((l) => l.includes('Claude Code v2.1')), 'the screen');
    // Only the tab goes away: unmounting the daemon provider would stop the store.
    const around = (child: React.ReactNode) => (
      <DaemonProvider store={m.store}>
        <DataProvider daemonId="test" box={Box}>
          {child}
        </DataProvider>
      </DaemonProvider>
    );
    await act(async () => root!.render(around(null)));
    expect(m.made).toHaveLength(1);
    expect(rows(m.made[0]!).some((l) => l.includes('Claude Code v2.1'))).toBe(true); // not disposed: still holds the screen
    await act(async () =>
      root!.render(
        around(
          <Probe
            create={() => {
              throw new Error('a kept terminal is reused, not made again');
            }}
          />,
        ),
      ),
    );
    await until(() => box.querySelector('.xterm-host > div'), 'the terminal again');
    expect(m.made).toHaveLength(1); // the kept instance was reused
  });
});

describe('the keep', () => {
  it('disposes a hidden terminal once 30 s are up, and not before', () => {
    vi.useFakeTimers();
    const gone = vi.fn();
    const stage = document.createElement('div');
    const pane = { term: new Terminal(), propose: () => null, setFontSize() {}, mark() {}, focus() {}, dispose: gone };
    keep('x', { stage, pane, quiet: { dispose() {} }, title: { to() {} } });
    vi.advanceTimersByTime(KEEP_MS - 1);
    expect(gone).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(gone).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
