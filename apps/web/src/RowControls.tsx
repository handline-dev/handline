// The ⋯ button of an agent row, and the agent's context menu (right-click on the row, or the ⋯ button). No Start or Resume: the daemon wakes, parks, restarts and moves agents by itself, so the person is not shown those.
import { asks, verbsOf, type Verb, type VerbId } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useAgentDetail, useVerbMark, useVerbRun } from '@handline/app-data';
import { useSelector } from '@handline/app-react';
import { useServed } from '#src/AgentVerbs.tsx';
import { Button, Icon, MenuItem, MenuSeparator, MenuSub, MenuSubContent, MenuSubTrigger, type At, type IconName } from '#src/ui/index.ts';
import { shell } from '#src/shell.ts';
import { goSettings } from '#src/route.ts';

const VERB_ICON: Partial<Record<VerbId, IconName>> = {
  interrupt: 'cancel',
  clear_stall: 'refresh',
};

/** The only session verbs the menu offers: stopping a turn is the person's call; the rest the daemon does itself. */
const MENU_VERBS: VerbId[] = ['interrupt'];

/** The row's two buttons: the agent's Settings, and the one that opens the row's menu at itself. */
export function RowControls({ agent: a, onMenu }: { agent: AgentView; onMenu: (at: At) => void }) {
  const agent = a.name;
  const mark = useVerbMark(agent);
  return (
    <span className="row-actions relative flex flex-none items-center gap-1">
      <Button
        label={`Settings of ${agent}`}
        aria-label={`Settings of ${agent}`}
        size="sm"
        className="!rounded-full !border-line bg-surface w-7 p-0"
        onClick={() => goSettings(agent)}
      >
        <Icon name="settings" size={14} aria-hidden />
      </Button>
      <Button
        label={`More actions for ${agent}`}
        aria-label={`More actions for ${agent}`}
        aria-haspopup="menu"
        size="sm"
        className="!rounded-full !border-line bg-surface w-7 p-0"
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
export function AgentMenu({
  agent: a,
  pinned,
  onPin,
  onAsk,
  onDelete,
}: {
  agent: AgentView;
  pinned: boolean;
  onPin: () => void;
  onAsk: (v: Verb) => void;
  onDelete: () => void;
}) {
  const agent = a.name;
  const d = useAgentDetail(agent).data;
  const run = useVerbRun(agent);
  const served = useServed();
  const canRetire = useSelector((s) => s.served).has('agent.retire');
  const busy = !!useVerbMark(agent);
  const open = shell('openPath');
  if (!d) return null;
  const copy = (text: string) => void navigator.clipboard?.writeText(text);
  const verbs = verbsOf(a, d).filter((v) => MENU_VERBS.includes(v.id) && served(v));
  return (
    <>
      <MenuItem icon="pin" onSelect={onPin}>
        {pinned ? 'Unpin' : 'Pin'}
      </MenuItem>
      <MenuSeparator />
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
      {canRetire ? (
        <>
          <MenuSeparator />
          <MenuItem icon="close" danger disabled={busy} onSelect={onDelete}>
            Delete agent
          </MenuItem>
        </>
      ) : null}
    </>
  );
}
