// The handoff prompt on the Settings page: the text the agent is asked before it closes, with the built-in one shown in the box, a way back to it,
// what the agent reads of it, and a warning when agents set their own. Blank, and the built-in itself, are the built-in.
import { useEffect, useState } from 'react';
import { ownTextWarning, promptPreview, promptProblem, promptToSave, type HandoffSettings } from '@handline/app-core';
import { useOff, useWrite } from '@handline/app-data';
import { Button, Icon, Textarea } from '#src/ui/index.ts';
import { Note, Row } from '#src/SettingsRows.tsx';

export function HandoffPrompt({ settings }: { settings: HandoffSettings }) {
  const save = useWrite('settings.set');
  const off = useOff('settings.set');
  const builtin = settings.handoff_prompt_default;
  const current = settings.values.handoff_prompt ?? '';
  const [text, setText] = useState(current);
  const [focus, setFocus] = useState(false);
  const [showOwn, setShowOwn] = useState(false);
  const [showReads, setShowReads] = useState(false);
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
  const to = promptToSave(text, builtin);
  const dirty = to !== (isBuiltin ? '' : current);
  const commit = () => {
    if (!bad && dirty) save.mutate({ changes: { handoff_prompt: to } });
  };
  const reset = () => {
    setText(builtin ?? '');
    if (current && !isBuiltin) save.mutate({ changes: { handoff_prompt: '' } });
  };
  const reads = promptPreview(bad ? current : text || builtin || '');
  return (
    <Row
      wide
      label="What the agent is asked"
      hint="{path} is the note's file and {max} its size limit. Without {path}, the file is named on a line of its own."
      note={<Note error={save.error} pending={save.isPending} bad={null} />}
    >
      <div className="handoff-prompt flex w-full min-w-0 flex-col gap-2">
        <Textarea
          aria-label="What the agent is asked"
          rows={4}
          className="max-h-64 w-full [field-sizing:content] aria-invalid:border-bad"
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
        {bad ? (
          <p className="m-0 flex items-center gap-1.5 text-xs text-bad" role="alert">
            <Icon name="failed" size={14} label="Not saved" />
            Not saved, the old text stays. {bad}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          {isBuiltin && !dirty ? <span className="rounded-sm bg-raised px-1.5 text-xs text-ink-2">Built-in</span> : null}
          <Button
            variant="outline"
            size="sm"
            label="Reset to built-in"
            disabled={!!off || (!dirty && (builtin === undefined ? !current : isBuiltin)) || save.isPending}
            onClick={reset}
          />
        </div>
        <div className="min-w-0 text-xs text-ink-2">
          <button
            type="button"
            aria-expanded={showReads}
            className="flex w-full min-w-0 cursor-pointer items-center gap-1.5 border-0 bg-transparent p-0 text-left text-xs text-ink-2"
            onClick={() => setShowReads(!showReads)}
          >
            <span className="flex shrink-0">
              <Icon
                name={showReads ? 'less' : 'more'}
                size={14}
                label={showReads ? 'Hide what the agent reads' : 'Show what the agent reads'}
              />
            </span>
            <span className="shrink-0 font-medium text-ink">What the agent reads</span>
            {showReads ? null : <span className="truncate">{reads.split('\n')[0]}</span>}
          </button>
          {showReads ? (
            <p data-reads className="m-0 mt-1 whitespace-pre-wrap rounded-sm bg-raised px-3 py-2">
              {reads}
            </p>
          ) : null}
        </div>
        {warning ? (
          <p className="m-0 flex flex-wrap items-center gap-x-2 text-xs text-wait" role="status">
            {warning}
            <button
              type="button"
              className="cursor-pointer border-0 bg-transparent p-0 text-xs text-ink-2 underline"
              onClick={() => setShowOwn(!showOwn)}
            >
              {showOwn ? 'Hide' : 'Show'}
            </button>
            {showOwn ? <span className="text-ink-2">{own.join(', ')}</span> : null}
          </p>
        ) : null}
      </div>
    </Row>
  );
}
