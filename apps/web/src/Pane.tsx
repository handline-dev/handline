// One pane: the active tab of its group, a thin handle when the window is split (its tabs are in the top bar, PaneTabs), and while a tab is dragged, the edges it can be dropped on.
import { useRef } from 'react';
import { fillTab, groupsOf, moveTab, CHAT, type Group } from '@handline/app-core';
import { ChatTab } from '#src/ChatTab.tsx';
import { Icon } from '#src/ui/index.ts';
import { TasksTab } from '#src/TasksTab.tsx';
import { DropOverlay } from '#src/DropOverlay.tsx';
import { DeviceTab } from '#src/DeviceTab.tsx';
import { WebTab } from '#src/WebTab.tsx';
import { BytesTab } from '#src/BytesTab.tsx';
import { wantsBytes } from '#src/terminal/choose.ts';
import { Watch } from '#src/TranscriptTab.tsx';
import { TerminalTab } from '#src/TerminalTab.tsx';
import { ChatStrip } from '#src/ChatCards.tsx';
import { useWorkspace } from '#src/Workspace.tsx';
import { usePaneData, type PaneData } from '#src/PaneData.ts';
import { tabLabel, TAB_ICON, useChoices } from '#src/PaneTabs.tsx';

/** The pane's data (who I am, what the daemon serves, the shell writes) is read once here; the view below holds none. */
export function Pane({ group }: { group: Group }) {
  const { agent } = useWorkspace();
  return <PaneView group={group} data={usePaneData(agent)} />;
}

function PaneView({ group, data: { openShell, hello } }: { group: Group; data: PaneData }) {
  const { ws, detail, agent, storage, apply, merged, dragging } = useWorkspace();
  const handle = useRef<HTMLDivElement>(null);
  const tab = ws.tabs[group.activeTab];
  const structured = detail.structured === true;
  const { choices, choose } = useChoices(openShell);
  const split = !merged && groupsOf(ws.root).length > 1;
  const focused = ws.active === group.id;
  // Closing a pane keeps its tabs: they move to the next pane, Chat back to its first place.
  const closePane = () =>
    apply((w) => {
      const into = groupsOf(w.root).find((g) => g.id !== group.id);
      return into ? group.tabs.reduce((acc, id) => moveTab(acc, id, into.id, id === CHAT ? 0 : undefined), w) : w;
    });

  return (
    <section
      className={`pane relative flex min-h-0 min-w-0 flex-1 flex-col bg-canvas -outline-offset-1${ws.active === group.id ? ' focus outline-1 outline-[color-mix(in_srgb,var(--primary)_40%,transparent)]' : ''}`}
      data-group={group.id}
      onPointerDownCapture={() => ws.active !== group.id && apply((w) => ({ ...w, active: group.id }))}
    >
      {split ? (
        <div
          className={`pane-handle flex h-[22px] flex-none items-center gap-1.5 border-b border-line px-2 text-hint ${focused ? 'bg-raised text-ink' : 'bg-surface text-ink-2'}`}
          data-handle={group.id}
          ref={handle}
        >
          <Icon name={TAB_ICON[tab?.kind ?? 'tasks'] ?? 'tasks'} size={12} aria-hidden />
          <span className="min-w-0 flex-1 truncate">{tab ? tabLabel(tab, structured) : ''}</span>
          <button
            className="pane-close tint inline-flex border-0 bg-transparent p-0.5 text-ink-3"
            aria-label="Close this pane"
            title="Close this pane; its tabs move to the next pane"
            onClick={(e) => {
              e.stopPropagation();
              closePane();
            }}
          >
            <Icon name="close" size={12} label="Close this pane" aria-hidden />
          </button>
        </div>
      ) : null}
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
                  className="tile flex aspect-square flex-col items-center justify-center gap-2 rounded-card border-0 bg-transparent text-body text-ink tint disabled:opacity-50"
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
            <strong>{tabLabel(tab, structured)}</strong>
            <p>This daemon does not serve {tab.kind} tabs. Close this tab.</p>
          </div>
        )}
      </div>
      {dragging ? <DropOverlay group={group} handle={split ? handle : null} /> : null}
    </section>
  );
}
