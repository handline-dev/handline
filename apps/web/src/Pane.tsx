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
  splitRoot,
  splitTab,
  type Edge,
  type Group,
  type Preset,
  type Tab,
} from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { ChatTab } from './ChatTab.tsx';
import { Icon, type IconName } from './ui/index.ts';
import { TasksTab } from './TasksTab.tsx';
import { Badges } from './Badges.tsx';
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
const EDGES: Edge[] = ['left', 'right', 'top', 'bottom'];

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
      className={`pane${ws.active === group.id ? ' focus' : ''}`}
      data-group={group.id}
      onPointerDownCapture={() => ws.active !== group.id && apply((w) => ({ ...w, active: group.id }))}
    >
      <div className="strip" role="tablist" ref={strip} onDragOver={(e) => dragging && e.preventDefault()} onDrop={dropOnStrip}>
        {group.tabs.map((id) => {
          const t = ws.tabs[id]!;
          return (
            <div
              key={id}
              role="tab"
              tabIndex={0}
              aria-selected={id === group.activeTab}
              className={`tab${id === group.activeTab ? ' on' : ''}`}
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
              <span className="tab-label">{label(t, structured)}</span>
              {t.kind === 'terminal' || t.kind === 'transcript' ? (
                <>
                  <Badges badges={badges} onClick={{ dialog: () => apply((w) => activate(w, CHAT)) }} />
                  {unread && id !== group.activeTab ? <span className="unread-dot" title="Needs you" /> : null}
                </>
              ) : null}
              {id === CHAT ? null : (
                <button
                  className="x"
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
        <span className="strip-end">
          <details className="menu">
            <summary className="icon" aria-label="Add a tab" title="Add a tab">
              <Icon name="plus" size={14} aria-hidden />
            </summary>
            <div className="menu-list">
              <button
                className="menu-item"
                onClick={(e) => {
                  e.currentTarget.closest('details')!.removeAttribute('open');
                  apply((w) => openTab(w, 'tasks', {}, group.id));
                }}
              >
                <Icon name="tasks" size={14} aria-hidden /> Tasks
              </button>
              {structured ? (
                <button
                  className="menu-item"
                  onClick={(e) => {
                    e.currentTarget.closest('details')!.removeAttribute('open');
                    apply((w) => openTab(w, 'transcript', {}, group.id));
                  }}
                >
                  <Icon name="terminal" size={14} aria-hidden /> Transcript
                </button>
              ) : detail.terminal ? (
                <button
                  className="menu-item"
                  onClick={(e) => {
                    e.currentTarget.closest('details')!.removeAttribute('open');
                    apply((w) => openTab(w, 'terminal', { handle: detail.terminal! }, group.id));
                  }}
                >
                  <Icon name="terminal" size={14} aria-hidden /> Claude Code
                </button>
              ) : (
                <span className="menu-item muted">
                  <Icon name="noTerminal" size={14} label="No terminal: start the agent first" />
                </span>
              )}
            </div>
          </details>
          <details className="menu">
            <summary className="icon" aria-label="Layout" title="Layout">
              <Icon name="layout" size={14} aria-hidden />
            </summary>
            <div className="menu-list">
              {PRESETS.map(([p, name]) => (
                <button
                  key={p}
                  className="menu-item"
                  onClick={(e) => {
                    e.currentTarget.closest('details')!.removeAttribute('open');
                    apply((w) => applyPreset(w, p));
                  }}
                >
                  {name}
                </button>
              ))}
            </div>
          </details>
        </span>
      </div>
      <div className="pane-body" role="tabpanel">
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

/** The edges of a pane (or, with `root`, of the whole view) that a dragged tab can be dropped on to split. */
export function DropZones({ group, root }: { group?: string; root?: boolean }) {
  const { ws, dragging, drop } = useWorkspace();
  const [over, setOver] = useState<Edge | null>(null);
  return (
    <>
      {EDGES.map((edge) => (
        <div
          key={edge}
          className={`dz${root ? ' root' : ''} ${edge}${over === edge ? ' over' : ''}`}
          data-edge={edge}
          data-root={root ? 'true' : undefined}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(edge);
          }}
          onDragLeave={() => setOver(null)}
          onDrop={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setOver(null);
            if (dragging) drop(root ? splitRoot(ws, dragging, edge) : splitTab(ws, dragging, group!, edge));
          }}
        />
      ))}
    </>
  );
}
