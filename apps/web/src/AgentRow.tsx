// One agent row of the list: the name and its marks on the first line, the task (or repo/branch) under it, and what the agent is doing now.
import { useEffect, useRef, useState, type HTMLAttributes } from 'react';
import { ago, dotOf, rowName, queueOf, handoffLine, type Verb, badgesOf, taskRef, whyOf } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useStore } from '@handline/app-react';
import { useVerbMark } from '@handline/app-data';
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

/** What the agent is doing now, from what the daemon sends: its newest tool line while a turn runs, else the word beside its state and how long. Empty for an agent at rest. */
export const liveOf = (a: AgentView): string => {
  if (a.handoff) {
    const { why, what } = handoffLine(a.handoff);
    return `${why}: ${what.charAt(0).toLowerCase()}${what.slice(1)}`;
  }
  if (a.state === 'running' && a.doing?.text) return a.doing.text;
  if (a.status) return [a.status.text, ago(a.status.since ?? a.state_since)].filter(Boolean).join(' · ');
  const d = dotOf(a);
  if (d !== 'running' && d !== 'waking' && !a.state_detail) return '';
  return [a.state_detail || MARKS[d].label, ago(a.state_since)].filter(Boolean).join(' · ');
};

export function AgentRow({
  agent: a,
  depth,
  kids,
  team,
  sub,
  live,
  folded,
  onFold,
  unreached,
  selected,
  pinned,
  onPin,
  drag,
}: {
  agent: AgentView;
  depth: number;
  kids: number;
  team: string;
  sub: string;
  live: string;
  folded: boolean;
  onFold: () => void;
  unreached: number | null;
  selected: boolean;
  pinned: boolean;
  onPin: () => void;
  /** Dragging the row to reorder (tree layout): the handlers for the row, and the line where a drop would land. */
  drag?: { attrs: HTMLAttributes<HTMLDivElement>; mark: 'before' | 'after' | null };
}) {
  const store = useStore();
  const dot = dotOf(a);
  const [ask, setAsk] = useState<Verb | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [hot, setHot] = useState(false); // the pointer or focus is on the row: its actions show, and only then do they cost a detail read
  const [menuOpen, setMenuOpen] = useState(false);
  const mark = useVerbMark(a.name);
  // The needs-you chip comes right after the name (the lesser chips drop first) and is the word when the word fits beside the name, else the icon.
  const main = useRef<HTMLButtonElement>(null);
  const [wide, setWide] = useState(true);
  const kindLen = a.needs_you?.kind.length ?? 0;
  useEffect(() => {
    const el = main.current;
    if (!el) return;
    const fit = () => setWide(el.clientWidth >= MARK_W + NAME_MIN + CHIP_PAD + CHAR_W * kindLen + 2 * GAP);
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [kindLen]);
  const queue = queueOf(a, unreached);
  const badges = badgesOf(a, store.me).filter((b) => b.kind !== 'no_terminal'); // the word already says it
  const tip = [a.name, mark ?? a.state, ago(a.state_since), a.task ? `${taskRef(a.task.id)} ${a.task.title}` : '']
    .filter(Boolean)
    .join(' · ');
  // The left edge: the waiting hue outranks the selection.
  const edge = dot === 'waiting' ? 'border-l-wait' : selected ? 'border-l-primary' : 'border-l-transparent';
  const showControls = selected || hot || menuOpen;
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
              className={`row${selected ? ' on' : ''}${dot === 'waiting' ? ' attn' : ''} flex items-start gap-1 px-2.5 py-[7px] border-l-[3px] hover:bg-canvas ${edge}`}
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
                {/* One line tall and wrapping: a chip that does not fit drops below the box whole, never cut off. */}
                <button
                  ref={main}
                  className={`row-main flex h-5 min-w-0 flex-1 flex-wrap content-start items-center gap-x-2 overflow-hidden border-0 bg-transparent p-0 text-left${showControls ? ' pr-7' : ''}`}
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
                  {a.handoff ? (
                    <span className="restarting inline-flex flex-none items-center gap-1 whitespace-nowrap text-label text-primary">
                      <Icon
                        name="refresh"
                        size={12}
                        aria-hidden
                        className="animate-[ui-spin_1.2s_linear_infinite] motion-reduce:animate-none"
                      />
                      Restarting…
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
                  {a.last_to_founder ? (
                    <span className="row-time flex-none whitespace-nowrap text-hint text-ink-3" title={a.last_to_founder.at}>
                      {ago(a.last_to_founder.at)}
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
                  {queue.messages ? (
                    <span
                      className="q-msgs inline-flex flex-none items-center gap-0.5 text-hint text-ink-2"
                      title={`${queue.messages} messages not yet delivered`}
                    >
                      <Icon name="inbox" size={12} aria-hidden />
                      {queue.messages}
                    </span>
                  ) : null}
                  {queue.tasks && !sub ? (
                    <span
                      className="q-tasks inline-flex flex-none items-center gap-0.5 text-hint text-ink-2"
                      title={`${queue.tasks} tasks queued or doing`}
                    >
                      <Icon name="tasks" size={12} aria-hidden />
                      {queue.tasks}
                    </span>
                  ) : null}
                </button>
                {sub ? (
                  <span
                    className={`row-sub flex cursor-pointer items-center gap-1 pl-[22px] text-hint ${a.founder_unread ? 'font-medium text-ink' : 'text-ink-2'}`}
                    title={sub}
                    onClick={() => goAgent(a.name)}
                  >
                    <span className="truncate">{sub}</span>
                    {queue.tasks ? (
                      <span
                        className="q-tasks ml-auto inline-flex flex-none items-center gap-0.5"
                        title={`${queue.tasks} tasks queued or doing`}
                      >
                        <Icon name="tasks" size={12} aria-hidden />
                        {queue.tasks}
                      </span>
                    ) : null}
                  </span>
                ) : null}
                {live ? (
                  <span
                    className="row-live flex cursor-pointer items-center gap-1 truncate pl-[22px] text-hint text-ink-3"
                    title={live}
                    onClick={() => goAgent(a.name)}
                  >
                    <span className="truncate">{live}</span>
                  </span>
                ) : null}
              </div>
              {/* Laid over the row's right end, not beside the name: the row is the same height with or without them. */}
              {showControls ? (
                <div className="absolute right-1 top-1/2 -translate-y-1/2 bg-canvas">
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
