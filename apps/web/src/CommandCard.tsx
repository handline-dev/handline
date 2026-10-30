// A fenced sh/bash/shell block in a message: the folder it belongs to, and Copy. Other fences stay plain code (Markdown.tsx).
import { useEffect, useState } from 'react';
import { useAgentDetail, useRead, useWrite } from '@handline/app-data';
import type { CommandRun } from '@handline/client';
import { CHAT, fillTab, groupsOf, openTab, splitPane } from '@handline/app-core';
import { useNow } from '#src/Gauge.tsx';
import { useWorkspaceIfAny } from '#src/Workspace.tsx';
import { Button } from '#src/ui/index.ts';

/** Which command block of which message a card is, and its last run. */
export interface BlockRef {
  message: number;
  /** 'r' on an agent's own reply (a transcript item), whose block key is `r<id>:<n>`. */
  prefix?: string;
  index: number;
  run?: CommandRun;
}

const runLine = (r: CommandRun, now: number) => {
  const s = Math.max(0, Math.round((now - Date.parse(r.started_at)) / 1000));
  if (r.state === 'running') return `Running · ${s}s`;
  if (r.state === 'stopped') return 'Stopped';
  return [
    r.state === 'done' ? '✓ Done' : '✗ Failed',
    r.exit === undefined ? null : `exit ${r.exit}`,
    r.seconds === undefined ? null : `${r.seconds}s`,
  ]
    .filter(Boolean)
    .join(' · ');
};

/** `/Users/me/work/x` as `~/work/x`. */
const homeShort = (path: string) => path.replace(/^\/(Users|home)\/[^/]+/, '~');

export const isShell = (lang: string) => /^(sh|bash|shell|zsh)$/i.test(lang);

const RISKY: [RegExp, string][] = [
  [/\brm\s+(-\w*r\w*f|-\w*f\w*r)\b|\brm\s+-r\b.*\s-f\b/, 'Deletes files for good.'],
  [/\bsudo\b/, 'Runs with administrator rights.'],
  [/\bgit\s+push\b.*(--force\b|\s-f\b)/, 'Overwrites history on the remote.'],
  [/\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b/, 'Runs a downloaded script unread.'],
];

/** Why a command is risky, in a plain line; null when it is not one of the known risky shapes. */
export const riskOf = (command: string): string | null => RISKY.find(([re]) => re.test(command))?.[1] ?? null;

export function CommandCard({ text, agent, block }: { text: string; agent?: string; block?: BlockRef }) {
  const path = useAgentDetail(agent ?? '').data?.worktree?.path;
  const folder = agent && path ? homeShort(path) : null;
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);
  const copy = () => void navigator.clipboard?.writeText(text).then(() => setCopied(true));
  const risk = riskOf(text);
  const ws = useWorkspaceIfAny();
  // The name the Computers page gives this machine.
  const host = useRead('connection.get', {}).data?.listener?.tailscale_host?.split('.')[0] ?? 'this computer';
  const { mutateAsync: openShell } = useWrite('shell.open', agent ?? '');
  const [asking, setAsking] = useState(false);
  const run = block?.run;
  const now = useNow(run?.state === 'running' ? 1000 : 60_000);
  // The terminal opens in the pane beside the chat, named after the command.
  const show = (handle: string) =>
    ws?.apply((w) => {
      const params = { handle, title: text.trim().split('\n')[0]!.slice(0, 32) };
      const groups = groupsOf(w.root);
      const chat = groups.find((g) => g.tabs.includes(CHAT)) ?? groups[0]!;
      const beside = groups.find((g) => g.id !== chat.id);
      if (beside) return openTab(w, 'shell', params, beside.id);
      const split = splitPane(w, chat.id);
      const fresh = split && groupsOf(split.root).find((g) => g.id === split.active)?.activeTab;
      return split && fresh ? fillTab(split, fresh, 'shell', params) : openTab(w, 'shell', params);
    });
  const start = async () => {
    setAsking(false);
    if (!agent || !block) return;
    try {
      show((await openShell({ agent, command: text, block: `${block.prefix ?? ''}${block.message}:${block.index}` })).terminal);
    } catch {
      // useWrite has reported it
    }
  };
  return (
    <div className="command-card my-1 min-w-0 rounded-md border border-line bg-panel first:mt-0 last:mb-0" data-kind="command">
      <div className="flex min-h-7 items-center justify-between gap-2 pr-1 pl-2">
        <span className="muted min-w-0 truncate text-meta">{folder}</span>
        <span className="flex flex-none items-center gap-1">
          {copied ? (
            <span className="muted text-meta" role="status">
              Copied
            </span>
          ) : null}
          <Button variant="ghost" size="sm" icon={copied ? 'done' : 'copy'} label="Copy" onClick={copy} />
          {agent && block && ws ? (
            <Button
              variant="ghost"
              size="sm"
              icon="play"
              label={`Run in ${folder ?? 'the worktree'}`}
              className={risk ? '!text-bad' : undefined}
              onClick={() => setAsking(true)}
            />
          ) : null}
        </span>
      </div>
      <pre className="m-0 overflow-x-auto px-2 pb-2 font-mono text-[0.92em] whitespace-pre">
        <code>{text}</code>
      </pre>
      {asking ? (
        <div
          className="flex w-0 min-w-full flex-wrap items-center gap-2 px-2 pb-2 text-meta"
          role="alertdialog"
          aria-label={`Run in ${folder ?? 'the worktree'}?`}
        >
          <span className="min-w-0 flex-[1_1_12rem] [overflow-wrap:anywhere]">{`Run in ${folder ?? 'the worktree'}? It runs on ${host} as you.`}</span>
          <Button size="sm" label="Cancel" onClick={() => setAsking(false)} />
          <Button size="sm" variant="solid" label="Run" className={risk ? '!bg-bad' : undefined} onClick={() => void start()} />
        </div>
      ) : null}
      {run ? (
        <p className="m-0 flex items-center gap-2 px-2 pb-2 text-meta" data-run={run.state}>
          <span className={run.state === 'failed' ? 'text-bad' : 'muted'}>{runLine(run, now)}</span>
          {ws && run.terminal && run.state !== 'stopped' ? (
            <Button size="sm" label={run.state === 'running' ? 'Open' : 'Open output'} onClick={() => show(run.terminal)} />
          ) : null}
        </p>
      ) : null}
      {risk ? <p className="m-0 px-2 pb-2 text-meta text-bad">{risk}</p> : null}
    </div>
  );
}
