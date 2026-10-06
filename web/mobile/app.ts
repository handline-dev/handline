// Browser entry: connects to the daemon, draws views.ts into #app and delegates its controls.
import { DaemonClient } from '../src/client.ts';
import { webSocketTransport } from '../src/transport.ts';
import { Store } from './store.ts';
import { app } from './views.ts';

const root = document.getElementById('app')!;
// The daemon's WebSocket: ?daemon=wss://host:port, else the page's own host and scheme (wss on an https page; plain ws only for local dev).
const daemonUrl = new URLSearchParams(location.search).get('daemon') ?? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.hostname}:7777`;
const store = new Store(new DaemonClient({
  client: 'mobile', reconnect: { minMs: 500, maxMs: 8000 },
  connect: () => webSocketTransport(daemonUrl),
}));

const template = document.createElement('template');

/** Redraws the page when it changes; otherwise patches only the regions whose HTML changed, so a streaming terminal
 *  never replaces the composer (IME, caret, focus) or the grid element (the reader's scroll position). */
function draw(): void {
  template.innerHTML = app(store.state);
  const next = template.content.firstElementChild!;
  const cur = root.firstElementChild as HTMLElement | null;
  if (!cur || cur.dataset.key !== (next as HTMLElement).dataset.key) {
    root.replaceChildren(next);
    const g = root.querySelector('.grid');
    if (g) g.scrollTop = g.scrollHeight;
    return;
  }
  for (const n of next.querySelectorAll<HTMLElement>('[data-r]')) {
    const c = cur.querySelector<HTMLElement>(`[data-r="${n.dataset.r}"]`);
    if (!c || c.innerHTML === n.innerHTML) continue;
    const stuck = c.classList.contains('grid') && c.scrollHeight - c.scrollTop - c.clientHeight < 40;
    c.innerHTML = n.innerHTML;
    if (stuck) c.scrollTop = c.scrollHeight;
  }
}
store.subscribe(draw);

root.addEventListener('click', (ev) => {
  const el = (ev.target as Element).closest<HTMLElement>('[data-act]');
  if (!el || el instanceof HTMLFormElement) return;
  const agent = store.state.page?.agent ?? '';
  const d = el.dataset;
  switch (d.act) {
    case 'open': void store.openAgent(d.agent!); break;
    case 'back': void store.closeAgent(); break;
    case 'tab': store.setTab(d.tab as 'chat' | 'terminal'); break;
    case 'start': void store.startAgent(agent); break;
    case 'resume': void store.resume(agent); break;
    case 'interrupt': void store.interrupt(agent); break;
    case 'stop':
      if (d.mode !== 'kill' || confirm(`Kill ${agent}'s terminal?`)) void store.stopAgent(agent, d.mode as 'exit' | 'close' | 'kill');
      break;
    case 'key': void store.key(agent, d.key!); break;
    case 'answer': { const p = store.state.page?.detail.prompt; if (p && p.id === d.prompt) void store.answer(p, d.key!); break; }
    case 'older': void store.loadOlder(); break;
    case 'dismiss': store.dismissError(); break;
  }
});
root.addEventListener('submit', (ev) => {
  ev.preventDefault();
  const form = ev.target as HTMLFormElement;
  const field = form.elements.namedItem('text') as HTMLInputElement;
  void store.send(store.state.page!.agent, field.value);
  field.value = '';
});

store.start().catch((e) => { root.innerHTML = `<div class="banner err">Cannot reach the daemon at ${daemonUrl}: ${e.message}</div>`; });
