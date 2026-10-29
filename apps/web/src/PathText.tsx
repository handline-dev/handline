// Text with its paths made clickable when the desktop shell can open them (docs/electron-bridge.md §4.1). In a plain browser a path stays text.
// The call is made only from the click, never from rendering.
import { useState } from 'react';
import { branchCandidates, findNames, findPaths } from '@handline/app-core';
import { goAgent } from '#src/route.ts';
import { shell } from '#src/shell.ts';

/** `names` are the agents there are: a word that is one opens that agent. `whole` is the whole message, whose words may name a branch. */
export function PathText({ text, agent, names, whole }: { text: string; agent?: string; names?: ReadonlySet<string>; whole?: string }) {
  const [note, setNote] = useState('');
  const api = shell('openPath');
  const agents = names ? findNames(text, names) : [];
  const overlaps = (s: { start: number; end: number }) => agents.some((a) => s.start < a.end && a.start < s.end);
  const spans = [
    ...agents.map((a) => ({ start: a.start, end: a.end, path: a.name })),
    ...(api ? findPaths(text).filter((s) => !overlaps(s)) : []),
  ].sort((a, b) => a.start - b.start);
  if (!spans.length) return <>{text}</>;
  const parts: React.ReactNode[] = [];
  let at = 0;
  for (const s of spans) {
    parts.push(text.slice(at, s.start));
    const word = text.slice(s.start, s.end);
    if (agents.some((a) => a.start === s.start)) {
      parts.push(
        <button
          key={s.start}
          type="button"
          className="agent-link cursor-pointer border-0 bg-transparent p-0 font-mono text-acc-ink no-underline"
          title={`Open ${word}`}
          onClick={() => goAgent(word)}
        >
          {word}
        </button>,
      );
      at = s.end;
      continue;
    }
    parts.push(
      <button
        key={s.start}
        type="button"
        className="path cursor-pointer border-0 bg-transparent p-0 font-mono text-acc-ink no-underline"
        title="Open"
        onClick={() =>
          void api!.openPath!({ path: s.path, ...(agent ? { agent } : {}), branches: branchCandidates(whole ?? text) }).then((r) =>
            setNote(r.ok ? (r.how === 'revealed' ? 'shown in the folder, not opened' : '') : r.message),
          )
        }
      >
        {text.slice(s.start, s.end)}
      </button>,
    );
    at = s.end;
  }
  parts.push(text.slice(at));
  return (
    <>
      {parts}
      {note ? (
        <span className="muted path-note" role="status">
          {' '}
          {note}
        </span>
      ) : null}
    </>
  );
}
