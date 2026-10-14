// The browser's xterm: the Terminal, its fit addon and web links, themed from the tokens. Everything else is in core.ts.
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import { colors, font } from '@handline/tokens';
import { DEFAULT_FONT, isEscapeChord, type CreateTerm } from './core.ts';
import '@xterm/xterm/css/xterm.css';

/** Opens a link only after the person has seen where it really goes. Only http(s) is ever offered. */
export function openLink(event: Event, uri: string, confirmFn: (m: string) => boolean = confirm): void {
  let url: URL;
  try {
    url = new URL(uri);
  } catch {
    return;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return;
  if (!confirmFn(`Open ${url.href}?`)) return;
  window.open(url.href, '_blank', 'noopener,noreferrer');
  event.preventDefault();
}

/** OSC 8 hyperlinks go through the same check as matched URLs; xterm's own fallback would open any scheme. */
export const linkHandler = { activate: (event: MouseEvent, uri: string) => openLink(event, uri) };

/** A paste bigger than this asks first: one stray Cmd+V should not type a whole file into an agent. */
export const PASTE_ASK_BYTES = 4096;

/** Ctrl/Cmd+C copies when text is selected and goes no further; without a selection the key is left alone (Ctrl+C is then the interrupt). */
export function copyKey(
  term: { hasSelection(): boolean; getSelection(): string },
  write: (text: string) => unknown = (t) => navigator.clipboard.writeText(t),
) {
  return (e: Pick<KeyboardEvent, 'type' | 'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey' | 'key'>): boolean => {
    const copy = e.type === 'keydown' && (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'c';
    if (!copy || !term.hasSelection()) return true;
    void Promise.resolve(write(term.getSelection())).catch(() => {}); // a refused clipboard write loses the copy, never the key
    return false;
  };
}

/** Holds a paste over PASTE_ASK_BYTES until the person confirms; `paste` is xterm's own, so bracketed paste is kept. A small paste is left to xterm. */
export function pasteGuard(paste: (text: string) => void, confirmFn: (m: string) => boolean = confirm) {
  return (e: { clipboardData: { getData(type: string): string } | null; preventDefault(): void; stopPropagation(): void }): void => {
    const text = e.clipboardData?.getData('text') ?? '';
    const bytes = new TextEncoder().encode(text).length;
    if (bytes <= PASTE_ASK_BYTES) return;
    e.preventDefault();
    e.stopPropagation();
    if (confirmFn(`Paste ${Math.ceil(bytes / 1024)} KB into the terminal?`)) paste(text);
  };
}

export const createXterm: CreateTerm = (host) => {
  const dark = matchMedia?.('(prefers-color-scheme: dark)').matches ?? true;
  const p = colors[dark ? 'dark' : 'light'];
  const term = new Terminal({
    scrollback: 5000,
    fontFamily: font.mono,
    fontSize: DEFAULT_FONT,
    cursorBlink: false,
    allowProposedApi: true,
    linkHandler,
    theme: { background: p.termBg, foreground: p.termInk },
  });
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.loadAddon(new WebLinksAddon(openLink));
  term.open(host);
  const copy = copyKey(term);
  term.attachCustomKeyEventHandler((e) => !isEscapeChord(e) && copy(e)); // everything else goes to the program; the escape chord is the app's
  host.addEventListener(
    'paste',
    pasteGuard((t) => term.paste(t)),
    true,
  ); // before xterm's own listener on its textarea
  return {
    term,
    propose: () => fit.proposeDimensions() ?? null,
    setFontSize: (px) => {
      term.options.fontSize = px;
    },
    mark: (label) => {
      const marker = term.registerMarker(0);
      const tick = marker && term.registerDecoration({ marker, x: 0, width: 1 });
      tick?.onRender((el) => {
        el.classList.add('daemon-tick', 'border-l-2', 'border-l-primary', 'opacity-60');
        el.title = label; // text only: the label comes from the stream's notices
      });
    },
    focus: () => term.focus(),
    blur: () => term.blur(),
    dispose: () => term.dispose(),
  };
};
