// An agent's workspace: tab groups in a split tree (app-core's workspace model), saved in the browser per daemon and agent.
// Nothing sits above the panes: each pane has its own tab strip.
import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import {
  activate,
  arrange,
  defaultWorkspace,
  groupsOf,
  loadWorkspace,
  openTab,
  saveWorkspace,
  setSplit,
  splitPane,
  oneTerminal,
  setTerminal,
  toggleView,
  storageKey,
  type KV,
  type Node,
  type Path,
  type Workspace,
} from '@handline/app-core';
import type { AgentDetail } from '@handline/client';
import { useConnection, useStore } from '@handline/app-react';
import { classify, useAgentDetail, useAgents, useChat, useServes } from '@handline/app-data';
import { useDecided } from '#src/ChatCards.tsx';
import { recordHistoryValue, takeTerminalWish, useHistoryValue } from '#src/route.ts';
import { View } from '#src/View.tsx';
import { Pane } from '#src/Pane.tsx';
import { isSwitchChord, isTerminalSwitch } from '#src/terminal/core.ts';

const CELL = 'cell flex min-h-0 min-w-0 [&>*]:min-h-0 [&>*]:min-w-0 [&>*]:flex-1';

export interface WorkspaceCtx {
  ws: Workspace;
  detail: AgentDetail;
  agent: string;
  storage: KV | null;
  apply: (f: (w: Workspace) => Workspace) => void;
  /** Shows a tab and makes the move a history entry, so Back returns to the tab shown before. */
  show: (tab: string) => void;
  /** The chat / Claude Code switch. */
  flip: () => void;
  /** Runs a drag-and-drop result; a refusal (a fifth pane) is said in the toast. */
  drop: (next: Workspace | null) => void;
  /** Whether Split on this pane shows as a pane of its own at this window size (narrower, the groups render as one strip). */
  canSplit: (group: string) => boolean;
  /** The groups render as one strip (a narrow window): a drop can only reorder or merge, never split. */
  merged: boolean;
  /** The pane whose strip comes first (the folded sidebar's buttons sit there): the one shown when the groups render as one. */
  lead: string;
  dragging: string | null;
  setDragging: (tab: string | null) => void;
}
const Ctx = createContext<WorkspaceCtx | null>(null);
export const useWorkspace = (): WorkspaceCtx => {
  const v = useContext(Ctx);
  if (!v) throw new Error('no workspace');
  return v;
};

/** The workspace when the caller sits in one; a text on a page with no panes (the Tasks page) has none. */
export const useWorkspaceIfAny = (): WorkspaceCtx | null => useContext(Ctx);

/** What a history entry must tell apart: the layout without its split ratios, since a divider drag is not a move. */
const shapeOf = (w: Workspace): string => JSON.stringify(w, (k, v) => (k === 'at' ? undefined : v));
const parse = (s: string): Workspace | null => {
  try {
    const w = JSON.parse(s) as Workspace;
    return w.v === 1 && w.root && w.tabs ? w : null;
  } catch {
    return null;
  }
};

/** The conversation is read while the agent's detail is still on its way, so the chat tab has it when it opens. */
function ChatPrefetch({ agent }: { agent: string }) {
  useChat(agent, 'mine');
  useDecided(agent);
  return null;
}

export function AgentWorkspace({ agent, daemon, storage }: { agent: string; daemon: string; storage: KV | null }) {
  const q = useAgentDetail(agent);
  const list = useAgents();
  const { served } = useServes('agent.get');
  const conn = useConnection();
  const gone = (list.data && !list.data.agents.some((a) => a.name === agent)) || (q.isError && classify(q.error).kind === 'not_found');
  if (gone)
    return (
      <div className="empty big">
        <p className="muted">No agent named {agent}.</p>
      </div>
    );
  if (!q.data)
    return (
      <div className="workspace-loading min-w-0 flex-1 p-3">
        {conn === 'open' ? null : <p className="muted pad">Connecting…</p>}
        <ChatPrefetch agent={agent} />
        <View q={q} served={served} shape="terminal" what={`${agent}'s workspace`}>
          {null}
        </View>
      </div>
    );
  return <Loaded agent={agent} detail={q.data} daemon={daemon} storage={storage} />;
}

function Loaded({ agent, detail, daemon, storage }: { agent: string; detail: AgentDetail; daemon: string; storage: KV | null }) {
  const store = useStore();
  const key = storageKey(daemon, agent);
  const [ws, setWs] = useState(() =>
    loadWorkspace(storage, key, () => defaultWorkspace({ terminal: detail.terminal ?? undefined, structured: detail.structured === true })),
  );
  const [dragging, setDragging] = useState<string | null>(null);
  // The panes, their tabs and the shown tab ride in the history entry: Back and Forward put that screen back exactly, reopening a tab closed since.
  const [entry, push] = useHistoryValue(`ws:${key}`);
  const synced = useRef<string | null>(null);
  const mine = shapeOf(ws);
  useEffect(() => {
    const theirs = entry ? shapeOf(parse(entry) ?? ws) : null;
    if (theirs === null) {
      recordHistoryValue(`ws:${key}`, JSON.stringify(ws));
      synced.current = mine;
    } else if (synced.current === null || theirs !== synced.current) {
      synced.current = theirs;
      if (theirs !== mine) setWs((w) => parse(entry!) ?? w);
    }
    // Runs when the entry changes (Back or Forward); `mine` and `ws` are read as they are at that moment.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entry]);
  useEffect(() => {
    if (synced.current === null || synced.current === mine) return;
    synced.current = mine;
    push(JSON.stringify(ws));
    // Runs when the layout changes (`mine` stands for `ws`); `push` is new every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mine]);
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

  // The session's Claude Code tab: added when a session starts while this is open (one already running was handled on load), and moved to the new handle when it restarts, so there is only ever one.
  const terminal = detail.terminal ?? null;
  const had = useRef(!!terminal);
  useEffect(() => {
    setWs((w) => (terminal ? setTerminal(w, terminal, !had.current) : oneTerminal(w)));
    had.current = !!terminal;
  }, [terminal]);

  // Cmd/Ctrl+J flips between the chat and Claude Code, from either (a focused terminal hands the chord over).
  const flip = () => {
    const next = toggleView(ws, detail.terminal ?? undefined, detail.structured === true);
    setWs(next);
  };
  const flipRef = useRef(flip);
  flipRef.current = flip;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const inTerminal = e.target instanceof HTMLElement && !!e.target.closest('.xterm');
      if (!(inTerminal ? isTerminalSwitch(e) : isSwitchChord(e))) return;
      e.preventDefault();
      flipRef.current();
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, []);

  // "Open terminal" from the My turn page: the terminal (or transcript) comes to the front once.
  useEffect(() => {
    const take = () => {
      if (!takeTerminalWish(agent)) return;
      setWs((w) => (detail.structured ? openTab(w, 'transcript') : detail.terminal ? openTab(w, 'terminal', { handle: detail.terminal }) : w));
    };
    take();
    addEventListener('handline-terminal', take);
    return () => removeEventListener('handline-terminal', take);
  }, [agent, detail.structured, detail.terminal]);

  const value: WorkspaceCtx = {
    flip,
    ws,
    detail,
    agent,
    storage,
    merged: arrange(ws, size.w, size.h).merged,
    lead: arrange(ws, size.w, size.h).merged ? ws.active : groupsOf(ws.root)[0]!.id,
    canSplit: (id) => {
      const next = splitPane(ws, id);
      return !!next && !arrange(next, size.w, size.h).merged;
    },
    dragging,
    setDragging,
    apply: (f) => setWs(f),
    show: (tab) => {
      setWs((w) => activate(w, tab));
    },
    drop: (next) => {
      setDragging(null);
      if (next) setWs(next);
      else store.fail('That tab is already alone in its pane.');
    },
  };
  const arr = arrange(ws, size.w, size.h);
  const active = groupsOf(ws.root).find((g) => g.id === ws.active) ?? groupsOf(ws.root)[0]!;
  return (
    <Ctx.Provider value={value}>
      <div className="workspace relative flex min-h-0 min-w-0 flex-1" ref={root} data-panes={groupsOf(ws.root).length} data-merged={arr.merged}>
        {arr.merged ? (
          <Pane group={{ type: 'group', id: active.id, tabs: arr.tabs, activeTab: arr.activeTab }} />
        ) : (
          <Tree node={ws.root} path={[]} />
        )}
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
    <div className={`split split-${node.dir} flex min-h-0 min-w-0 flex-1${row ? '' : ' flex-col'}`} ref={box}>
      <div className={CELL} style={{ flex: `${node.at} 1 0` }}>
        <Tree node={node.a} path={[...path, 'a']} />
      </div>
      <div
        className={`divider flex-none touch-none bg-line hover:bg-primary ${row ? 'w-[5px] cursor-col-resize' : 'h-[5px] cursor-row-resize'}`}
        role="separator"
        aria-orientation={row ? 'vertical' : 'horizontal'}
        title="Drag to resize; double-click to reset"
        onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)}
        onPointerMove={drag}
        onDoubleClick={() => apply((w) => setSplit(w, path, 0.5))}
      />
      <div className={CELL} style={{ flex: `${1 - node.at} 1 0` }}>
        <Tree node={node.b} path={[...path, 'b']} />
      </div>
    </div>
  );
}
