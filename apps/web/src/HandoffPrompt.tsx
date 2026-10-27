// The handoff prompt on the Settings page: the text the agent is asked before it closes, with the built-in one shown in the box, a way back to it,
// what the agent reads of it, and a warning when agents set their own. Blank, and the built-in itself, are the built-in.
import { useEffect, useState } from 'react';
import { ownTextWarning, promptPreview, promptProblem, promptToSave, type HandoffSettings } from '@handline/app-core';
import { useOff, useWrite } from '@handline/app-data';
import { Button, Textarea } from '#src/ui/index.ts';
import { Note, Row } from '#src/SettingsRows.tsx';

export function HandoffPrompt({ settings }: { settings: HandoffSettings }) {
  const save = useWrite('settings.set');
  const off = useOff('settings.set');
  const builtin = settings.handoff_prompt_default;
  const current = settings.values.handoff_prompt ?? '';
  const [text, setText] = useState(current);
  const [focus, setFocus] = useState(false);
  const [showOwn, setShowOwn] = useState(false);
  useEffect(() => {
    if (!focus) setText(current);
  }, [current, focus]);
  useEffect(() => {
    if (save.isError) setText(current);
  }, [save.isError, current]);
  const bad = promptProblem(text);
  const isBuiltin = builtin !== undefined && current === builtin;
  const own = settings.handoff_prompt_own ?? [];
  const warning = ownTextWarning(own);
  const commit = () => {
    if (bad) return;
    const to = promptToSave(text, builtin);
    if (to !== (isBuiltin ? '' : current)) save.mutate({ changes: { handoff_prompt: to } });
  };
  return (
    <Row
      wide
      label="What the agent is asked"
      hint="{path} is the note's file and {max} its size limit. Without {path}, the file is named on a line of its own."
      note={<Note error={save.error} pending={save.isPending} bad={bad} />}
    >
      <div className="handoff-prompt flex flex-col gap-2">
        <Textarea
          aria-label="What the agent is asked"
          rows={8}
          value={text}
          disabled={!!off}
          title={off}
          aria-invalid={bad ? true : undefined}
          onFocus={() => setFocus(true)}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => {
            setFocus(false);
            commit();
          }}
        />
        <div className="flex flex-wrap items-center gap-2">
          {isBuiltin ? <span className="rounded-ctl bg-raised px-1.5 text-hint text-ink-2">Built-in</span> : null}
          <Button
            variant="outline"
            size="sm"
            label="Reset to built-in"
            disabled={!!off || (builtin === undefined ? !current : isBuiltin) || save.isPending}
            onClick={() => save.mutate({ changes: { handoff_prompt: '' } })}
          />
        </div>
        <div className="rounded-ctl bg-raised px-3 py-2 text-hint text-ink-2">
          <p className="m-0 mb-1 font-medium text-ink">The agent reads:</p>
          <p data-reads className="m-0 whitespace-pre-wrap">
            {promptPreview(bad ? current : text || builtin || '')}
          </p>
        </div>
        {warning ? (
          <p className="m-0 flex flex-wrap items-center gap-x-2 text-hint text-wait" role="status">
            {warning}
            <Button variant="ghost" size="sm" label={showOwn ? 'Hide' : 'Show'} onClick={() => setShowOwn(!showOwn)} />
            {showOwn ? <span className="text-ink-2">{own.join(', ')}</span> : null}
          </p>
        ) : null}
      </div>
    </Row>
  );
}
