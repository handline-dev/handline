// The frame: the sidebar with every agent control, and the selected agent's workspace at full height.
import { useEffect, useRef, useState } from 'react';
import type { KV, Store } from '@handline/app-core';
import { DaemonProvider } from '@handline/app-react';
import { DataProvider, SKELETON_CSS, useUnread, type BoxComponent } from '@handline/app-data';
import { setting } from '#src/daemon.ts';
import { AccountsPage } from '#src/Accounts.tsx';
import { RequestsPage } from '#src/RequestsPage.tsx';
import { UsagePage } from '#src/UsagePage.tsx';
import { MyTurnPage } from '#src/MyTurn.tsx';
import { SettingsPage } from '#src/Settings.tsx';
import { AdminPage } from '#src/Admin.tsx';
import { DebugTerminals } from '#src/debug/DebugTerminals.tsx';
import {
  useAccountsRoute,
  useDebugRoute,
  useNewAgentRoute,
  usePageRoute,
  useSelectedAgent,
  useSettingsOpen,
  useTasksRoute,
  useTeamRoute,
  leave,
} from '#src/route.ts';
import { useShell } from '#src/shell.ts';
import { SettingsView } from '#src/SettingsView.tsx';
import { TeamPage } from '#src/TeamPage.tsx';
import { NewAgent } from '#src/NewAgent.tsx';
import { KeysSheet } from '#src/KeysSheet.tsx';
import { Palette } from '#src/Palette.tsx';
import { Sidebar } from '#src/Sidebar.tsx';
import { FoldedControls, RailControls } from '#src/RailControls.tsx';
import { DaemonDown } from '#src/DaemonDown.tsx';
import { terminalOwnsKey } from '#src/terminal/core.ts';
import { StatusBar } from '#src/StatusBar.tsx';
import { CaptureHost } from '#src/Capture.tsx';
import { TasksPage } from '#src/Tasks.tsx';
import { AgentWorkspace } from '#src/Workspace.tsx';
import { AnimatedList } from '#src/AnimatedList.tsx';
import { typing } from '#src/typing.ts';

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
  const newAgent = useNewAgentRoute();
  const page = usePageRoute();
  useShell(agent);
  const unread = useUnread().length;
  const [railOpen, setRail] = useState(() => setting(storage, 'rail', '1').value === '1');
  const toggle = () => {
    setRail(!railOpen);
    setting(storage, 'rail', '1').save(railOpen ? '0' : '1');
  };
  const toggleRef = useRef(toggle);
  toggleRef.current = toggle;
  const [palette, setPalette] = useState(false);
  const [keys, setKeys] = useState(false);
  const own = tasks !== null; // these pages answer `?` with their own keys
  useEffect(() => {
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
    // The mouse's back and forward buttons walk the history like the arrows do.
    const onMouse = (e: MouseEvent) => {
      if (e.button !== 3 && e.button !== 4) return;
      e.preventDefault();
      if (e.button === 3) history.back();
      else history.forward();
    };
    addEventListener('keydown', onKey);
    addEventListener('mouseup', onMouse);
    return () => {
      removeEventListener('keydown', onKey);
      removeEventListener('mouseup', onMouse);
    };
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
  const hasStrip =
    !team && !accounts.open && tasks === null && page !== 'stats' && page !== 'settings' && page !== 'admin' && !!agent && !settings;
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
          ) : hasStrip ? null : (
            <div className="rail-handle flex w-9 flex-none justify-start self-stretch border-r border-line bg-panel px-1 shell:pt-[calc(var(--h-tabs)+4px)] pt-2">
              <RailControls show={toggle} />
            </div>
          )}
          <main className={`main flex min-h-0 min-w-0 flex-1 bg-canvas${railOpen ? ' max-[900px]:ml-0' : ''}`} aria-label="Agent workspace">
            <DaemonDown />
            {team ? (
              <TeamPage />
            ) : page === 'stats' ? (
              <UsagePage />
            ) : accounts.open ? (
              <AccountsPage name={accounts.name} />
            ) : tasks !== null ? (
              <TasksPage route={tasks} />
            ) : page === 'requests' ? (
              <RequestsPage />
            ) : page === 'my-turn' ? (
              <MyTurnPage />
            ) : page === 'settings' ? (
              <SettingsPage storage={storage} />
            ) : page === 'admin' ? (
              <AdminPage />
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
          <AnimatedList>{newAgent.open ? <NewAgent key="NewAgent" onClose={() => leave('')} /> : null}</AnimatedList>
        </div>
        <StatusBar />
        <AnimatedList>{palette ? <Palette key="Palette" onClose={() => setPalette(false)} /> : null}</AnimatedList>
        <AnimatedList>{keys ? <KeysSheet key="KeysSheet" onClose={() => setKeys(false)} /> : null}</AnimatedList>
      </div>
    </FoldedControls.Provider>
  );
}
