// A fenced sh/bash/shell block in a message: the folder it belongs to, and Copy. Other fences stay plain code (Markdown.tsx).
import { useEffect, useState } from 'react';
import { useAgentDetail } from '@handline/app-data';
import { Button } from '#src/ui/index.ts';

export const isShell = (lang: string) => /^(sh|bash|shell|zsh)$/i.test(lang);

const RISKY: [RegExp, string][] = [
  [/\brm\s+(-\w*r\w*f|-\w*f\w*r)\b|\brm\s+-r\b.*\s-f\b/, 'Deletes files for good.'],
  [/\bsudo\b/, 'Runs with administrator rights.'],
  [/\bgit\s+push\b.*(--force\b|\s-f\b)/, 'Overwrites history on the remote.'],
  [/\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b/, 'Runs a downloaded script unread.'],
];

/** Why a command is risky, in a plain line; null when it is not one of the known risky shapes. */
export const riskOf = (command: string): string | null => RISKY.find(([re]) => re.test(command))?.[1] ?? null;

export function CommandCard({ text, agent }: { text: string; agent?: string }) {
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
        </span>
      </div>
      <pre className="m-0 overflow-x-auto px-2 pb-2 font-mono text-[0.92em] whitespace-pre">
        <code>{text}</code>
      </pre>
      {risk ? <p className="m-0 px-2 pb-2 text-meta text-bad">{risk}</p> : null}
    </div>
  );
}
