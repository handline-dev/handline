// One pane: its own tab strip, the active tab, and while a tab is dragged, the edges it can be dropped on.
import { useEffect, useRef, useState } from 'react';
import { useScreenKeys, type KeyRows } from './screenKeys.ts';
import {
  badgesOf,
  needsAttention,
  closeTab,
  fillTab,
  splitPane,
  CHAT,
  moveTab,
  moveTabBy,
  openTab,
  taskRef,
  type Group,
  type Tab,
  type TabKind,
  type TabParams,
} from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { ChatTab } from './ChatTab.tsx';
import { Button, Icon, MenuButton, MenuItem, type IconName } from './ui/index.ts';
import { TasksTab } from './TasksTab.tsx';
import { TimelineTab } from './TimelineTab.tsx';
import { ActivityTab } from './ActivityTab.tsx';
import { Badges } from './Badges.tsx';
import { DropZones } from './DropZones.tsx';
import { ChangesTab } from './ChangesTab.tsx';
import { BytesTab } from './BytesTab.tsx';
import { wantsBytes } from './terminal/choose.ts';
import { Watch } from './TranscriptTab.tsx';
import { TerminalTab } from './TerminalTab.tsx';
import { ViewSwitch } from './ViewSwitch.tsx';
import { useWorkspace } from './Workspace.tsx';
import { RailControls, useFoldedControls } from './RailControls.tsx';

const label = (t: Tab, structured = false): string =>
  t.kind === 'chat'
    ? 'Chat'
    : t.kind === 'new'
      ? 'New tab'
    : t.kind === 'tasks'
      ? t.params.pinned
        ? taskRef(Number(t.params.task))
        : 'Tasks'
      : t.kind === 'activity'
        ? 'Activity'
      : t.kind === 'timeline'
        ? 'Timeline'
        : t.kind === 'changes'
          ? 'Changes'
          : t.kind === 'transcript' || (t.kind === 'terminal' && structured)
          ? 'Transcript'
          : t.kind === 'terminal'
            ? 'Claude Code'
            : `${t.kind}: ${Object.values(t.params)[0] ?? ''}`;
const TAB_ICON: Record<string, IconName> = { chat: 'chat', tasks: 'tasks', terminal: 'terminal', transcript: 'terminal', timeline: 'history', activity: 'team', changes: 'changes', new: 'plus' };

const TAB_KEYS: KeyRows = [
  ['Alt+Left Alt+Right', 'move the focused tab along the strip'],
  ['Enter Space', 'show the focused tab'],
];

export function Pane({ group }: { group: Group }) {
  useScreenKeys('On the tabs', TAB_KEYS);
  const { ws, detail, agent, storage, apply, show, canSplit, lead, dragging, setDragging } = useWorkspace();
  const folded = useFoldedControls();
  const store = useStore();
  const strip = useRef<HTMLDivElement>(null);
  const tab = ws.tabs[group.activeTab];
  const badges = badgesOf(detail, store.me);
  // What a new tab can show: the same list for the + menu and for the picker in a freshly split pane.
  const choices: { kind: TabKind; params: TabParams; label: string; icon: IconName; disabled?: boolean }[] = [
    { kind: 'tasks', params: {}, label: 'Tasks', icon: 'tasks' },
    { kind: 'activity', params: {}, label: 'Activity', icon: 'team' },
    { kind: 'timeline', params: {}, label: 'Timeline', icon: 'history' },
    { kind: 'changes', params: {}, label: 'Changes', icon: 'changes', disabled: !detail.worktree },
  ];
  const structured = detail.structured === true;
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
  const dropOnStrip = (e: React.DragEvent) => {
    if (!dragging) return;
    e.preventDefault();
    const mids = [...strip.current!.querySelectorAll<HTMLElement>('[role=tab]')].map((el) => {
      const r = el.getBoundingClientRect();
      return r.left + r.width / 2;
    });
    const at = mids.findIndex((m) => e.clientX < m);
    apply((w) => moveTab(w, dragging, group.id, at < 0 ? undefined : at));
    setDragging(null);
  };
  return (
    <section
      className={`pane relative flex min-h-0 min-w-0 flex-1 flex-col bg-canvas -outline-offset-1${ws.active === group.id ? ' focus outline-1 outline-[color-mix(in_srgb,var(--primary)_40%,transparent)]' : ''}`}
      data-group={group.id}
      onPointerDownCapture={() => ws.active !== group.id && apply((w) => ({ ...w, active: group.id }))}
    >
      <div className="strip flex h-[var(--h-tabs)] items-stretch border-b border-line bg-surface px-2" role="tablist" ref={strip} onDragOver={(e) => dragging && e.preventDefault()} onDrop={dropOnStrip}>
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
              className={`tab group ${viaSwitch ? 'hidden' : 'inline-flex'} max-w-[200px] cursor-default select-none items-center gap-1.5 whitespace-nowrap border-b-2 px-2.5 font-sans text-body font-medium ${id === group.activeTab ? 'on border-b-ink text-ink' : asleep ? 'border-b-line-strong text-ink-2' : 'border-b-transparent text-ink-2'}`}
              data-tab={id}
              draggable
              onDragStart={(e) => {
                e.dataTransfer?.setData('text/plain', id);
                setDragging(id);
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
                  <Badges
                    badges={badges}
                    onClick={{
                      dialog: () => show(CHAT),
                      stalled: () => apply((w) => openTab(w, 'timeline', {}, group.id)),
                    }}
                  />
                  {unread && id !== group.activeTab ? <span className="unread-dot ml-1 inline-block h-[7px] w-[7px] rounded-full bg-wait" title="Needs you" /> : null}
                </>
              ) : null}
              {id === CHAT ? null : (
                <button
                  className="x inline-flex border-0 bg-transparent p-0.5 text-ink-3 opacity-0 focus-visible:opacity-100 group-hover:opacity-100 group-aria-selected:opacity-100"
                  aria-label={`Close ${label(t, structured)}`}
                  title={t.kind === 'terminal' || t.kind === 'transcript' ? 'Close the tab; the agent keeps running' : 'Close the tab'}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (
                      t.kind === 'terminal' &&
                      !structured &&
                      detail.session_open &&
                      !confirm(`Close the Claude Code tab of ${agent}? The agent keeps running.`)
                    )
                      return;
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
            <MenuItem key={c.label} icon={c.icon} disabled={c.disabled} onSelect={() => apply((w) => openTab(w, c.kind, c.params, group.id))}>
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
      </div>
      <div className="pane-body relative flex min-h-0 flex-1 flex-col [&>*]:min-h-0 [&>*]:flex-1" role="tabpanel">
        {!tab ? null : tab.kind === 'chat' ? (
          <ChatTab agent={agent} detail={detail} />
        ) : tab.kind === 'new' ? (
          <div className="new-tab flex flex-col items-center justify-center gap-1 p-6">
            <p className="m-0 mb-2 text-ink-2">Open in this pane</p>
            {choices.map((c) => (
              <Button
                key={c.label}
                variant="outline"
                icon={c.icon}
                label={c.label}
                disabled={c.disabled}
                onClick={() => apply((w) => fillTab(w, tab.id, c.kind, c.params))}
              >
                {c.label}
              </Button>
            ))}
          </div>
        ) : tab.kind === 'tasks' ? (
          <TasksTab agent={agent} task={typeof tab.params.task === 'number' ? tab.params.task : null} />
        ) : tab.kind === 'activity' ? (
          <ActivityTab agent={agent} row={typeof tab.params.row === 'string' ? tab.params.row : null} />
        ) : tab.kind === 'timeline' ? (
          <TimelineTab agent={agent} />
        ) : tab.kind === 'changes' ? (
          <ChangesTab agent={agent} detail={detail} />
        ) : tab.kind === 'terminal' || tab.kind === 'transcript' ? (
          <Watch
            agent={agent}
            structured={structured}
            terminal={
              <div className="flex min-h-0 flex-col [&>:nth-last-child(2)]:flex-1">
                {wantsBytes(store.client.hello?.capabilities.terminal_format, storage) ? (
                  <BytesTab agent={agent} detail={detail} />
                ) : (
                  <TerminalTab agent={agent} detail={detail} />
                )}
                <div className="chat-foot flex h-8 flex-none items-center border-t border-line px-3">
                  <ViewSwitch to="chat" />
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
        {dragging ? <DropZones group={group.id} /> : null}
      </div>
    </section>
  );
}
