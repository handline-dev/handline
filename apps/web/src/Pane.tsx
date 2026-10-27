// One pane: its own tab strip, the active tab, and while a tab is dragged, the edges it can be dropped on.
import { useEffect, useRef, useState } from 'react';
import { useScreenKeys, type KeyRows } from '#src/screenKeys.ts';
import {
  badgesOf,
  needsAttention,
  closeTab,
  fillTab,
  splitPane,
  CHAT,
  moveTabBy,
  openTab,
  taskRef,
  type Group,
  type Tab,
  type TabKind,
  type TabParams,
  type Workspace,
} from '@handline/app-core';
import { useHello, useMe, useStore } from '@handline/app-react';
import { ChatTab } from '#src/ChatTab.tsx';
import { Button, Icon, MenuButton, MenuItem, type IconName } from '#src/ui/index.ts';
import { TasksTab } from '#src/TasksTab.tsx';
import { Badges } from '#src/Badges.tsx';
import { DropOverlay } from '#src/DropOverlay.tsx';
import { DeviceTab } from '#src/DeviceTab.tsx';
import { WebTab } from '#src/WebTab.tsx';
import { BytesTab } from '#src/BytesTab.tsx';
import { wantsBytes } from '#src/terminal/choose.ts';
import { Watch } from '#src/TranscriptTab.tsx';
import { TerminalTab } from '#src/TerminalTab.tsx';
import { ChatStrip } from '#src/ChatCards.tsx';
import { useWorkspace } from '#src/Workspace.tsx';
import { goSettings } from '#src/route.ts';
import { RailControls, useFoldedControls } from '#src/RailControls.tsx';

const label = (t: Tab, structured = false): string =>
  t.kind === 'chat'
    ? 'Chat'
    : t.kind === 'new'
      ? 'New tab'
      : t.kind === 'tasks'
        ? t.params.pinned
          ? taskRef(Number(t.params.task))
          : 'Tasks'
        : t.kind === 'android'
          ? 'Android'
          : t.kind === 'ios'
            ? 'iOS'
            : t.kind === 'web'
              ? String(t.params.url ?? 'Preview').replace(/^https?:\/\//, '')
              : t.kind === 'transcript' || (t.kind === 'terminal' && structured)
                ? 'Transcript'
                : t.kind === 'terminal'
                  ? 'Claude Code'
                  : t.kind === 'shell'
                    ? 'Terminal'
                    : `${t.kind}: ${Object.values(t.params)[0] ?? ''}`;
const TAB_ICON: Record<string, IconName> = {
  chat: 'chat',
  tasks: 'tasks',
  terminal: 'terminal',
  transcript: 'terminal',
  shell: 'terminal',
  web: 'web',
  android: 'device',
  ios: 'device',
  new: 'plus',
};

const TAB_KEYS: KeyRows = [
  ['Alt+Left Alt+Right', 'move the focused tab along the strip'],
  ['Enter Space', 'show the focused tab'],
];

export function Pane({ group }: { group: Group }) {
  useScreenKeys('On the tabs', TAB_KEYS);
  const { ws, detail, agent, storage, apply, show, canSplit, lead, dragging, setDragging } = useWorkspace();
  const store = useStore();
  const folded = useFoldedControls();
  const strip = useRef<HTMLDivElement>(null);
  const tab = ws.tabs[group.activeTab];
  const me = useMe();
  const hello = useHello();
  const badges = badgesOf(detail, me);
  const structured = detail.structured === true;
  // What a new tab can show: the same flat list for the + menu and for the picker in a freshly split pane.
  const choices: { kind: TabKind; params: TabParams; label: string; icon: IconName; disabled?: boolean; open?: boolean }[] = [
    // A new plain shell in the agent's worktree, not its Claude Code terminal (that one is its own tab); several may be open.
    { kind: 'shell', params: {}, label: 'Terminal', icon: 'terminal', open: true },
    { kind: 'tasks', params: {}, label: 'Tasks', icon: 'tasks' },
    { kind: 'android', params: {}, label: 'Android', icon: 'device' },
    { kind: 'ios', params: {}, label: 'iOS', icon: 'device' },
  ];
  /** Opens what a choice shows; a new shell is first asked of the daemon and then shown by its handle. */
  const choose = async (c: (typeof choices)[number], place: (w: Workspace, kind: TabKind, params: TabParams) => Workspace) => {
    let params = c.params;
    if (c.open) {
      try {
        params = { handle: (await store.client.call('shell.open', { agent })).terminal };
      } catch (e) {
        return store.reportError('open a terminal', e);
      }
    }
    apply((w) => place(w, c.kind, params));
  };
  // A terminal tab that is not showing gets a dot when its agent newly needs someone (a dialog, a stall); showing it clears the dot.
  // The first terminal tab of the group: a group holds one terminal tab per agent, so the unread dot follows that one.
  const termId = group.tabs.find((id) => ws.tabs[id]?.kind === 'terminal' || ws.tabs[id]?.kind === 'transcript');
  const termShown = termId !== undefined && termId === group.activeTab;
  const attention = needsAttention(badges);
  const wasAttention = useRef(attention);
  const [unread, setUnread] = useState(false);
  useEffect(() => {
    if (attention && !wasAttention.current && termId !== undefined && !termShown) setUnread(true);
    wasAttention.current = attention;
  }, [attention, termId, termShown]);
  useEffect(() => {
    if (termShown) setUnread(false);
  }, [termShown]);

  const hiddenActive = group.tabs.includes(CHAT) && ['terminal', 'transcript'].includes(ws.tabs[group.activeTab]?.kind ?? '');
  return (
    <section
      className={`pane relative flex min-h-0 min-w-0 flex-1 flex-col bg-canvas -outline-offset-1${ws.active === group.id ? ' focus outline-1 outline-[color-mix(in_srgb,var(--primary)_40%,transparent)]' : ''}`}
      data-group={group.id}
      onPointerDownCapture={() => ws.active !== group.id && apply((w) => ({ ...w, active: group.id }))}
    >
      <div className="strip flex h-[var(--h-tabs)] items-stretch border-b border-line bg-surface px-2" role="tablist" ref={strip}>
        {folded && lead === group.id ? (
          <div className="mr-1 flex flex-none items-center shell:pl-[var(--lights-w,86px)]">
            <RailControls show={folded.show} row />
          </div>
        ) : null}
        {group.tabs.map((id) => {
          const t = ws.tabs[id]!;
          // Claude Code is reached by the switch (Cmd/Ctrl+J, /terminal, the icon in the bottom bar), not by a tab, while the chat is in the group.
          const asleep = id === CHAT && hiddenActive; // Claude Code is showing: the Chat tab stays the strip's one tab, quiet
          const viaSwitch = (t.kind === 'terminal' || t.kind === 'transcript') && group.tabs.includes(CHAT);
          return (
            <div
              key={id}
              role="tab"
              tabIndex={0}
              aria-selected={id === group.activeTab || asleep}
              className={`tab group ${viaSwitch ? 'hidden' : 'inline-flex'} my-1 max-w-[200px] cursor-default select-none items-center gap-1.5 whitespace-nowrap rounded-ctl px-2.5 font-sans text-body font-medium hover:bg-raised ${id === group.activeTab ? 'on bg-raised text-ink' : asleep ? 'text-ink-2 bg-raised/50' : 'text-ink-2'}`}
              data-tab={id}
              draggable={id !== CHAT}
              onDragStart={(e) => {
                e.dataTransfer?.setData('text/plain', id);
                // The drop cover appears over the tab being dragged; mounting it inside dragstart makes Chromium cancel the drag.
                setTimeout(() => setDragging(id), 0);
              }}
              onDragEnd={() => setDragging(null)}
              onClick={() => show(id)}
              onKeyDown={(e) => {
                if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
                  e.preventDefault();
                  apply((w) => moveTabBy(w, id, e.key === 'ArrowLeft' ? -1 : 1));
                } else if (e.key === 'Enter' || e.key === ' ') show(id);
              }}
            >
              <Icon name={TAB_ICON[t.kind] ?? 'tasks'} size={14} label={label(t, structured)} aria-hidden />
              <span className="tab-label overflow-hidden text-ellipsis">{label(t, structured)}</span>
              {t.kind === 'terminal' || t.kind === 'transcript' ? (
                <>
                  <Badges badges={badges} onClick={{ dialog: () => show(CHAT) }} />
                  {unread && id !== group.activeTab ? (
                    <span className="unread-dot ml-1 inline-block h-[7px] w-[7px] rounded-full bg-wait" title="Needs you" />
                  ) : null}
                </>
              ) : null}
              {id === CHAT ? null : (
                <button
                  className="x inline-flex border-0 bg-transparent p-0.5 text-ink-3 opacity-0 focus-visible:opacity-100 group-hover:opacity-100 group-aria-selected:opacity-100"
                  aria-label={`Close ${label(t, structured)}`}
                  title={
                    t.kind === 'terminal' || t.kind === 'transcript'
                      ? 'Close the tab; the agent keeps running'
                      : t.kind === 'shell'
                        ? 'Close the terminal'
                        : 'Close the tab'
                  }
                  onClick={(e) => {
                    e.stopPropagation();
                    if (
                      t.kind === 'terminal' &&
                      !structured &&
                      detail.session_open &&
                      !confirm(`Close the Claude Code tab of ${agent}? The agent keeps running.`)
                    )
                      return;
                    if (t.kind === 'shell') void store.client.call('shell.close', { terminal: String(t.params.handle) }).catch(() => {}); // the shell ends with its tab
                    apply((w) => closeTab(w, id));
                  }}
                >
                  <Icon name="close" size={12} label="Close" aria-hidden />
                </button>
              )}
            </div>
          );
        })}
        <MenuButton icon="plus" size="sm" label="Add a tab" name="Add a tab" className="ml-0.5 self-center">
          {choices.map((c) => (
            <MenuItem
              key={c.label}
              icon={c.icon}
              disabled={c.disabled}
              onSelect={() => void choose(c, (w, kind, params) => openTab(w, kind, params, group.id))}
            >
              {c.label}
            </MenuItem>
          ))}
        </MenuButton>
        <Button
          icon="split"
          size="sm"
          label="Split this pane"
          title={canSplit(group.id) ? undefined : 'The window is too narrow for another pane'}
          disabled={!canSplit(group.id)}
          className="ml-auto self-center"
          onClick={() => apply((w) => splitPane(w, group.id) ?? w)}
        />
        <Button icon="settings" size="sm" label={`${agent} settings`} className="self-center" onClick={() => goSettings(agent)} />
      </div>
      <div className="pane-body relative flex min-h-0 flex-1 flex-col [&>*]:min-h-0 [&>*]:flex-1" role="tabpanel">
        {!tab ? null : tab.kind === 'chat' ? (
          <ChatTab agent={agent} detail={detail} />
        ) : tab.kind === 'new' ? (
          <div className="new-tab flex flex-col items-center justify-center gap-3 p-6">
            <p className="m-0 text-ink-2">Open in this pane</p>
            <div className="grid w-full max-w-sm grid-cols-[repeat(auto-fill,minmax(88px,1fr))] gap-1">
              {choices.map((c) => (
                <button
                  key={c.label}
                  type="button"
                  className="tile flex aspect-square flex-col items-center justify-center gap-2 rounded-card border-0 bg-transparent text-body text-ink hover:enabled:bg-raised disabled:opacity-50"
                  disabled={c.disabled}
                  onClick={() => void choose(c, (w, kind, params) => fillTab(w, tab.id, kind, params))}
                >
                  <Icon name={c.icon} size={20} aria-hidden />
                  {c.label}
                </button>
              ))}
            </div>
          </div>
        ) : tab.kind === 'tasks' ? (
          <TasksTab agent={agent} task={typeof tab.params.task === 'number' ? tab.params.task : null} />
        ) : tab.kind === 'web' ? (
          <WebTab
            agent={agent}
            url={typeof tab.params.url === 'string' ? tab.params.url : ''}
            hidden={!!dragging}
            onUrl={(u) => apply((w) => ({ ...w, tabs: { ...w.tabs, [tab.id]: { ...tab, params: { ...tab.params, url: u } } } }))}
          />
        ) : tab.kind === 'android' || tab.kind === 'ios' ? (
          <DeviceTab
            agent={agent}
            kind={tab.kind}
            hidden={!!dragging}
            picked={typeof tab.params.device === 'string' ? tab.params.device : ''}
            onPick={(device) => apply((w) => ({ ...w, tabs: { ...w.tabs, [tab.id]: { ...tab, params: { ...tab.params, device } } } }))}
          />
        ) : tab.kind === 'shell' ? (
          <BytesTab key={tab.id} agent={agent} detail={{ terminal: String(tab.params.handle) }} handle={String(tab.params.handle)} shell />
        ) : tab.kind === 'terminal' || tab.kind === 'transcript' ? (
          <Watch
            agent={agent}
            structured={structured}
            terminal={
              <div className="flex min-h-0 flex-col [&>:nth-last-child(2)]:flex-1">
                {wantsBytes(hello?.capabilities.terminal_format, storage) ? (
                  <BytesTab agent={agent} detail={detail} />
                ) : (
                  <TerminalTab agent={agent} detail={detail} />
                )}
                <div className="chat-foot flex h-8 max-w-none! flex-none items-center border-t border-line px-3">
                  <ChatStrip detail={detail} to="chat" />
                </div>
              </div>
            }
          />
        ) : (
          <div className="empty big">
            <strong>{label(tab, structured)}</strong>
            <p>This daemon does not serve {tab.kind} tabs. Close this tab.</p>
          </div>
        )}
      </div>
      {dragging ? <DropOverlay group={group} strip={strip} /> : null}
    </section>
  );
}
