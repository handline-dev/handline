// One agent row of the list, always the same height: the name and its marks on the first line, one line under it (agentLine).
import { useEffect, useRef, useState, type HTMLAttributes } from 'react';
import { ago, dotOf, queuedBehind, waitingTasks, rowName, agentStateWord, type Verb, badgesOf, taskRef, whyOf } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useMe } from '@handline/app-react';
import { useOpenTasks, useVerbMark } from '@handline/app-data';
import { DeleteSheet, VerbSheet } from '#src/AgentVerbs.tsx';
import { Badges } from '#src/Badges.tsx';
import { Mark, MARKS } from '#src/RailIcons.tsx';
import { Icon, Menu, type At, MenuContent, MenuTrigger } from '#src/ui/index.ts';
import { goAgent } from '#src/route.ts';
import { AgentMenu, RowControls } from '#src/RowControls.tsx';
import { AnimatedList } from '#src/AnimatedList.tsx';

// What a row's one line holds, in px: the state mark, the name's least width, a chip's icon and padding, a letter of it, and the gap between items.
const MARK_W = 14;
const NAME_MIN = 72;
const CHIP_PAD = 28;
const CHAR_W = 7;
const GAP = 8;
const RESTART_W = 93; // the icon and "Restarting…"
const TIME_W = 34;

export function AgentRow({
  agent: a,
  depth,
  kids,
  working,
  stuck,
  team,
  line,
  folded,
  onFold,
  selected,
  pinned,
  onPin,
  drag,
}: {
  agent: AgentView;
  depth: number;
  kids: number;
  /** Sub-agents working under a folded or a pinned row; 0 when it is open and not pinned, or none work. */
  working: number;
  /** Sub-agents under a folded or a pinned row that cannot go on: held for load, on a dialog, blocked. */
  stuck: number;
  team: string;
  line: string;
  folded: boolean;
  onFold: () => void;
  selected: boolean;
  pinned: boolean;
  onPin: () => void;
  /** Dragging the row to reorder (tree layout): the handlers for the row, and the line where a drop would land. */
  drag?: { attrs: HTMLAttributes<HTMLDivElement>; mark: 'before' | 'after' | null };
}) {
  const me = useMe();
  const dot = dotOf(a);
  const [ask, setAsk] = useState<Verb | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [hot, setHot] = useState(false); // the pointer or focus is on the row: its actions show, and only then do they cost a detail read
  const [menuOpen, setMenuOpen] = useState(false);
  const mark = useVerbMark(a.name);
  // The needs-you chip comes right after the name (the lesser chips drop first) and is the word when the word fits beside the name, else the icon.
  const main = useRef<HTMLButtonElement>(null);
  const [wide, setWide] = useState(true);
  const [roomy, setRoomy] = useState(true); // the word "Restarting…" fits beside the name and the time, else its icon alone
  const kindLen = a.needs_you?.kind.length ?? 0;
  useEffect(() => {
    const el = main.current;
    if (!el) return;
    const fit = () => {
      setWide(el.clientWidth >= MARK_W + NAME_MIN + CHIP_PAD + CHAR_W * kindLen + 2 * GAP);
      setRoomy(!el.clientWidth || el.clientWidth >= MARK_W + NAME_MIN + RESTART_W + TIME_W + 3 * GAP);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [kindLen]);
  const queued = queuedBehind(a) || 0;
  const waiting = waitingTasks(a, useOpenTasks(a.name, hot && queued > 0).data?.items ?? []);
  const queuedTip = [`${queued} waiting behind the current task`, ...waiting.map((t) => `${taskRef(t.id)} ${t.title}`)].join('\n');
  const stamp = a.founder_unread && a.last_to_founder ? a.last_to_founder.at : a.state_since; // when it last wrote to you, else since when it is in this state
  const badges = badgesOf(a, me).filter((b) => b.kind === 'dialog' || b.kind === 'stalled'); // only what the founder must act on
  const tip = [a.name, mark ?? agentStateWord(a.state), ago(a.state_since), a.task ? `${taskRef(a.task.id)} ${a.task.title}` : '']
    .filter(Boolean)
    .join(' · ');
  // The left edge: the waiting hue outranks the selection.
  const edge = dot === 'waiting' ? 'border-l-wait' : selected ? 'border-l-primary' : 'border-l-transparent';
  const showControls = hot || menuOpen || !!mark; // never just because the row is selected; a pending verb's mark is drawn in the button
  // The ⋯ button asks the row for its own context menu, at the button.
  const opener = useRef<((at: At) => void) | null>(null);
  const openMenu = (at: At) => opener.current?.(at);
  return (
    <>
      <Menu onOpenChange={setMenuOpen} openRef={opener}>
        <MenuTrigger asChild>
          <div
            className={`row-wrap relative${selected ? ' on bg-canvas' : ''}${drag?.mark === 'before' ? ' shadow-[inset_0_2px_0_var(--c-primary,currentColor)]' : drag?.mark === 'after' ? ' shadow-[inset_0_-2px_0_var(--c-primary,currentColor)]' : ''}`}
            data-agent={a.name}
            data-drop={drag?.mark ?? undefined}
            {...drag?.attrs}
            onPointerEnter={() => setHot(true)}
            onPointerLeave={() => setHot(false)}
            onFocus={() => setHot(true)}
            onBlur={(e) => !e.currentTarget.contains(e.relatedTarget) && setHot(false)}
          >
            <div
              className={`row${selected ? ' on' : ''}${dot === 'waiting' ? ' attn' : ''} flex items-start gap-1 px-2.5 h-11 py-[3px] border-l-[3px] tint ${edge}`}
              style={{ paddingLeft: 10 + depth * 14 }}
            >
              {kids ? (
                <button
                  className="fold flex-none border-0 bg-transparent p-0 text-ink-2"
                  aria-expanded={!folded}
                  aria-label={`${folded ? 'Show' : 'Hide'} the ${kids} under ${a.name}`}
                  title={`${folded ? 'Show' : 'Hide'} the ${kids} under ${a.name}`}
                  onClick={onFold}
                >
                  <Icon name={folded ? 'chevronRight' : 'more'} size={12} aria-hidden />
                </button>
              ) : (
                <span className="w-3 flex-none" aria-hidden />
              )}
              <div className="flex min-w-0 flex-1 flex-col">
                {/* One line that never wraps: the name gives way first, then the chips drop whole from the last one; the time keeps its place. */}
                <button
                  ref={main}
                  className={`row-main flex h-5 w-full min-w-0 flex-none items-center gap-x-2 overflow-hidden border-0 bg-transparent p-0 text-left`}
                  onClick={() => goAgent(a.name)}
                  aria-current={selected ? 'true' : undefined}
                  title={tip}
                >
                  <Mark
                    dot={dot}
                    title={[MARKS[dot].label, dot === 'blocked' || dot === 'waiting' ? whyOf(a) : null, ago(a.state_since)]
                      .filter(Boolean)
                      .join(' · ')}
                  />
                  <span className="name h-5 min-w-[4.5rem] flex-1 overflow-hidden text-ellipsis whitespace-nowrap leading-5">
                    {rowName(a.name, depth, team)}
                  </span>
                  <span className="row-chips flex h-5 min-w-0 flex-wrap content-start items-center gap-x-2 overflow-hidden [&>*]:h-5">
                    {a.handoff ? (
                      <span
                        title="Restarting…"
                        className="restarting inline-flex flex-none items-center gap-1 whitespace-nowrap text-label text-primary"
                      >
                        <Icon
                          name="refresh"
                          size={12}
                          aria-hidden
                          className="animate-[ui-spin_1.2s_linear_infinite] motion-reduce:animate-none"
                        />
                        {roomy ? 'Restarting…' : null}
                      </span>
                    ) : null}
                    {a.needs_you ? (
                      <span
                        className="needs inline-flex flex-none items-center gap-1 whitespace-nowrap rounded-pill bg-wait px-1.5 text-label text-canvas"
                        title={[a.needs_you.kind, a.needs_you.text].filter(Boolean).join(': ')}
                      >
                        <Icon name={a.needs_you.kind === 'permission' ? 'permission' : 'answer'} size={12} label={a.needs_you.kind} />
                        {wide ? <span>{a.needs_you.kind}</span> : null}
                      </span>
                    ) : null}
                    {a.founder_unread ? (
                      <span
                        className="unread inline-flex flex-none items-center rounded-pill bg-primary px-1.5 text-label text-canvas"
                        title={`${a.founder_unread} unseen ${a.founder_unread === 1 ? 'request' : 'requests'} to you`}
                      >
                        {a.founder_unread}
                      </span>
                    ) : null}
                    <Badges badges={badges} />
                    {stuck ? (
                      <span
                        className="row-stuck inline-flex flex-none items-center gap-0.5 whitespace-nowrap text-hint text-wait"
                        title={`${stuck} under ${a.name} cannot go on: held for load, waiting on a dialog, or blocked`}
                      >
                        <Icon name="warn" size={11} aria-hidden />
                        {stuck}
                      </span>
                    ) : null}
                    {working ? (
                      <span
                        className="row-working inline-flex flex-none items-center gap-0.5 whitespace-nowrap text-hint text-working"
                        title={`${working} working under ${a.name}`}
                      >
                        <Icon name="working" size={11} aria-hidden />
                        {working}
                      </span>
                    ) : null}
                    {queued ? (
                      <span
                        className="row-queued inline-flex flex-none items-center gap-0.5 whitespace-nowrap text-hint text-ink-2"
                        title={queuedTip}
                      >
                        <Icon name="tasks" size={11} aria-hidden />
                        {queued}
                      </span>
                    ) : null}
                  </span>
                  <span className="row-time flex-none whitespace-nowrap text-hint text-ink-3">{ago(stamp)}</span>
                </button>
                <span
                  className={`row-line block h-4 cursor-pointer truncate pl-[22px] text-hint leading-4 ${a.founder_unread ? 'font-medium text-ink' : 'text-ink-2'}`}
                  title={line}
                  onClick={() => goAgent(a.name)}
                >
                  {line}
                </span>
              </div>
              {/* In the row's right column, so the time, badges and preview line move left in full; the row is the same height with or without it. */}
              {showControls ? (
                <div className="flex-none self-center">
                  <RowControls agent={a} onMenu={openMenu} />
                </div>
              ) : null}
            </div>
          </div>
        </MenuTrigger>
        <MenuContent>
          <AgentMenu agent={a} pinned={pinned} onPin={onPin} onAsk={setAsk} onDelete={() => setDeleting(true)} />
        </MenuContent>
      </Menu>
      <AnimatedList>{ask ? <VerbSheet key="VerbSheet" a={a} verb={ask} onClose={() => setAsk(null)} /> : null}</AnimatedList>
      <AnimatedList>{deleting ? <DeleteSheet key="DeleteSheet" a={a} onClose={() => setDeleting(false)} /> : null}</AnimatedList>
    </>
  );
}
