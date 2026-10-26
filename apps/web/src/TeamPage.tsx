// The Team and HR tabs of the Settings area: Team (who is who, and who may ask whom) and HR (the proposals that wait for the founder).
import { useState } from 'react';
import { taskRef } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { useServes, useTeamBoard, type TeamNode } from '@handline/app-data';
import { HrTab } from '#src/HrTab.tsx';
import { TeamSeal } from '#src/TeamSeal.tsx';
import { Button, Dialog, Empty, Icon, Segmented, State, stateOfView } from '#src/ui/index.ts';
import { SettingsSection, SettingsShell } from '#src/SettingsShell.tsx';
import { goAgent, goSettings, useTeamRoute } from '#src/route.ts';
import { View } from '#src/View.tsx';
import { Reveal } from '#src/ui/Reveal.tsx';

type Look = 'tree' | 'chart';
const LOOK_KEY = 'team-look';

/** The look is remembered by this window only; a browser that refuses storage starts on the tree. */
function useLook(): [Look, (l: Look) => void] {
  const [look, set] = useState<Look>(() => {
    try {
      return localStorage.getItem(LOOK_KEY) === 'chart' ? 'chart' : 'tree';
    } catch {
      return 'tree';
    }
  });
  return [
    look,
    (l) => {
      set(l);
      try {
        localStorage.setItem(LOOK_KEY, l);
      } catch {
        /* not remembered */
      }
    },
  ];
}

export function TeamPage() {
  const route = useTeamRoute()!;
  const { served: hrServed } = useServes('hr.list');
  const [look, setLook] = useLook();
  return (
    <SettingsShell tab={route.tab} label="Team">
      {route.tab === 'hr' && hrServed ? (
        <HrTab focus={route.proposal} />
      ) : (
        <SettingsSection
          title="Team"
          action={
            <span className="flex items-center gap-2">
              <Segmented
                label="Team view"
                value={look}
                onChange={setLook}
                options={[
                  { value: 'tree', label: 'Tree' },
                  { value: 'chart', label: 'Chart' },
                ]}
              />
              <TeamSeal />
            </span>
          }
        >
          <TeamBoard look={look} />
        </SettingsSection>
      )}
    </SettingsShell>
  );
}

function TeamBoard({ look }: { look: Look }) {
  const { query: q, served, team, tree, edges, doing } = useTeamBoard();
  return (
    <View q={q} served={served} shape="team" what="the team" empty={false}>
      {team && !team.agents.length ? (
        <Empty icon="team" name="No agents in the team" />
      ) : look === 'tree' ? (
        <TeamTree nodes={tree} doing={doing} edges={edges} />
      ) : (
        <TeamChart nodes={tree} doing={doing} edges={edges} />
      )}
    </View>
  );
}

type Edges = { from: string; to: string }[];
type Doing = (name: string) => AgentView | undefined;

/** What an agent is called under its manager: the last part of its name. */
const shortName = (name: string) => name.slice(name.lastIndexOf('.') + 1);

function StateOf({ d }: { d: AgentView | undefined }) {
  return d ? (
    <State
      kind={stateOfView(d)}
      label={d.status?.text || [d.block ? `blocked (${d.block.kind})` : d.state, d.state_detail].filter(Boolean).join(' · ')}
    />
  ) : (
    <span className="inline-block w-3.5" aria-hidden />
  );
}

/** The team as an indented list like the sidebar's: a team folds, a row opens who it may ask and the way to its chat and settings. */
function TeamTree({ nodes, doing, edges }: { nodes: TeamNode[]; doing: Doing; edges: Edges }) {
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const flip = (name: string) =>
    setFolded((s) => {
      const n = new Set(s);
      if (!n.delete(name)) n.add(name);
      return n;
    });
  const rows = (list: TeamNode[], depth: number): { n: TeamNode; depth: number }[] =>
    list.flatMap((n) => [{ n, depth }, ...(folded.has(n.agent.name) ? [] : rows(n.children, depth + 1))]);
  return (
    <div className="team-board flex flex-col rounded-card bg-surface" role="tree" aria-label="Team">
      {rows(nodes, 0).map(({ n, depth }) => {
        const a = n.agent;
        const d = doing(a.name);
        const kids = n.children.length > 0;
        return (
          <div key={a.name} className="[&:not(:first-child)]:border-t [&:not(:first-child)]:border-line">
            <div
              className="team-row grid min-h-9 cursor-pointer grid-cols-[minmax(0,1.3fr)_minmax(0,2fr)_minmax(0,1fr)_minmax(0,0.8fr)] items-center gap-x-3 px-3 py-1.5 hover:bg-raised"
              role="treeitem"
              aria-level={depth + 1}
              aria-expanded={kids ? !folded.has(a.name) : undefined}
              data-agent={a.name}
              onClick={() => setOpen(open === a.name ? null : a.name)}
            >
              <span className="flex min-w-0 items-center gap-1.5" style={{ paddingLeft: `${depth * 16}px` }}>
                {kids ? (
                  <button
                    type="button"
                    className="team-fold inline-flex border-0 bg-transparent p-0 text-ink-3"
                    aria-label={folded.has(a.name) ? `Show ${shortName(a.name)}'s team` : `Fold ${shortName(a.name)}'s team`}
                    onClick={(e) => (e.stopPropagation(), flip(a.name))}
                  >
                    <Icon name={folded.has(a.name) ? 'more' : 'less'} size={14} aria-hidden />
                  </button>
                ) : (
                  <span className="inline-block w-3.5" aria-hidden />
                )}
                <StateOf d={d} />
                <span className="team-name min-w-0 truncate font-medium" title={a.name}>
                  {depth ? shortName(a.name) : a.name}
                </span>
              </span>
              <span
                className="team-task min-w-0 truncate text-label text-ink-2"
                title={d?.task ? `${taskRef(d.task.id)} ${d.task.title}` : undefined}
              >
                {d?.task ? `${taskRef(d.task.id)} ${d.task.title}` : ''}
              </span>
              <span className="team-model min-w-0 truncate text-label text-ink-2">
                {d?.model ? `${d.model}${d.effort ? ` · ${d.effort}` : ''}` : ''}
              </span>
              <span className="team-account min-w-0 truncate text-label text-ink-2">{d?.account ?? ''}</span>
            </div>
            <Reveal show={open === a.name}>
              <div className="px-3 pb-2" style={{ paddingLeft: `${depth * 16 + 12 + 20}px` }}>
                <AgentDetail name={a.name} edges={edges} />
              </div>
            </Reveal>
          </div>
        );
      })}
    </div>
  );
}

/** Who the agent may ask and be asked by, and the way to its chat and its settings. No role text. */
function AgentDetail({ name, edges }: { name: string; edges: Edges }) {
  const chips = (names: string[]) =>
    names.length ? (
      names.map((n) => (
        <button
          key={n}
          className="chip inline-block rounded-[12px] border border-line bg-panel py-0 pr-1 pl-2 font-mono text-[12px]/4"
          onClick={() => goAgent(n)}
        >
          {n}
        </button>
      ))
    ) : (
      <span className="muted">nobody</span>
    );
  return (
    <div className="edges grid gap-1.5 [&_svg]:text-ink-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Icon name="send" label="May ask" size={14} />
        {chips(edges.filter((e) => e.from === name).map((e) => e.to))}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Icon name="reply" label="May be asked by" size={14} />
        {chips(edges.filter((e) => e.to === name).map((e) => e.from))}
      </div>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" label="Open chat" onClick={() => goAgent(name)} />
        <Button size="sm" variant="outline" label="Agent settings" onClick={() => goSettings(name)} />
      </div>
    </div>
  );
}

/** The same team left to right, a box per agent with its reports to its right; a box opens the same detail in a small window. */
function TeamChart({ nodes, doing, edges }: { nodes: TeamNode[]; doing: Doing; edges: Edges }) {
  const [picked, setPicked] = useState<string | null>(null);
  return (
    <div className="team-chart flex flex-col gap-3 overflow-x-auto rounded-card bg-surface p-4" aria-label="Team chart">
      {nodes.map((n) => (
        <ChartNode key={n.agent.name} node={n} doing={doing} pick={setPicked} />
      ))}
      {picked ? (
        <Dialog title={picked} onClose={() => setPicked(null)}>
          <AgentDetail name={picked} edges={edges} />
        </Dialog>
      ) : null}
    </div>
  );
}

function ChartNode({ node, doing, pick }: { node: TeamNode; doing: Doing; pick: (name: string) => void }) {
  const a = node.agent;
  const d = doing(a.name);
  return (
    <div className="flex items-center gap-4">
      <button
        type="button"
        className="team-box flex min-w-0 max-w-56 flex-none cursor-pointer items-center gap-1.5 rounded-card border border-line bg-panel px-2.5 py-1.5 text-left hover:bg-raised"
        data-agent={a.name}
        onClick={() => pick(a.name)}
      >
        <StateOf d={d} />
        <span className="truncate font-medium" title={a.name}>
          {shortName(a.name)}
        </span>
      </button>
      {node.children.length ? (
        <div className="flex flex-col gap-2 border-l border-line pl-4">
          {node.children.map((c) => (
            <ChartNode key={c.agent.name} node={c} doing={doing} pick={pick} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
