// One pane: its own tab strip, the active tab, and while a tab is dragged, the edges it can be dropped on.
import { useRef, useState } from 'react';
import {
  activate, applyPreset, closeTab, CHAT, moveTab, moveTabBy, openTab, splitRoot, splitTab,
  type Edge, type Group, type Preset, type Tab,
} from '@handline/app-core';
import { ChatTab } from './ChatTab.tsx';
import { TerminalTab } from './TerminalTab.tsx';
import { useWorkspace } from './Workspace.tsx';

const label = (t: Tab): string => (t.kind === 'chat' ? 'Chat' : t.kind === 'terminal' ? 'Claude Code' : `${t.kind}: ${Object.values(t.params)[0] ?? ''}`);
const PRESETS: [Preset, string][] = [['single', 'One pane'], ['cols', 'Two columns'], ['rows', 'Two rows'], ['grid', '2 × 2']];
const EDGES: Edge[] = ['left', 'right', 'top', 'bottom'];

export function Pane({ group }: { group: Group }) {
  const { ws, detail, agent, apply, drop, dragging, setDragging } = useWorkspace();
  const strip = useRef<HTMLDivElement>(null);
  const tab = ws.tabs[group.activeTab];

  const dropOnStrip = (e: React.DragEvent) => {
    if (!dragging) return;
    e.preventDefault();
    const mids = [...strip.current!.querySelectorAll<HTMLElement>('[role=tab]')].map((el) => { const r = el.getBoundingClientRect(); return r.left + r.width / 2; });
    const at = mids.findIndex((m) => e.clientX < m);
    apply((w) => moveTab(w, dragging, group.id, at < 0 ? undefined : at));
    setDragging(null);
  };
  return (
    <section className={`pane${ws.active === group.id ? ' focus' : ''}`} data-group={group.id} onPointerDownCapture={() => ws.active !== group.id && apply((w) => ({ ...w, active: group.id }))}>
      <div className="strip" role="tablist" ref={strip} onDragOver={(e) => dragging && e.preventDefault()} onDrop={dropOnStrip}>
        {group.tabs.map((id) => {
          const t = ws.tabs[id]!;
          return (
            <div key={id} role="tab" tabIndex={0} aria-selected={id === group.activeTab} className={`tab${id === group.activeTab ? ' on' : ''}`} data-tab={id}
              draggable onDragStart={(e) => { e.dataTransfer?.setData('text/plain', id); setDragging(id); }} onDragEnd={() => setDragging(null)}
              onClick={() => apply((w) => activate(w, id))}
              onKeyDown={(e) => {
                if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) { e.preventDefault(); apply((w) => moveTabBy(w, id, e.key === 'ArrowLeft' ? -1 : 1)); }
                else if (e.key === 'Enter' || e.key === ' ') apply((w) => activate(w, id));
              }}>
              <span className="tab-label">{label(t)}</span>
              {id === CHAT ? null : (
                <button className="x" aria-label={`Close ${label(t)}`} title={t.kind === 'terminal' ? 'Close the tab; the agent keeps running' : 'Close the tab'}
                  onClick={(e) => { e.stopPropagation(); apply((w) => closeTab(w, id)); }}>✕</button>
              )}
            </div>
          );
        })}
        <span className="strip-end">
          <details className="menu">
            <summary className="icon" aria-label="Add a tab" title="Add a tab">＋</summary>
            <div className="menu-list">
              {detail.terminal
                ? <button className="menu-item" onClick={(e) => { e.currentTarget.closest('details')!.removeAttribute('open'); apply((w) => openTab(w, 'terminal', { handle: detail.terminal! }, group.id)); }}>Claude Code</button>
                : <span className="menu-item muted">No terminal: start {agent} first</span>}
            </div>
          </details>
          <details className="menu">
            <summary className="icon" aria-label="Layout" title="Layout">▦</summary>
            <div className="menu-list">
              {PRESETS.map(([p, name]) => <button key={p} className="menu-item" onClick={(e) => { e.currentTarget.closest('details')!.removeAttribute('open'); apply((w) => applyPreset(w, p)); }}>{name}</button>)}
            </div>
          </details>
        </span>
      </div>
      <div className="pane-body" role="tabpanel">
        {!tab ? null : tab.kind === 'chat' ? <ChatTab agent={agent} detail={detail} />
          : tab.kind === 'terminal' ? <TerminalTab agent={agent} detail={detail} />
          : <div className="empty big"><strong>{label(tab)}</strong><p>This daemon does not serve {tab.kind} tabs. Close this tab.</p></div>}
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
        <div key={edge} className={`dz${root ? ' root' : ''} ${edge}${over === edge ? ' over' : ''}`} data-edge={edge} data-root={root ? 'true' : undefined}
          onDragOver={(e) => { e.preventDefault(); setOver(edge); }} onDragLeave={() => setOver(null)}
          onDrop={(e) => { e.preventDefault(); e.stopPropagation(); setOver(null); if (dragging) drop(root ? splitRoot(ws, dragging, edge) : splitTab(ws, dragging, group!, edge)); }} />
      ))}
    </>
  );
}
