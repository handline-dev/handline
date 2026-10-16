// The frame: the sidebar with every agent control, and the selected agent's workspace at full height.
import { useEffect, useRef, useState } from 'react';
import type { KV, Store } from '@handline/app-core';
import { DaemonProvider, useError, useStore } from '@handline/app-react';
import { DataProvider, SKELETON_CSS, useUnread, type BoxComponent } from '@handline/app-data';
import { setting } from './daemon.ts';
import { AccountsPage } from './Accounts.tsx';
import { InboxPage } from './Inbox.tsx';
import { MyTurnPage } from './MyTurn.tsx';
import { SettingsPage } from './Settings.tsx';
import { AdminPage } from './Admin.tsx';
import { DebugTerminals } from './debug/DebugTerminals.tsx';
import { StatsPage } from './Stats.tsx';
import {
  useAccountsRoute,
  useDebugRoute,
  useInboxRoute,
  useNewAgentRoute,
  usePageRoute,
  useSelectedAgent,
  useSettingsOpen,
  useTasksRoute,
  useTeamRoute,
  useWorktreesRoute,
} from './route.ts';
import { useShell } from './shell.ts';
import { SettingsView } from './SettingsView.tsx';
import { TeamPage } from './TeamPage.tsx';
import { Worktrees } from './Worktrees.tsx';
import { NewAgent } from './NewAgent.tsx';
import { KeysSheet } from './KeysSheet.tsx';
import { Palette } from './Palette.tsx';
import { Sidebar } from './Sidebar.tsx';
import { FoldedControls, RailControls } from './RailControls.tsx';
import { DaemonDown, useOffline } from './DaemonDown.tsx';
import { HrBanner } from './HrBanner.tsx';
import { terminalOwnsKey } from './terminal/core.ts';
import { StatusBar } from './StatusBar.tsx';
import { CaptureHost } from './Capture.tsx';
import { TasksPage } from './Tasks.tsx';
import { AgentWorkspace } from './Workspace.tsx';

const Box: BoxComponent = (p) => <div {...p} />;

const RAIL_W = 280;
const clampRail = (w: number) => Math.min(560, Math.max(200, Math.round(w)));

export function App(props: { store: Store; daemon: string; storage: KV | null; connect?: boolean }) {
  const debug = useDebugRoute(); // the debug window: its own page, no sidebar
  return (
    <DaemonProvider store={props.store} where={props.daemon} connect={props.connect}>
      <DataProvider daemonId={props.daemon} box={Box}>
        <style>{SKELETON_CSS}</style>
        {debug ? <DebugTerminals /> : <Frame daemon={props.daemon} storage={props.storage} />}
      </DataProvider>
    </DaemonProvider>
  );
}

function Frame({ daemon, storage }: { daemon: string; storage: KV | null }) {
  const agent = useSelectedAgent();
  const tasks = useTasksRoute();
  const settings = useSettingsOpen();
  const accounts = useAccountsRoute();
  const team = useTeamRoute();
  const worktrees = useWorktreesRoute();
  const newAgent = useNewAgentRoute();
  const page = usePageRoute();
  useShell(agent);
  const inbox = useInboxRoute();
  const unread = useUnread().length;
  const error = useError();
  const offline = useOffline();
  const store = useStore();
  const [railOpen, setRail] = useState(() => setting(storage, 'rail', '1').value === '1');
  const toggle = () => {
    setRail(!railOpen);
    setting(storage, 'rail', '1').save(railOpen ? '0' : '1');
  };
  const toggleRef = useRef(toggle);
  toggleRef.current = toggle;
  const [palette, setPalette] = useState(false);
  const [keys, setKeys] = useState(false);
  const own = tasks !== null; // the Tasks page answers `?` with its own keys
  useEffect(() => {
    const typing = (t: EventTarget | null) => t instanceof HTMLElement && !!t.closest('input, textarea, select, [contenteditable]');
    const onKey = (e: KeyboardEvent) => {
      if (terminalOwnsKey(e)) return; // a focused terminal gets every key
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPalette((p) => !p);
      } else if (mod && e.key.toLowerCase() === 'b') {
        e.preventDefault();
        toggleRef.current();
      } else if (mod && e.key === '[') history.back();
      else if (mod && e.key === ']') history.forward();
      else if (e.key === '?' && !mod && !own && !typing(e.target)) setKeys((k) => !k);
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, [own]);
  const [railW, setRailW] = useState(() => clampRail(Number(setting(storage, 'railWidth', '').value) || RAIL_W));
  const sizeRail = (w: number) => {
    const next = clampRail(w);
    setRailW(next);
    setting(storage, 'railWidth', '').save(String(next));
  };
  useEffect(() => {
    document.title = unread ? `(${unread}) Handline` : 'Handline';
  }, [unread]);
  // Everything but the workspace lives in the sidebar, so the main view gets the whole window height.
  // The pages with no tab strip keep the rail's handle; on the agent workspace the controls ride the first strip.
  const hasStrip = !team && !worktrees.open && !inbox.open && !accounts.open && tasks === null && page !== 'stats' && page !== 'settings' && page !== 'admin' && !!agent && !settings;
  return (
    <FoldedControls.Provider value={railOpen ? null : { show: toggle }}>
    <div className="frame flex h-screen flex-col">
      <CaptureHost />
      <div id="app" data-rail={railOpen ? 'open' : 'closed'} style={{ '--rail-w': `${railW}px` } as React.CSSProperties}>
        {railOpen ? (
          <>
            <Sidebar selected={agent} onTasks={tasks !== null} storage={storage} onToggle={toggle} />
            <div
              className="rail-divider z-[1] -ml-0.75 w-[5px] flex-none cursor-col-resize touch-none hover:bg-primary max-[760px]:hidden"
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize the sidebar"
              title="Drag to resize; double-click to reset"
              onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)}
              onPointerMove={(e) => e.buttons & 1 && sizeRail(e.clientX)}
              onDoubleClick={() => sizeRail(RAIL_W)}
            />
          </>
        ) : (
          hasStrip ? null : (
            <div className="rail-handle flex w-9 flex-none justify-start self-stretch border-r border-line bg-panel px-1 shell:pt-[calc(var(--h-tabs)+4px)] pt-2">
              <RailControls show={toggle} />
            </div>
          )
        )}
        <main className={`main flex min-h-0 min-w-0 flex-1 bg-canvas${railOpen ? ' max-[900px]:ml-0' : ''}`} aria-label="Agent workspace">
          {team ? (
            <TeamPage />
          ) : worktrees.open ? (
            <Worktrees path={worktrees.path} storage={storage} />
          ) : inbox.open ? (
            <InboxPage item={inbox.item} />
          ) : accounts.open ? (
            <AccountsPage name={accounts.name} />
          ) : tasks !== null ? (
            <TasksPage storage={storage} route={tasks} />
          ) : page === 'my-turn' ? (
            <MyTurnPage />
          ) : page === 'stats' ? (
            <StatsPage />
          ) : page === 'settings' ? (
            <SettingsPage />
          ) : page === 'admin' ? (
            <AdminPage />
          ) : agent && settings ? (
            <SettingsView key={agent} agent={agent} />
          ) : agent ? (
            <AgentWorkspace key={agent} agent={agent} daemon={daemon} storage={storage} />
          ) : (
            offline ? null : (
              <div className="empty big">
                <strong>Pick an agent</strong>
                <p>Choose one in the list to open its workspace.</p>
              </div>
            )
          )}
        </main>
        <HrBanner />
        {newAgent.open ? <NewAgent onClose={() => void (location.hash = '')} /> : null}
        {error && !offline ? (
          <div
            className="toast fixed right-4 bottom-10 max-w-[420px] rounded-[8px] bg-ink px-3.5 py-2.5 text-canvas shadow-[var(--shadow-pop)]"
            role="status"
            onClick={() => store.dismissError()}
            title="Click to dismiss"
          >
            {error}
          </div>
        ) : null}
      </div>
      <DaemonDown />
      <StatusBar />
      {palette ? <Palette onClose={() => setPalette(false)} /> : null}
      {keys ? <KeysSheet onClose={() => setKeys(false)} /> : null}
    </div>
    </FoldedControls.Provider>
  );
}
