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
  term.attachCustomKeyEventHandler((e) => !isEscapeChord(e)); // everything else goes to the program; the escape chord is the app's
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
