// The frame: top bar, sidebar with every agent control, the selected agent's workspace, a context panel and the status bar.
import { useState } from 'react';
import { dotOf, type KV, type Store } from '@handline/app-core';
import { DaemonProvider, useAgents, useError, useStore } from '@handline/app-react';
import { setting } from './daemon.ts';
import { useSelectedAgent } from './route.ts';
import { Sidebar } from './Sidebar.tsx';
import { AgentWorkspace } from './Workspace.tsx';

export function App(props: { store: Store; daemon: string; storage: KV | null; connect?: boolean }) {
  return (
    <DaemonProvider store={props.store} where={props.daemon} connect={props.connect}>
      <Frame daemon={props.daemon} storage={props.storage} />
    </DaemonProvider>
  );
}

function Frame({ daemon, storage }: { daemon: string; storage: KV | null }) {
  const agent = useSelectedAgent();
  const waiting = useAgents().filter((a) => dotOf(a) === 'waiting').length;
  const error = useError();
  const store = useStore();
  const [railOpen, setRail] = useState(() => setting(storage, 'rail', '1').value === '1');
  const toggle = () => { setRail(!railOpen); setting(storage, 'rail', '1').save(railOpen ? '0' : '1'); };
  document.title = waiting ? `(${waiting}) Handline` : 'Handline';
  // Everything but the workspace lives in the sidebar, so the main view gets the whole window height.
  return (
    <div id="app" data-rail={railOpen ? 'open' : 'closed'}>
      {railOpen ? <Sidebar selected={agent} storage={storage} onToggle={toggle} />
        : <button className="rail-handle" aria-label="Show the agent list" title="Show the agent list" onClick={toggle}>☰</button>}
      <main className="main" aria-label="Agent workspace">
        {agent ? <AgentWorkspace key={agent} agent={agent} daemon={daemon} storage={storage} />
          : <div className="empty big"><strong>Pick an agent</strong><p>Choose one in the list to open its workspace.</p></div>}
      </main>
      {error ? <div className="toast" role="status" onClick={() => store.dismissError()} title="Click to dismiss">{error}</div> : null}
    </div>
  );
}
