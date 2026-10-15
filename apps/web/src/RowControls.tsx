// The ⋯ button of an agent row, and the agent's context menu (right-click on the row, or the ⋯ button). No Start or Resume: the daemon wakes, parks, restarts and moves agents by itself, so the person is not shown those.
import { asks, verbsOf, type Verb, type VerbId } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useSelector } from '@handline/app-react';
import { useAgentDetail, useVerbMark, useVerbRun, useWrite } from '@handline/app-data';
import { useServed } from './AgentVerbs.tsx';
import { Button, Icon, MenuItem, MenuSeparator, MenuSub, MenuSubContent, MenuSubTrigger, type At, type IconName } from './ui/index.ts';
import { shell } from './shell.ts';

const VERB_ICON: Partial<Record<VerbId, IconName>> = {
  interrupt: 'cancel',
  clear_stall: 'refresh',
};

/** The only session verbs the menu offers: stopping a turn and clearing a stall are the person's call; the rest the daemon does itself. */
const MENU_VERBS: VerbId[] = ['interrupt', 'clear_stall'];

/** The button that opens the row's menu at itself. */
export function RowControls({ agent: a, onMenu }: { agent: AgentView; onMenu: (at: At) => void }) {
  const agent = a.name;
  const mark = useVerbMark(agent);
  return (
    <span className="row-actions relative flex flex-none items-center gap-1">
      <Button
        label={`More actions for ${agent}`}
        aria-label={`More actions for ${agent}`}
        aria-haspopup="menu"
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          onMenu({ x: r.left, y: r.top, w: r.width, h: r.height });
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
  const verbs = verbsOf(a, d).filter((v) => MENU_VERBS.includes(v.id) && served(v));
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
        <MenuItem key={v.id} icon={VERB_ICON[v.id]} disabled={busy} onSelect={() => (asks(v) ? onAsk(v) : run(v))}>
          {v.label}
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
