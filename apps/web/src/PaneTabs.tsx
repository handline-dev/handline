// The tabs of the focused pane, in the top bar: the tab run (drag to reorder, close, the unread dot, a menu for what does not fit) and the
// + and Split buttons. A pane keeps no strip of its own; when the window is split it keeps a thin handle (Pane.tsx).
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useScreenKeys, type KeyRows } from '#src/screenKeys.ts';
import {
  badgesOf,
  needsAttention,
  closeTab,
  fitTabs,
  moveTab,
  fillTab,
  groupsOf,
  moveTabBy,
  openTab,
  splitPane,
  CHAT,
  taskRef,
  type Group,
  type Tab,
  type TabKind,
  type TabParams,
  type Workspace,
} from '@handline/app-core';
import { Button, Icon, MenuButton, MenuItem, type IconName } from '#src/ui/index.ts';
import { Badges } from '#src/Badges.tsx';
import { WithKey } from '#src/WithKey.tsx';
import { showPill } from '#src/Pill.tsx';
import { useWorkspace } from '#src/Workspace.tsx';
import { usePaneData, type PaneData } from '#src/PaneData.ts';
import { lineAt, slotAt } from '#src/tabSlots.ts';

/** A tab's name; `agent` (two agents side by side) puts whose it is in front. */
export const tabLabel = (t: Tab, structured = false, agent?: string): string =>
  agent ? `${agent} · ${plainLabel(t, structured)}` : plainLabel(t, structured);

const plainLabel = (t: Tab, structured: boolean): string =>
  t.kind === 'chat'
    ? 'Chat'
    : t.kind === 'new'
      ? 'New tab'
      : t.kind === 'tasks'
        ? t.params.pinned
          ? taskRef(Number(t.params.task))
          : 'Tasks'
        : t.kind === 'android'
          ? 'Android'
          : t.kind === 'ios'
            ? 'iOS'
            : t.kind === 'web'
              ? String(t.params.url ?? 'Preview').replace(/^https?:\/\//, '')
              : t.kind === 'transcript' || (t.kind === 'terminal' && structured)
                ? 'Transcript'
                : t.kind === 'terminal'
                  ? 'Claude Code'
                  : t.kind === 'shell'
                    ? t.params.title
                      ? `Terminal · ${t.params.title}`
                      : 'Terminal'
                    : `${t.kind}: ${Object.values(t.params)[0] ?? ''}`;

export const TAB_ICON: Record<string, IconName> = {
  chat: 'chat',
  tasks: 'tasks',
  terminal: 'terminal',
  transcript: 'terminal',
  shell: 'terminal',
  web: 'web',
  android: 'device',
  ios: 'device',
  new: 'plus',
};

const TAB_KEYS: KeyRows = [
  ['Alt+Left Alt+Right', 'move the focused tab along the bar'],
  ['Enter Space', 'show the focused tab'],
];

/** The narrowest a tab gets before the rest go to the menu, and the room the menu's own button takes. */
const MIN_TAB = 96;
const MORE_W = 76;

type Choice = { kind: TabKind; params: TabParams; label: string; icon: IconName; disabled?: boolean; open?: boolean };

/** What a new tab can show: the same flat list for the + menu and for the picker in a freshly split pane. */
export function useChoices(openShell: PaneData['openShell']) {
  const { agent, apply } = useWorkspace();
  const choices: Choice[] = [
    // A new plain shell in the agent's worktree, not its Claude Code terminal (that one is its own tab); several may be open.
    { kind: 'shell', params: {}, label: 'Terminal', icon: 'terminal', open: true },
    { kind: 'tasks', params: {}, label: 'Tasks', icon: 'tasks' },
    { kind: 'android', params: {}, label: 'Android', icon: 'device' },
    { kind: 'ios', params: {}, label: 'iOS', icon: 'device' },
  ];
  /** Opens what a choice shows; a new shell is first asked of the daemon and then shown by its handle. */
  const choose = async (c: Choice, place: (w: Workspace, kind: TabKind, params: TabParams) => Workspace) => {
    let params = c.params;
    if (c.open) {
      try {
        params = { handle: (await openShell({ agent })).terminal };
      } catch {
        return; // useWrite has reported it
      }
    }
    apply((w) => place(w, c.kind, params));
  };
  return { choices, choose };
}

/** The tab run of `group`; the data it reads is taken here, the view below holds none. */
export function PaneTabs({ group }: { group: Group }) {
  const { agent } = useWorkspace();
  return <TabRun group={group} data={usePaneData(agent)} />;
}

function TabRun({ group, data: { me, closeShell, openShell } }: { group: Group; data: PaneData }) {
  const { choices, choose } = useChoices(openShell);
  useScreenKeys('On the tabs', TAB_KEYS);
  const { ws, detail, agent, named, apply, show, drop, dragging, setDragging } = useWorkspace();
  const run = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [line, setLine] = useState<number | null>(null);
  const at = useRef(-1);
  const badges = badgesOf(detail, me);
  const structured = detail.structured === true;
  const label = (t: Tab) => tabLabel(t, structured, named ? agent : undefined);
  useLayoutEffect(() => {
    const el = run.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el); // observing delivers the first size too
    return () => ro.disconnect();
  }, []);

  // A terminal tab that is not showing gets a dot when its agent newly needs someone (a dialog, a stall); showing it clears the dot.
  // The first terminal tab of the group: a group holds one terminal tab per agent, so the unread dot follows that one.
  const termId = group.tabs.find((id) => ws.tabs[id]?.kind === 'terminal' || ws.tabs[id]?.kind === 'transcript');
  const termShown = termId !== undefined && termId === group.activeTab;
  const attention = needsAttention(badges);
  const wasAttention = useRef(attention);
  const [unread, setUnread] = useState(false);
  useEffect(() => {
    if (attention && !wasAttention.current && termId !== undefined && !termShown) setUnread(true);
    wasAttention.current = attention;
  }, [attention, termId, termShown]);
  if (termShown && unread) setUnread(false); // showing the terminal clears the dot: state adjusted while rendering, not in an effect

  // Claude Code is reached by the switch (Cmd/Ctrl+J, /terminal, the icon in the bottom bar), not by a tab, while the chat is in the group.
  const viaSwitch = (id: string) => (ws.tabs[id]?.kind === 'terminal' || ws.tabs[id]?.kind === 'transcript') && group.tabs.includes(CHAT);
  const hiddenActive = group.tabs.includes(CHAT) && ['terminal', 'transcript'].includes(ws.tabs[group.activeTab]?.kind ?? '');
  const listed = group.tabs.filter((id) => !viaSwitch(id));
  const fits = !width || listed.length * MIN_TAB <= width;
  const { shown, more } = fitTabs(listed, hiddenActive ? CHAT : group.activeTab, fits ? listed.length : (width - MORE_W) / MIN_TAB);
  const hidden = new Set(group.tabs.filter((id) => !shown.includes(id) && !more.includes(id))); // the switch's tabs: in the run, not shown

  return (
    <div
      className="strip relative flex min-w-0 flex-1 items-center gap-0.5 self-stretch"
      role="tablist"
      ref={run}
      onDragOver={(e) => {
        if (!dragging) return;
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
        const bar = run.current!;
        at.current = slotAt(bar, group, e.clientX);
        setLine(lineAt(bar, at.current, bar.getBoundingClientRect().left));
      }}
      onDragLeave={(e) => !e.currentTarget.contains(e.relatedTarget as Node | null) && setLine(null)}
      onDrop={(e) => {
        if (!dragging) return;
        e.preventDefault();
        setLine(null);
        drop(moveTab(ws, dragging, group.id, at.current >= 0 ? at.current : undefined));
      }}
    >
      {group.tabs.map((id) => {
        const t = ws.tabs[id]!;
        if (!shown.includes(id) && !hidden.has(id)) return null;
        const asleep = id === CHAT && hiddenActive; // Claude Code is showing: the Chat tab stays the bar's one tab, quiet
        return (
          <div
            key={id}
            role="tab"
            tabIndex={0}
            aria-selected={id === group.activeTab || asleep}
            className={`tab group ${hidden.has(id) ? 'hidden' : 'inline-flex'} h-[var(--h-m)] min-w-24 max-w-[200px] shrink cursor-default select-none items-center gap-1.5 whitespace-nowrap rounded-sm px-2.5 font-sans text-sm focus-visible:outline-2 focus-visible:outline-acc shell:[-webkit-app-region:no-drag] ${id === group.activeTab ? 'on bg-panel text-ink' : asleep ? 'text-ink-2 bg-panel/50 tint' : 'text-ink-2 tint'}`}
            data-tab={id}
            draggable={id !== CHAT}
            onDragStart={(e) => {
              e.dataTransfer?.setData('text/plain', id);
              // The drop cover appears over the pane; mounting it inside dragstart makes Chromium cancel the drag.
              setTimeout(() => setDragging(id), 0);
            }}
            onDragEnd={() => setDragging(null)}
            onClick={() => show(id)}
            onKeyDown={(e) => {
              if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
                e.preventDefault();
                apply((w) => moveTabBy(w, id, e.key === 'ArrowLeft' ? -1 : 1));
              } else if (e.key === 'Enter' || e.key === ' ') show(id);
            }}
          >
            <Icon name={TAB_ICON[t.kind] ?? 'tasks'} size={14} label={label(t)} aria-hidden />
            <span className="tab-label overflow-hidden text-ellipsis">{label(t)}</span>
            {t.kind === 'terminal' || t.kind === 'transcript' ? (
              <>
                <Badges badges={badges} onClick={{ dialog: () => show(CHAT) }} />
                {unread && id !== group.activeTab ? (
                  <span className="unread-dot ml-1 inline-block h-[7px] w-[7px] rounded-full bg-wait" title="Needs you" />
                ) : null}
              </>
            ) : null}
            {id === CHAT ? null : (
              <button
                className="x inline-flex border-0 bg-transparent p-0.5 text-ink-3 opacity-0 focus-visible:opacity-100 group-hover:opacity-100 group-aria-selected:opacity-100"
                aria-label={`Close ${label(t)}`}
                title={
                  t.kind === 'terminal' || t.kind === 'transcript'
                    ? 'Close the tab; the agent keeps working'
                    : t.kind === 'shell'
                      ? 'Close the terminal'
                      : 'Close the tab'
                }
                onClick={(e) => {
                  e.stopPropagation();
                  if (t.kind === 'terminal' && !structured && detail.session_open) showPill(`${agent} keeps working in the background`);
                  if (t.kind === 'shell') closeShell({ terminal: String(t.params.handle) }); // the shell ends with its tab
                  apply((w) => closeTab(w, id));
                }}
              >
                <Icon name="close" size={12} label="Close" aria-hidden />
              </button>
            )}
          </div>
        );
      })}
      {more.length ? (
        <MenuButton
          size="sm"
          label={`${more.length} more tabs`}
          name="More tabs"
          className="more-tabs flex-none shell:[-webkit-app-region:no-drag]"
          align="start"
          trigger={`${more.length} more`}
        >
          {more.map((id) => (
            <MenuItem key={id} icon={TAB_ICON[ws.tabs[id]!.kind] ?? 'tasks'} onSelect={() => show(id)}>
              {label(ws.tabs[id]!)}
            </MenuItem>
          ))}
        </MenuButton>
      ) : null}
      <WithKey k="⌘T">
        <MenuButton icon="plus" size="sm" label="Add a tab" name="Add a tab" className="flex-none shell:[-webkit-app-region:no-drag]">
          {choices.map((c) => (
            <MenuItem
              key={c.label}
              icon={c.icon}
              disabled={c.disabled}
              onSelect={() => void choose(c, (w, kind, params) => openTab(w, kind, params, group.id))}
            >
              {c.label}
            </MenuItem>
          ))}
        </MenuButton>
      </WithKey>
      {line != null ? (
        <div className="drop-line pointer-events-none absolute top-1 h-6 w-0.5 rounded-full bg-acc" style={{ left: line - 1 }} />
      ) : null}
    </div>
  );
}

/** The bar's Split for `group`: the same chooser as the + after the tabs, so the new pane opens on what was chosen. */
export function PaneActions({ group }: { group: Group }) {
  const { agent, canSplit, closeSplit } = useWorkspace();
  const { choices, choose } = useChoices(usePaneData(agent).openShell);
  // The new pane holds a "new" tab until what was chosen takes its place.
  const splitWith = (w: Workspace, kind: TabKind, params: TabParams): Workspace => {
    const next = splitPane(w, group.id);
    const fresh = next && groupsOf(next.root).find((g) => g.id === next.active);
    return next && fresh ? fillTab(next, fresh.activeTab, kind, params) : w;
  };
  return closeSplit ? (
    <Button icon="close" size="sm" label="Close the split" className="shell:[-webkit-app-region:no-drag]" onClick={closeSplit} />
  ) : (
    <WithKey k="⌘\">
      <MenuButton
        icon="split"
        size="sm"
        label="Split this pane"
        name="Split this pane"
        align="end"
        disabled={!canSplit(group.id)}
        className="shell:[-webkit-app-region:no-drag]"
      >
        {choices.map((c) => (
          <MenuItem key={c.label} icon={c.icon} disabled={c.disabled} onSelect={() => void choose(c, splitWith)}>
            {c.label}
          </MenuItem>
        ))}
      </MenuButton>
    </WithKey>
  );
}
