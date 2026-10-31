// An agent's workspace: tab groups in a split tree (app-core's workspace model), saved in the browser per daemon and agent.
// The top bar carries the agent's name and the tabs of the focused pane; a pane has a thin handle only when the window is split.
import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import {
  activate,
  arrange,
  closeTab,
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
import { onCommand, type CommandName } from '#src/commands.ts';
import { View } from '#src/View.tsx';
import { Pane } from '#src/Pane.tsx';
import { PaneActions, PaneTabs } from '#src/PaneTabs.tsx';
import { PageBar } from '#src/ui/index.ts';
import { isSwitchChord, isTerminalSwitch, terminalOwnsKey } from '#src/terminal/core.ts';

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
  /** Two agents sit side by side: a tab says whose it is ("harbor · Chat"). */
  named: boolean;
  closeSplit?: () => void;
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

export function AgentWorkspace({
  agent,
  daemon,
  storage,
  active = true,
  named = false,
  onCloseSplit,
}: {
  agent: string;
  daemon: string;
  storage: KV | null;
  /** Two agents are side by side: a tab says whose it is. */
  named?: boolean;
  /** Whether this is the half the keys and commands go to (always, when the page holds one agent). */
  active?: boolean;
  /** Set on the second half of a two-agent split: its Split button closes the split. */
  onCloseSplit?: () => void;
}) {
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
  return (
    <Loaded agent={agent} detail={q.data} daemon={daemon} storage={storage} active={active} named={named} onCloseSplit={onCloseSplit} />
  );
}

function Loaded({
  agent,
  detail,
  daemon,
  storage,
  active: focused,
  named,
  onCloseSplit,
}: {
  agent: string;
  detail: AgentDetail;
  daemon: string;
  storage: KV | null;
  active: boolean;
  named: boolean;
  onCloseSplit?: () => void;
}) {
  // Two halves are mounted at once, but the keys and commands belong to the focused one.
  const focusedRef = useRef(focused);
  focusedRef.current = focused;
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
      if (!focusedRef.current) return;
      const inTerminal = e.target instanceof HTMLElement && !!e.target.closest('.xterm');
      if (!(inTerminal ? isTerminalSwitch(e) : isSwitchChord(e))) return;
      e.preventDefault();
      flipRef.current();
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, []);

  // Window keys, and the palette's commands that go with them: close the shown tab, a new tab, split the pane, focus pane 1 to 9. One that changes nothing is left to the browser.
  const splitIfRoom = (id: string) => {
    const split = splitPane(ws, id);
    return split && !arrange(split, size.w, size.h).merged ? split : null;
  };
  const windowCommand = (name: CommandName | `pane-${string}`): boolean => {
    const panes = groupsOf(ws.root);
    const here = panes.find((g) => g.id === ws.active) ?? panes[0]!;
    const next =
      name === 'tab-close'
        ? closeTab(ws, here.activeTab)
        : name === 'tab-new'
          ? openTab(ws, 'new')
          : name === 'split'
            ? splitIfRoom(here.id)
            : name.startsWith('pane-') && panes[Number(name.slice(5)) - 1]
              ? { ...ws, active: panes[Number(name.slice(5)) - 1]!.id }
              : null;
    if (!next || next === ws) return false;
    setWs(next);
    return true;
  };
  const commandRef = useRef(windowCommand);
  commandRef.current = windowCommand;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!focusedRef.current || terminalOwnsKey(e) || !(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
      const k = e.key.toLowerCase();
      const name =
        k === 'w' ? 'tab-close' : k === 't' ? 'tab-new' : k === '\\' ? 'split' : /^[1-9]$/.test(k) ? (`pane-${k}` as const) : null;
      if (name && commandRef.current(name)) e.preventDefault();
    };
    addEventListener('keydown', onKey);
    const off = onCommand((name) => void (focusedRef.current && commandRef.current(name)));
    return () => (removeEventListener('keydown', onKey), off());
  }, []);

  // "Open terminal" from the My turn page: the terminal (or transcript) comes to the front once.
  useEffect(() => {
    const take = () => {
      if (!takeTerminalWish(agent)) return;
      setWs((w) =>
        detail.structured ? openTab(w, 'transcript') : detail.terminal ? openTab(w, 'terminal', { handle: detail.terminal }) : w,
      );
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
    canSplit: (id) => {
      const next = splitPane(ws, id);
      return !!next && !arrange(next, size.w, size.h).merged;
    },
    named,
    closeSplit: onCloseSplit,
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
  // The bar shows the focused pane's tabs; in a narrow window the panes are one, and the bar shows its tabs.
  const barGroup = arr.merged ? { type: 'group' as const, id: active.id, tabs: arr.tabs, activeTab: arr.activeTab } : active;
  return (
    <Ctx.Provider value={value}>
      <PageBar
        tabs={<PaneTabs group={barGroup} />}
        tools={
          <>
            <PaneActions group={barGroup} />
          </>
        }
      />
      <div
        className="workspace relative flex min-h-0 min-w-0 flex-1"
        ref={root}
        data-panes={groupsOf(ws.root).length}
        data-merged={arr.merged}
      >
        {arr.merged ? <Pane group={barGroup} /> : <Tree node={ws.root} path={[]} />}
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
        className={`divider flex-none touch-none bg-line hover:bg-acc ${row ? 'w-[5px] cursor-col-resize' : 'h-[5px] cursor-row-resize'}`}
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
