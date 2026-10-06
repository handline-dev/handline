// An agent's workspace: tab groups in a split tree (app-core's workspace model), saved in the browser per daemon and agent.
// Nothing sits above the panes: each pane has its own tab strip.
import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import {
  arrange,
  defaultWorkspace,
  groupOf,
  groupsOf,
  loadWorkspace,
  MAX_GROUPS,
  openTab,
  saveWorkspace,
  setSplit,
  splitTab,
  storageKey,
  type KV,
  type Node,
  type Path,
  type Workspace,
} from '@handline/app-core';
import type { AgentDetail } from '@handline/client';
import { useAgentDetail, useConnection, useSelector, useStore } from '@handline/app-react';
import { DropZones, Pane } from './Pane.tsx';

export interface WorkspaceCtx {
  ws: Workspace;
  detail: AgentDetail;
  agent: string;
  apply: (f: (w: Workspace) => Workspace) => void;
  /** Runs a drag-and-drop result; a refusal (a fifth pane) is said in the toast. */
  drop: (next: Workspace | null) => void;
  dragging: string | null;
  setDragging: (tab: string | null) => void;
}
const Ctx = createContext<WorkspaceCtx | null>(null);
export const useWorkspace = (): WorkspaceCtx => {
  const v = useContext(Ctx);
  if (!v) throw new Error('no workspace');
  return v;
};

export function AgentWorkspace({ agent, daemon, storage }: { agent: string; daemon: string; storage: KV | null }) {
  const detail = useAgentDetail(agent);
  const known = useSelector((s) => s.agents.has(agent));
  const conn = useConnection();
  if (!known)
    return (
      <div className="empty big">
        <p className="muted">{conn === 'open' ? `No agent named ${agent}.` : 'Connecting…'}</p>
      </div>
    );
  if (!detail)
    return (
      <div className="empty big">
        <p className="muted">Loading…</p>
      </div>
    );
  return <Loaded agent={agent} detail={detail} daemon={daemon} storage={storage} />;
}

function Loaded({ agent, detail, daemon, storage }: { agent: string; detail: AgentDetail; daemon: string; storage: KV | null }) {
  const store = useStore();
  const key = storageKey(daemon, agent);
  const [ws, setWs] = useState(() => loadWorkspace(storage, key, () => defaultWorkspace({ terminal: detail.terminal ?? undefined })));
  const [dragging, setDragging] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: innerWidth, h: innerHeight });

  useEffect(() => saveWorkspace(storage, key, ws), [storage, key, ws]);
  useLayoutEffect(() => {
    const el = root.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => {
      if (el.clientWidth) setSize({ w: el.clientWidth, h: el.clientHeight });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // A session that starts while this workspace is open gets its terminal beside the others; one already running was handled on load.
  const terminal = detail.terminal ?? null;
  const had = useRef(!!terminal);
  useEffect(() => {
    if (terminal && !had.current) {
      setWs((w) => {
        const opened = openTab(w, 'terminal', { handle: terminal });
        const g = groupOf(opened, `terminal:${terminal}`)!;
        return g.tabs.length > 1 && groupsOf(opened.root).length < MAX_GROUPS
          ? (splitTab(opened, `terminal:${terminal}`, g.id, 'right') ?? opened)
          : opened;
      });
    }
    had.current = !!terminal;
  }, [terminal]);

  const value: WorkspaceCtx = {
    ws,
    detail,
    agent,
    dragging,
    setDragging,
    apply: (f) => setWs(f),
    drop: (next) => {
      setDragging(null);
      if (next) setWs(next);
      else store.fail(`At most ${MAX_GROUPS} panes. Drop a tab on another pane's tab strip to merge it.`);
    },
  };
  const arr = arrange(ws, size.w, size.h);
  const active = groupsOf(ws.root).find((g) => g.id === ws.active) ?? groupsOf(ws.root)[0]!;
  return (
    <Ctx.Provider value={value}>
      <div className="workspace" ref={root} data-panes={groupsOf(ws.root).length} data-merged={arr.merged}>
        {arr.merged ? (
          <Pane group={{ type: 'group', id: active.id, tabs: arr.tabs, activeTab: arr.activeTab }} />
        ) : (
          <Tree node={ws.root} path={[]} />
        )}
        {dragging ? <DropZones root /> : null}
      </div>
    </Ctx.Provider>
  );
}

function Tree({ node, path }: { node: Node; path: Path }): ReactNode {
  const { apply } = useWorkspace();
  const box = useRef<HTMLDivElement>(null);
  if (node.type === 'group') return <Pane group={node} />;
  const row = node.dir === 'row';
  const drag = (e: React.PointerEvent) => {
    if (!(e.buttons & 1)) return;
    const r = box.current!.getBoundingClientRect();
    apply((w) => setSplit(w, path, row ? (e.clientX - r.left) / r.width : (e.clientY - r.top) / r.height));
  };
  return (
    <div className={`split split-${node.dir}`} ref={box}>
      <div className="cell" style={{ flex: `${node.at} 1 0` }}>
        <Tree node={node.a} path={[...path, 'a']} />
      </div>
      <div
        className="divider"
        role="separator"
        aria-orientation={row ? 'vertical' : 'horizontal'}
        title="Drag to resize; double-click to reset"
        onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)}
        onPointerMove={drag}
        onDoubleClick={() => apply((w) => setSplit(w, path, 0.5))}
      />
      <div className="cell" style={{ flex: `${1 - node.at} 1 0` }}>
        <Tree node={node.b} path={[...path, 'b']} />
      </div>
    </div>
  );
}
