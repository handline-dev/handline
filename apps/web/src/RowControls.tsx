// The ⋯ button of an agent row, and the agent's context menu (right-click on the row, or the ⋯ button): Pin to top, Settings…, Open in, Mark as read and Stop this turn. No Start or Resume: the daemon wakes, parks, restarts and moves agents by itself, so the person is not shown those.
import { asks, verbsOf, type Verb, type VerbId } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useAgentDetail, useVerbMark, useVerbRun, useWrite } from '@handline/app-data';
import { useSelector } from '@handline/app-react';
import { useServed } from '#src/AgentVerbs.tsx';
import { Button, Icon, MenuItem, MenuSeparator, MenuSub, MenuSubContent, MenuSubTrigger, type At, type IconName } from '#src/ui/index.ts';
import { OpenInItems } from '#src/OpenInItems.tsx';
import { shell } from '#src/shell.ts';
import { goSettings } from '#src/route.ts';

const VERB_ICON: Partial<Record<VerbId, IconName>> = {
  interrupt: 'cancel',
  clear_stall: 'refresh',
};

/** The only session verbs the menu offers: stopping a turn is the person's call; the rest the daemon does itself. */
const MENU_VERBS: VerbId[] = ['interrupt'];

/** The row's one button: it opens the row's menu at itself. */
export function RowControls({ agent: a, onMenu }: { agent: AgentView; onMenu: (at: At) => void }) {
  const agent = a.name;
  const mark = useVerbMark(agent);
  return (
    <span className="row-actions relative flex flex-none items-center gap-1">
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

/** The items of the agent's menu: Open in (the folder, on the desktop) and the session verbs its state allows and the daemon serves. */
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
  const client = useSelector((s) => s.client);
  const seen = useWrite('chat.seen', agent);
  const open = shell('openPath');
  if (!d) return null;
  // The newest message the agent wrote to the person: everything up to it leaves the unseen count. Pages back until one is found.
  const markRead = async () => {
    for (let before: number | undefined; ;) {
      const page = await client.call('chat.messages', { agent, scope: 'mine', limit: 50, before });
      const up_to = page.messages.find((m) => m.from === agent && m.id > 0)?.id;
      if (up_to) return void seen.mutate({ agent, up_to });
      before = Math.min(...page.messages.map((m) => m.id));
      if (!page.more || !page.messages.length) return;
    }
  };
  const verbs = verbsOf(a, d).filter((v) => MENU_VERBS.includes(v.id) && served(v));
  return (
    <>
      <MenuItem icon="pin" onSelect={onPin}>
        {pinned ? 'Unpin' : 'Pin to top'}
      </MenuItem>
      <MenuItem icon="settings" onSelect={() => goSettings(agent)}>
        Settings…
      </MenuItem>
      <MenuSeparator />
      {open ? (
        <>
          <MenuSub>
            <MenuSubTrigger icon="open">Open in</MenuSubTrigger>
            <MenuSubContent>
              <OpenInItems agent={agent} cwd={d.cwd} withCopy={false} />
            </MenuSubContent>
          </MenuSub>
          <MenuSeparator />
        </>
      ) : null}
      {verbs.length || a.founder_unread ? <MenuSeparator /> : null}
      {a.founder_unread ? (
        <MenuItem icon="done" onSelect={() => void markRead()}>
          Mark as read
        </MenuItem>
      ) : null}
      {verbs.map((v) => (
        <MenuItem key={v.id} icon={VERB_ICON[v.id]} disabled={busy} onSelect={() => (asks(v) ? onAsk(v) : run(v))}>
          {v.id === 'interrupt' ? 'Stop this turn' : v.label}
        </MenuItem>
      ))}
      {canRetire ? (
        <>
          <MenuSeparator />
          <MenuItem icon="close" danger disabled={busy} onSelect={onDelete}>
            Delete agent…
          </MenuItem>
        </>
      ) : null}
    </>
  );
}
