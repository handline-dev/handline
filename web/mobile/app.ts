// Browser entry: connects to the daemon, draws views.ts into #app and delegates its controls.
import { DaemonClient } from '../src/client.ts';
import { webSocketTransport } from '../src/transport.ts';
import { Store } from './store.ts';
import { app } from './views.ts';

const root = document.getElementById('app')!;
// The daemon's WebSocket: ?daemon=ws://host:port, else the page's own host on the fake daemon's port.
const daemonUrl = new URLSearchParams(location.search).get('daemon') ?? `ws://${location.hostname}:7777`;
const store = new Store(new DaemonClient({
  client: 'mobile', reconnect: { minMs: 500, maxMs: 8000 },
  connect: () => webSocketTransport(daemonUrl),
}));

function draw(): void {
  // Keep what the person is typing across redraws.
  const typed = root.querySelector<HTMLInputElement>('form input, form textarea');
  const keep = typed ? { name: typed.name, value: typed.value, focus: document.activeElement === typed } : null;
  const grid = root.querySelector('.grid');
  const stuck = grid ? grid.scrollHeight - grid.scrollTop - grid.clientHeight < 40 : true;
  root.innerHTML = app(store.state);
  if (keep) {
    const el = root.querySelector<HTMLInputElement>(`form [name="${keep.name}"]`);
    if (el) { el.value = keep.value; if (keep.focus) el.focus(); }
  }
  const g = root.querySelector('.grid');
  if (g && stuck) g.scrollTop = g.scrollHeight;
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
