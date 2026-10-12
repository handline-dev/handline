// The frame: the sidebar with every agent control, and the selected agent's workspace at full height.
import { useEffect, useRef, useState } from 'react';
import type { KV, Store } from '@handline/app-core';
import { DaemonProvider, useError, useStore } from '@handline/app-react';
import { DataProvider, SKELETON_CSS, useUnread, type BoxComponent } from '@handline/app-data';
import { setting } from './daemon.ts';
import { Icon } from './ui/index.ts';
import { AccountsPage } from './Accounts.tsx';
import { InboxPage } from './Inbox.tsx';
import { SettingsPage } from './Settings.tsx';
import { StatsPage } from './Stats.tsx';
import {
  useAccountsRoute,
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
import { DaemonDown } from './DaemonDown.tsx';
import { HrBanner } from './HrBanner.tsx';
import { terminalOwnsKey } from './terminal/core.ts';
import { StatusBar } from './StatusBar.tsx';
import { TasksPage } from './Tasks.tsx';
import { AgentWorkspace } from './Workspace.tsx';

const Box: BoxComponent = (p) => <div {...p} />;

const RAIL_W = 280;
const clampRail = (w: number) => Math.min(560, Math.max(200, Math.round(w)));

export function App(props: { store: Store; daemon: string; storage: KV | null; connect?: boolean }) {
  return (
    <DaemonProvider store={props.store} where={props.daemon} connect={props.connect}>
      <DataProvider daemonId={props.daemon} box={Box}>
        <style>{SKELETON_CSS}</style>
        <Frame daemon={props.daemon} storage={props.storage} />
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
  const own = inbox.open || tasks !== null; // these pages answer `?` with their own keys
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
  return (
    <div className="frame flex h-screen flex-col">
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
          <button
            className="rail-handle flex w-7 flex-none justify-center self-stretch border-0 border-r border-line bg-panel pt-2 align-top"
            aria-label="Show the agent list"
            title="Show the agent list"
            onClick={toggle}
          >
            <Icon name="sidebar" size={16} aria-hidden />
          </button>
        )}
        <main className={`main flex min-h-0 min-w-0 flex-1 bg-canvas${railOpen ? ' max-[900px]:ml-0' : ''}`} aria-label="Agent workspace">
          <DaemonDown />
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
          ) : page === 'stats' ? (
            <StatsPage />
          ) : page === 'settings' ? (
            <SettingsPage />
          ) : agent && settings ? (
            <SettingsView key={agent} agent={agent} />
          ) : agent ? (
            <AgentWorkspace key={agent} agent={agent} daemon={daemon} storage={storage} />
          ) : (
            <div className="empty big">
              <strong>Pick an agent</strong>
              <p>Choose one in the list to open its workspace.</p>
            </div>
          )}
        </main>
        <HrBanner />
        {newAgent.open ? <NewAgent onClose={() => void (location.hash = '')} /> : null}
        {error ? (
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
      <StatusBar />
      {palette ? <Palette onClose={() => setPalette(false)} /> : null}
      {keys ? <KeysSheet onClose={() => setKeys(false)} /> : null}
    </div>
  );
}
