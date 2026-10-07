// The frame: the sidebar with every agent control, and the selected agent's workspace at full height.
import { useEffect, useState } from 'react';
import type { KV, Store } from '@handline/app-core';
import { DaemonProvider, useError, useStore } from '@handline/app-react';
import { DataProvider, SKELETON_CSS, useUnread, type BoxComponent } from '@handline/app-data';
import { setting } from './daemon.ts';
import { AccountsPage } from './Accounts.tsx';
import { InboxPage } from './Inbox.tsx';
import { useAccountsRoute, useInboxRoute, useSelectedAgent, useSettingsOpen, useTasksRoute, useTeamRoute } from './route.ts';
import { useShell } from './shell.ts';
import { SettingsView } from './SettingsView.tsx';
import { TeamPage } from './TeamPage.tsx';
import { Sidebar } from './Sidebar.tsx';
import { TasksPage } from './Tasks.tsx';
import { AgentWorkspace } from './Workspace.tsx';

const Box: BoxComponent = (p) => <div {...p} />;

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
  useEffect(() => {
    document.title = unread ? `(${unread}) Handline` : 'Handline';
  }, [unread]);
  // Everything but the workspace lives in the sidebar, so the main view gets the whole window height.
  return (
    <div id="app" data-rail={railOpen ? 'open' : 'closed'}>
      {railOpen ? (
        <Sidebar selected={agent} onTasks={tasks !== null} storage={storage} onToggle={toggle} />
      ) : (
        <button className="rail-handle" aria-label="Show the agent list" title="Show the agent list" onClick={toggle}>
          ☰
        </button>
      )}
      <main className="main" aria-label="Agent workspace">
        {team ? (
          <TeamPage />
        ) : inbox.open ? (
          <InboxPage item={inbox.item} />
        ) : accounts.open ? (
          <AccountsPage name={accounts.name} />
        ) : tasks !== null ? (
          <TasksPage storage={storage} route={tasks} />
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
      {error ? (
        <div className="toast" role="status" onClick={() => store.dismissError()} title="Click to dismiss">
          {error}
        </div>
      ) : null}
    </div>
  );
}
