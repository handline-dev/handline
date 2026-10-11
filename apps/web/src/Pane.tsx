// One pane: its own tab strip, the active tab, and while a tab is dragged, the edges it can be dropped on.
import { useEffect, useRef, useState } from 'react';
import {
  activate,
  applyPreset,
  badgesOf,
  needsAttention,
  closeTab,
  CHAT,
  moveTab,
  moveTabBy,
  openTab,
  type Group,
  type Preset,
  type Tab,
} from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { ChatTab } from './ChatTab.tsx';
import { Button, Icon, Popup, PopupRow, type IconName } from './ui/index.ts';
import { TasksTab } from './TasksTab.tsx';
import { Badges } from './Badges.tsx';
import { DropZones } from './DropZones.tsx';
import { BytesTab } from './BytesTab.tsx';
import { wantsBytes } from './terminal/choose.ts';
import { Watch } from './TranscriptTab.tsx';
import { TerminalTab } from './TerminalTab.tsx';
import { useWorkspace } from './Workspace.tsx';

const label = (t: Tab, structured = false): string =>
  t.kind === 'chat'
    ? 'Chat'
    : t.kind === 'tasks'
      ? 'Tasks'
      : t.kind === 'transcript' || (t.kind === 'terminal' && structured)
        ? 'Transcript'
        : t.kind === 'terminal'
          ? 'Claude Code'
          : `${t.kind}: ${Object.values(t.params)[0] ?? ''}`;
const TAB_ICON: Record<string, IconName> = { chat: 'chat', tasks: 'tasks', terminal: 'terminal', transcript: 'terminal' };
const PRESETS: [Preset, string][] = [
  ['single', 'One pane'],
  ['cols', 'Two columns'],
  ['rows', 'Two rows'],
  ['grid', '2 × 2'],
];

export function Pane({ group }: { group: Group }) {
  const { ws, detail, agent, storage, apply, dragging, setDragging } = useWorkspace();
  const store = useStore();
  const strip = useRef<HTMLDivElement>(null);
  const tab = ws.tabs[group.activeTab];
  const badges = badgesOf(detail, store.me);
  const structured = detail.structured === true;
  // A terminal tab that is not showing gets a dot when its agent newly needs someone (a dialog, a stall); showing it clears the dot.
  // The first terminal tab of the group: a group holds one terminal tab per agent, so the unread dot follows that one.
  const termId = group.tabs.find((id) => ws.tabs[id]?.kind === 'terminal' || ws.tabs[id]?.kind === 'transcript');
  const termShown = termId !== undefined && termId === group.activeTab;
  const attention = needsAttention(badges);
  const wasAttention = useRef(attention);
  const [unread, setUnread] = useState(false);
  const [menu, setMenu] = useState<'tab' | 'layout' | null>(null);
  useEffect(() => {
    if (attention && !wasAttention.current && termId !== undefined && !termShown) setUnread(true);
    wasAttention.current = attention;
  }, [attention, termId, termShown]);
  useEffect(() => {
    if (termShown) setUnread(false);
  }, [termShown]);

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
      className={`pane relative flex min-h-0 min-w-0 flex-1 flex-col bg-panel -outline-offset-1${ws.active === group.id ? ' focus outline-1 outline-[color-mix(in_srgb,var(--primary)_40%,transparent)]' : ''}`}
      data-group={group.id}
      onPointerDownCapture={() => ws.active !== group.id && apply((w) => ({ ...w, active: group.id }))}
    >
      <div className="strip flex h-[var(--h-tabs)] items-stretch border-b border-line bg-surface px-2" role="tablist" ref={strip} onDragOver={(e) => dragging && e.preventDefault()} onDrop={dropOnStrip}>
        {group.tabs.map((id) => {
          const t = ws.tabs[id]!;
          return (
            <div
              key={id}
              role="tab"
              tabIndex={0}
              aria-selected={id === group.activeTab}
              className={`tab group inline-flex max-w-[200px] cursor-default select-none items-center gap-1.5 whitespace-nowrap border-b-2 px-3 font-sans text-body font-medium ${id === group.activeTab ? 'on border-b-ink text-ink' : 'border-b-transparent text-ink-2'}`}
              data-tab={id}
              draggable
              onDragStart={(e) => {
                e.dataTransfer?.setData('text/plain', id);
                setDragging(id);
              }}
              onDragEnd={() => setDragging(null)}
              onClick={() => apply((w) => activate(w, id))}
              onKeyDown={(e) => {
                if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
                  e.preventDefault();
                  apply((w) => moveTabBy(w, id, e.key === 'ArrowLeft' ? -1 : 1));
                } else if (e.key === 'Enter' || e.key === ' ') apply((w) => activate(w, id));
              }}
            >
              <Icon name={TAB_ICON[t.kind] ?? 'tasks'} size={14} label={label(t, structured)} aria-hidden />
              <span className="tab-label overflow-hidden text-ellipsis">{label(t, structured)}</span>
              {t.kind === 'terminal' || t.kind === 'transcript' ? (
                <>
                  <Badges badges={badges} onClick={{ dialog: () => apply((w) => activate(w, CHAT)) }} />
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
        <span className="strip-end relative ml-auto flex items-center gap-0.5">
          <Button
            icon="plus"
            size="sm"
            label="Add a tab"
            aria-haspopup="menu"
            aria-expanded={menu === 'tab'}
            onClick={() => setMenu(menu === 'tab' ? null : 'tab')}
          />
          <Popup open={menu === 'tab'} onClose={() => setMenu(null)} title="Add a tab" align="right">
            <PopupRow icon="tasks" label="Tasks" onClick={() => (setMenu(null), apply((w) => openTab(w, 'tasks', {}, group.id)))} />
            {structured ? (
              <PopupRow icon="terminal" label="Transcript" onClick={() => (setMenu(null), apply((w) => openTab(w, 'transcript', {}, group.id)))} />
            ) : detail.terminal ? (
              <PopupRow
                icon="terminal"
                label="Claude Code"
                onClick={() => (setMenu(null), apply((w) => openTab(w, 'terminal', { handle: detail.terminal! }, group.id)))}
              />
            ) : (
              <span className="ui-prow text-ink-3">
                <Icon name="noTerminal" size={14} label="No terminal: start the agent first" />
              </span>
            )}
          </Popup>
          <Button
            icon="layout"
            size="sm"
            label="Layout"
            aria-haspopup="menu"
            aria-expanded={menu === 'layout'}
            onClick={() => setMenu(menu === 'layout' ? null : 'layout')}
          />
          <Popup open={menu === 'layout'} onClose={() => setMenu(null)} title="Layout" align="right">
            {PRESETS.map(([p, name]) => (
              <PopupRow key={p} label={name} onClick={() => (setMenu(null), apply((w) => applyPreset(w, p)))} />
            ))}
          </Popup>
        </span>
      </div>
      <div className="pane-body relative flex min-h-0 flex-1 flex-col [&>*]:min-h-0 [&>*]:flex-1" role="tabpanel">
        {!tab ? null : tab.kind === 'chat' ? (
          <ChatTab agent={agent} detail={detail} />
        ) : tab.kind === 'tasks' ? (
          <TasksTab agent={agent} />
        ) : tab.kind === 'terminal' || tab.kind === 'transcript' ? (
          <Watch
            agent={agent}
            structured={structured}
            terminal={
              wantsBytes(store.client.hello?.capabilities.terminal_format, storage) ? (
                <BytesTab agent={agent} detail={detail} />
              ) : (
                <TerminalTab agent={agent} detail={detail} />
              )
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
