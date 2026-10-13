// Start and the ⋯ button of an agent row, and the agent's context menu (right-click on the row, or the ⋯ button). There is no Resume: a message to an agent with no session wakes it.
import { controlsOf, asks, verbsOf, type Verb, type VerbId } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useSelector, useStore } from '@handline/app-react';
import { useAgentDetail, useVerbMark, useVerbRun, useWrite } from '@handline/app-data';
import { useServed } from './AgentVerbs.tsx';
import { Button, Icon, MenuItem, MenuSeparator, MenuSub, MenuSubContent, MenuSubTrigger, type IconName } from './ui/index.ts';
import { shell } from './shell.ts';

const VERB_ICON: Partial<Record<VerbId, IconName>> = {
  interrupt: 'cancel',
  clear_stall: 'refresh',
  restart: 'retry',
  restart_fresh: 'retry',
  close: 'close',
  park: 'idle',
  fold: 'less',
  move: 'accounts',
};

/** Start, and the button that opens the row's menu at itself. */
export function RowControls({ agent: a, onMenu }: { agent: AgentView; onMenu: (at: { x: number; y: number }) => void }) {
  const agent = a.name;
  const d = useAgentDetail(agent).data;
  const start = useWrite('session.start', agent);
  const store = useStore();
  const mark = useVerbMark(agent);
  const busy = start.isPending || !!mark;
  if (!d) return null;
  const c = controlsOf(d, store.me);
  return (
    <span className="row-actions relative flex flex-none items-center gap-1">
      {c.start ? (
        <Button variant="solid" label="Start" disabled={busy} onClick={() => start.mutate({ agent })}>
          {start.isPending ? (
            'starting…'
          ) : (
            <>
              <Icon name="play" size={12} aria-hidden className="mr-1 inline-block align-[-2px]" /> Start
            </>
          )}
        </Button>
      ) : null}
      <Button
        label={`More actions for ${agent}`}
        aria-label={`More actions for ${agent}`}
        aria-haspopup="menu"
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          onMenu({ x: r.right, y: r.bottom });
        }}
      >
        {mark ?? <Icon name="menu" size={14} aria-hidden />}
      </Button>
    </span>
  );
}

/** The items of the agent's menu: Open in (the folder, on the desktop), the copies, and the session verbs its state allows and the daemon serves. */
export function AgentMenu({ agent: a, onAsk }: { agent: AgentView; onAsk: (v: Verb) => void }) {
  const agent = a.name;
  const d = useAgentDetail(agent).data;
  const run = useVerbRun(agent);
  const served = useServed();
  const busy = !!useVerbMark(agent);
  const closeServed = useSelector((st) => st.served.has('terminal.close'));
  const closeTerminal = useWrite('terminal.close', agent);
  const open = shell('openPath');
  if (!d) return null;
  const copy = (text: string) => void navigator.clipboard?.writeText(text);
  const verbs = verbsOf(a, d).filter(served);
  return (
    <>
      {open ? (
        <>
          <MenuSub>
            <MenuSubTrigger icon="open">Open in</MenuSubTrigger>
            <MenuSubContent>
              <MenuItem icon="open" onSelect={() => void open.openPath!({ path: d.cwd, agent })}>
                Finder
              </MenuItem>
            </MenuSubContent>
          </MenuSub>
          <MenuSeparator />
        </>
      ) : null}
      <MenuItem icon="reference" onSelect={() => copy(d.cwd)}>
        Copy path
      </MenuItem>
      {d.worktree ? (
        <MenuItem icon="reference" onSelect={() => copy(d.worktree!.path.split('/').pop() ?? d.worktree!.path)}>
          Copy worktree name
        </MenuItem>
      ) : null}
      {verbs.length ? <MenuSeparator /> : null}
      {verbs.map((v) => (
        <MenuItem key={v.id} icon={VERB_ICON[v.id]} disabled={busy} onSelect={() => (asks(v) || v.id === 'move' ? onAsk(v) : run(v))}>
          {v.id === 'park' ? 'Sleep' : v.label}
        </MenuItem>
      ))}
      {d.terminal && !d.session_open && closeServed ? (
        <MenuItem icon="close" disabled={busy || closeTerminal.isPending} onSelect={() => closeTerminal.mutate({ agent })}>
          Close terminal
        </MenuItem>
      ) : null}
    </>
  );
}
