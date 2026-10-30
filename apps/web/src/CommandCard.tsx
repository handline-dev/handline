// A fenced sh/bash/shell block in a message: the folder it belongs to, and Copy. Other fences stay plain code (Markdown.tsx).
import { useEffect, useState } from 'react';
import { useAgentDetail, useWrite } from '@handline/app-data';
import type { CommandRun } from '@handline/client';
import { openTab } from '@handline/app-core';
import { useNow } from '#src/Gauge.tsx';
import { useWorkspace } from '#src/Workspace.tsx';
import { Button } from '#src/ui/index.ts';

/** Which command block of which message a card is, and its last run. */
export interface BlockRef {
  message: number;
  index: number;
  run?: CommandRun;
}

const runLine = (r: CommandRun, now: number) => {
  const s = Math.max(0, Math.round((now - Date.parse(r.started_at)) / 1000));
  if (r.state === 'running') return `Running · ${s}s`;
  if (r.state === 'stopped') return 'Stopped';
  return `${r.state === 'done' ? 'Done' : 'Failed'} · exit ${r.exit ?? 0} · ${r.seconds ?? 0}s`;
};

/** The computer the daemon runs on, as the page knows it: this one when the page is served from it. */
const hostName = () => (/^(localhost|127\.|\[?::1)/.test(location.hostname) ? 'this computer' : location.hostname);

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
  const folder = agent && path ? path.split('/').filter(Boolean).at(-1) : null;
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);
  const copy = () => void navigator.clipboard?.writeText(text).then(() => setCopied(true));
  const risk = riskOf(text);
  const { apply } = useWorkspace();
  const { mutateAsync: openShell } = useWrite('shell.open', agent ?? '');
  const [asking, setAsking] = useState(false);
  const run = block?.run;
  const now = useNow(run?.state === 'running' ? 1000 : 60_000);
  const show = (handle: string) => apply((w) => openTab(w, 'shell', { handle }));
  const start = async () => {
    setAsking(false);
    if (!agent || !block) return;
    try {
      show((await openShell({ agent, command: text, block: `${block.message}:${block.index}` })).terminal);
    } catch {
      // useWrite has reported it
    }
  };
  return (
    <div className="command-card my-1 rounded-md bg-black/10 first:mt-0 last:mb-0" data-kind="command">
      <div className="flex min-h-7 items-center justify-between gap-2 pr-1 pl-2">
        <span className="muted min-w-0 truncate text-meta">{folder}</span>
        <span className="flex flex-none items-center gap-1">
          {copied ? (
            <span className="muted text-meta" role="status">
              Copied
            </span>
          ) : null}
          <Button variant="ghost" size="sm" icon={copied ? 'done' : 'copy'} label="Copy" onClick={copy} />
          {agent && block ? (
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
        <div className="flex items-center gap-2 px-2 pb-2 text-meta" role="alertdialog" aria-label={`Run in ${folder ?? 'the worktree'}?`}>
          <span className="min-w-0 flex-1">{`Run in ${folder ?? 'the worktree'}? It runs on ${hostName()} as you.`}</span>
          <Button size="sm" label="Cancel" onClick={() => setAsking(false)} />
          <Button size="sm" variant="solid" label="Run" onClick={() => void start()} />
        </div>
      ) : null}
      {run ? (
        <p className="m-0 flex items-center gap-2 px-2 pb-2 text-meta" data-run={run.state}>
          <span className={run.state === 'failed' ? 'text-bad' : 'muted'}>{runLine(run, now)}</span>
          {run.state === 'running' ? <Button size="sm" label="Open" onClick={() => show(run.terminal)} /> : null}
        </p>
      ) : null}
      {risk ? <p className="m-0 px-2 pb-2 text-meta text-bad">{risk}</p> : null}
    </div>
  );
}
