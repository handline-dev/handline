// A .md or .json file in the agent's worktree: shown as text, edited in place and saved against the etag the read gave (file.write).
import { useState } from 'react';
import type { AgentDetail } from '@handline/client';
import { EDITABLE, shown, useFileEditor, useWorktrees } from '@handline/app-data';
import { Button, Icon, Input, MenuButton, MenuItem, Textarea } from './ui/index.ts';
import { Markdown } from './Markdown.tsx';
import { shell } from './shell.ts';
import { View } from './View.tsx';

export function FileTab({ agent, detail, path, onPath }: { agent: string; detail: AgentDetail; path: string; onPath: (p: string) => void }) {
  const [typed, setTyped] = useState(path);
  const changed = (useWorktrees().data?.worktrees.find((w) => w.path === detail.worktree?.path)?.changed ?? []).filter((c) => EDITABLE.test(c.path));
  const { q, served, saving, draft, setDraft, note, submit, edit, discard, editable } = useFileEditor(agent, path);
  const open = shell('openPath');
  const d = q.data;
  const go = (p: string) => {
    const next = p.trim().replace(/^\.?\//, '');
    setTyped(next);
    if (next) onPath(next);
  };
  return (
    <section className="file-tab flex h-full min-h-0 flex-col" aria-label={`File of ${agent}`}>
      <form
        className="flex flex-none items-center gap-2 border-b border-line px-3 py-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          go(typed);
        }}
      >
        <Icon name="reference" size={14} aria-hidden />
        <Input className="mono min-w-0 flex-1" aria-label="File path" placeholder="docs/notes.md" value={typed} onChange={(e) => setTyped(e.target.value)} list={`file-${agent}`} />
        <datalist id={`file-${agent}`}>
          {changed.map((c) => (
            <option key={c.path} value={c.path} />
          ))}
        </datalist>
        <Button type="submit" size="sm" variant="outline" label="Open file">
          Open
        </Button>
        {path ? (
          <Button icon="refresh" size="sm" variant="outline" label="Reload" onClick={() => void q.refetch().then(() => setDraft(null))}>
            Reload
          </Button>
        ) : null}
        {editable && draft === null ? (
          <Button size="sm" variant="outline" label="Edit" onClick={edit}>
            Edit
          </Button>
        ) : null}
        {draft !== null ? (
          <>
            <Button size="sm" label="Save" disabled={saving} onClick={() => void submit()}>
              Save
            </Button>
            <Button size="sm" variant="outline" label="Discard" onClick={discard}>
              Discard
            </Button>
          </>
        ) : null}
        {path && open ? (
          <MenuButton icon="open" size="sm" variant="outline" label="Open outside" name="Open outside" trigger="Open outside">
            <MenuItem icon="open" onSelect={() => void open.openPath!({ path: `${detail.worktree?.path ?? ''}/${path}`, agent })}>
              Show in Finder
            </MenuItem>
          </MenuButton>
        ) : null}
      </form>
      {note ? (
        <p className="m-0 border-b border-line px-3 py-1.5 text-body text-bad" role="alert">
          {note}
        </p>
      ) : null}
      {!path ? (
        <div className="flex flex-col gap-1 p-4">
          <p className="muted m-0">Type the path of a Markdown or JSON file in the worktree.</p>
          {changed.map((c) => (
            <button key={c.path} className="mono w-fit border-0 bg-transparent p-0 text-left text-body text-link" onClick={() => go(c.path)}>
              {c.path}
            </button>
          ))}
        </div>
      ) : (
        <View q={q} served={served} shape="worktrees" what="the file">
          {d ? (
            draft !== null ? (
              <Textarea className="mono min-h-0 flex-1 resize-none rounded-none border-0 p-3 text-[12px] leading-5" aria-label="File text" value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false} />
            ) : (
              <div className="min-h-0 flex-1 overflow-auto p-3">
                {/\.md$/i.test(path) ? <Markdown text={d.text} /> : <pre className="m-0 font-mono text-[12px] leading-5">{shown(path, d.text)}</pre>}
                {d.truncated ? <p className="muted">The file is cut here (over 1 MiB); it cannot be edited.</p> : null}
              </div>
            )
          ) : null}
        </View>
      )}
    </section>
  );
}
