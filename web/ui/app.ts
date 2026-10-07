// The frame: top bar, agent rail, tabs, context panel and status bar around the screens.
import { DaemonClient, RpcError, webSocketTransport } from '@handline/client';
import { AgentPage } from './agent-page.ts';
import { h, keyed } from './dom.ts';
import { ago, dotOf, railGroups, type Dot, type GroupBy, type KV } from '@handline/app-core';
import { Store } from '../legacy/store.ts';

const storage: KV | null = (() => {
  try {
    return localStorage;
  } catch {
    return null;
  }
})();
const get = (k: string, d: string) => {
  try {
    return storage?.getItem(k) ?? d;
  } catch {
    return d;
  }
};
const put = (k: string, v: string) => {
  try {
    storage?.setItem(k, v);
  } catch {
    /* blocked storage: the setting just does not persist */
  }
};

const params = new URLSearchParams(location.search);
const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);
// The daemon's WebSocket: the page's own host, wss on an https page. Plain ws is for a loopback daemon only.
const url = params.get('daemon') ?? `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.hostname || '127.0.0.1'}:7777`;
const insecure = (() => {
  try {
    const u = new URL(url);
    return u.protocol !== 'wss:' && !LOOPBACK.has(u.hostname);
  } catch {
    return true;
  }
})();
const store = new Store(new DaemonClient({ client: 'web-pc', connect: () => webSocketTransport(url) }));

let openTabs: string[] = JSON.parse(get('tabs', '[]')).filter((x: unknown) => typeof x === 'string');
let groupBy = (get('group', 'team') === 'role' ? 'team' : get('group', 'team')) as GroupBy;
let hideParked = get('hideParked', '') === '1';
let search = '';
let railOpen = get('rail', '1') === '1',
  sideOpen = get('side', '1') === '1';
let side: 'task' | 'worktree' | 'timeline' = 'task';
let page: AgentPage | null = null;

const selected = () => decodeURIComponent(location.hash.match(/^#\/agents\/(.+)$/)?.[1] ?? '');
const go = (name: string) => {
  location.hash = `#/agents/${encodeURIComponent(name)}`;
};

const root = document.getElementById('app')!;
const topbar = h('header', { class: 'topbar' });
const rail = h('nav', { class: 'rail', 'aria-label': 'Agents' });
const tabs = h('div', { class: 'tabstrip', role: 'tablist' });
const main = h('main', { class: 'main' });
const sidebar = h('aside', { class: 'side', 'aria-label': 'Context' });
const status = h('footer', { class: 'statusbar' });
const toast = h('div', { class: 'toast', role: 'status', hidden: true });
root.append(topbar, h('div', { class: 'frame' }, rail, h('div', { class: 'center' }, tabs, main), sidebar), status, toast);

const bell = h('span', { class: 'bell', hidden: true });
function buildTop(): void {
  const later = (t: string) => h('button', { class: 'nav', disabled: true, title: 'Not built yet' }, t);
  topbar.replaceChildren(
    h(
      'button',
      {
        class: 'icon',
        'aria-label': 'Toggle the agent list',
        onclick: () => {
          railOpen = !railOpen;
          put('rail', railOpen ? '1' : '0');
          layout();
        },
      },
      '☰',
    ),
    h('strong', { class: 'brand' }, 'Handline'),
    h('input', {
      class: 'search',
      type: 'search',
      placeholder: 'Search agents',
      'aria-label': 'Search agents',
      value: search,
      oninput: (e: Event) => {
        search = (e.target as HTMLInputElement).value;
        renderRail();
      },
    }),
    h('span', { class: 'spacer' }),
    h('button', { class: 'nav', disabled: true, title: 'Not built yet' }, 'Inbox', bell),
    later('Tasks'),
    later('Team'),
    later('Accounts'),
    h(
      'button',
      {
        class: 'icon',
        'aria-label': 'Toggle the context panel',
        onclick: () => {
          sideOpen = !sideOpen;
          put('side', sideOpen ? '1' : '0');
          layout();
        },
      },
      '▥',
    ),
  );
}
/** The bell counts agents waiting on a person; it also leads the browser tab title. */
function renderBell(): void {
  const waiting = [...store.agents.values()].filter((a) => dotOf(a) === 'waiting').length;
  bell.hidden = !waiting;
  bell.textContent = String(waiting);
  document.title = waiting ? `(${waiting}) Handline` : 'Handline';
}

const railList = h('div', { class: 'rail-list' });
function buildRail(): void {
  const select = (label: string, value: string, opts: string[], on: (v: string) => void) =>
    h(
      'select',
      { 'aria-label': label, onchange: (e: Event) => on((e.target as HTMLSelectElement).value) },
      ...opts.map((o) => h('option', { value: o, selected: o === value }, `${label} ${o}`)),
    );
  rail.append(
    h(
      'div',
      { class: 'rail-opts' },
      select('by', groupBy, ['team', 'state', 'account'], (v) => {
        groupBy = v as GroupBy;
        put('group', v);
        renderRail();
      }),
      h(
        'label',
        { class: 'check' },
        h('input', {
          type: 'checkbox',
          checked: hideParked,
          onchange: (e: Event) => {
            hideParked = (e.target as HTMLInputElement).checked;
            put('hideParked', hideParked ? '1' : '');
            renderRail();
          },
        }),
        ' hide parked',
      ),
    ),
    railList,
  );
}

/** Rebuilt only when a row it shows changed; the options above it are never rebuilt, so an open pick stays open. */
function renderRail(): void {
  const all = [...store.agents.values()];
  const groups = railGroups(all, groupBy, { hidden: hideParked ? new Set<Dot>(['parked']) : undefined, search });
  keyed(railList, [groups, selected(), all.length, store.conn], () => [
    ...(all.length ? [] : [h('p', { class: 'muted pad' }, store.conn === 'open' ? 'No agents.' : 'Connecting…')]),
    ...groups.map((g) =>
      h(
        'section',
        { class: 'group' },
        h('h3', null, g.title),
        ...g.rows.map(({ agent: a, depth }) =>
          h(
            'button',
            {
              class: `row${a.name === selected() ? ' on' : ''}${dotOf(a) === 'waiting' ? ' attn' : ''}`,
              style: `padding-left:${10 + depth * 14}px`,
              onclick: () => go(a.name),
            },
            h('span', { class: `dot ${dotOf(a)}` }),
            h(
              'span',
              { class: 'row-main' },
              h('span', { class: 'name' }, a.name),
              h(
                'span',
                { class: 'sub' },
                [a.state, a.task ? `#${a.task.id} ${a.task.title}` : null, ago(a.state_since)].filter(Boolean).join(' · '),
              ),
            ),
            h('span', { class: 'row-side' }, [a.model, a.account].filter(Boolean).join(' · ')),
          ),
        ),
      ),
    ),
  ]);
}

function renderTabs(): void {
  const cur = selected();
  keyed(tabs, [openTabs, cur], () =>
    openTabs.map((n) =>
      h(
        'div',
        { class: `tab${n === cur ? ' on' : ''}`, role: 'tab', 'aria-selected': String(n === cur) },
        h('button', { class: 'tab-label', onclick: () => go(n) }, n, h('span', { class: 'muted' }, ' Agent')),
        h('button', { class: 'x', 'aria-label': `Close ${n}`, onclick: () => closeTab(n) }, '✕'),
      ),
    ),
  );
}
function closeTab(n: string): void {
  const i = openTabs.indexOf(n);
  openTabs = openTabs.filter((x) => x !== n);
  put('tabs', JSON.stringify(openTabs));
  if (n === selected()) {
    const next = openTabs[Math.min(i, openTabs.length - 1)];
    if (next) go(next);
    else location.hash = '#/agents';
  } else renderTabs();
}

function renderSide(): void {
  const n = selected(),
    d = n ? store.details.get(n) : undefined;
  const row = (k: string, v: string) => h('div', { class: 'kv' }, h('span', { class: 'muted' }, k), h('span', null, v));
  const body = !d
    ? [h('p', { class: 'muted pad' }, 'Select an agent.')]
    : side === 'task'
      ? [
          d.task ? row('request', `#${d.task.id} ${d.task.title}`) : h('p', { class: 'muted pad' }, 'No current request.'),
          row('session', d.session_id ?? 'none'),
          row('terminal', d.terminal ?? 'none'),
        ]
      : side === 'worktree'
        ? [row('branch', d.worktree?.branch ?? 'none'), row('path', d.worktree?.path ?? 'none')]
        : (store.notes.get(n) ?? [])
            .slice()
            .reverse()
            .map((x) => h('div', { class: 'note' }, h('span', { class: 'muted' }, ago(x.at)), ` ${x.text}`));
  if (side === 'timeline' && !body.length) body.push(h('p', { class: 'muted pad' }, 'Nothing since this page opened.'));
  keyed(sidebar, [n, side, d, store.notes.get(n)?.length], () => [
    h(
      'div',
      { class: 'tabs' },
      ...(['task', 'worktree', 'timeline'] as const).map((t) =>
        h(
          'button',
          {
            class: `tab${side === t ? ' on' : ''}`,
            onclick: () => {
              side = t;
              renderSide();
            },
          },
          t[0].toUpperCase() + t.slice(1),
        ),
      ),
    ),
    ...body,
  ]);
}

function renderStatus(): void {
  const all = [...store.agents.values()];
  const dot = store.conn === 'open' ? 'running' : store.conn === 'closed' ? 'blocked' : 'waiting';
  const accounts = [...new Set(all.map((a) => a.account).filter(Boolean))];
  status.replaceChildren(
    h('span', null, h('span', { class: `dot ${dot}` }), ` daemon ${store.conn}`),
    h('span', null, `${all.length} agents · ${all.filter((a) => a.state === 'running').length} running`),
    h('span', { class: 'muted' }, accounts.length ? `accounts ${accounts.join(', ')}` : ''),
  );
  if (store.error) {
    toast.hidden = false;
    toast.textContent = store.error;
  } else toast.hidden = true;
}

function layout(): void {
  root.dataset.rail = railOpen ? 'open' : 'closed';
  root.dataset.side = sideOpen ? 'open' : 'closed';
}

function route(): void {
  const n = selected();
  page?.dispose();
  page = null;
  if (n) {
    if (!openTabs.includes(n)) {
      openTabs.push(n);
      put('tabs', JSON.stringify(openTabs));
    }
    page = new AgentPage(store, n, storage);
    main.replaceChildren(page.el);
  } else
    main.replaceChildren(
      h('div', { class: 'empty big' }, h('strong', null, 'Pick an agent'), h('p', null, 'Choose one in the list to see its page.')),
    );
  renderTabs();
  renderRail();
  renderSide();
}

function paint(): void {
  renderBell();
  renderRail();
  renderTabs();
  renderSide();
  renderStatus();
}

buildTop();
buildRail();
layout();
route();
paint();
addEventListener('hashchange', route);
store.subscribe(paint);
/** Connects; when the daemon cannot be reached the page says so and tries again until it can. */
function boot(): void {
  if (insecure) {
    store.error = `Refusing ${url}: use wss:// for a daemon that is not on this machine.`;
    renderStatus();
    return;
  }
  store.start().then(
    () => {
      store.error = null;
      renderStatus();
    },
    (e) => {
      const fatal = e instanceof RpcError && e.code === 'protocol';
      store.error = fatal ? (e.reason ?? e.message) : `Cannot reach the daemon at ${url}; trying again.`;
      renderStatus();
      if (!fatal) setTimeout(boot, 2000);
    },
  );
}
boot();
