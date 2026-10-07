// The Home settings page (#/settings), founder only: the default cap, idle close and three switches through settings.set, each field saving on its
// own (authored: it shows at once and goes back with its own note when refused), and the standing answers.
import { useEffect, useState } from 'react';
import { INVALID, minutesOf, parseCap, parseMinutes } from '@handline/app-core';
import type { SettingsChanges } from '@handline/client';
import { classify, sentence, useServes, useSettings, useWrite } from '@handline/app-data';
import { Answers } from './Answers.tsx';
import { useOff } from './useOff.ts';
import { View } from './View.tsx';

function Note({ error, pending, bad }: { error: unknown; pending: boolean; bad: string | null }) {
  const text = bad ?? (error ? `not saved: ${sentence(classify(error))}` : null);
  return (
    <>
      {pending ? (
        <span className="muted field-note" role="status">
          <i>○ saving…</i>
        </span>
      ) : null}
      {text ? (
        <span className="field-note bad" role="alert">
          {text}
        </span>
      ) : null}
    </>
  );
}

function NumberField(props: {
  label: string;
  hint: string;
  current: string;
  parse: (t: string) => number | null | typeof INVALID;
  change: (v: number | null) => SettingsChanges;
  allowed: string;
  placeholder?: string;
}) {
  const save = useWrite('settings.set');
  const off = useOff('settings.set');
  const { current } = props;
  const [text, setText] = useState(current);
  const [bad, setBad] = useState<string | null>(null);
  const [focus, setFocus] = useState(false);
  useEffect(() => {
    if (!focus) setText(current);
  }, [current, focus]);
  // A refusal puts the field back even when the cache went to the new value and back inside one render.
  const failed = save.isError;
  useEffect(() => {
    if (failed) setText(current);
  }, [failed, current]);
  const commit = () => {
    const v = props.parse(text);
    if (v === INVALID) return setBad(`${props.allowed}`);
    setBad(null);
    if (text.trim() !== props.current.trim()) save.mutate({ changes: props.change(v) });
  };
  return (
    <label className="setting" data-setting={props.label}>
      <span className="setting-label">
        {props.label} <span className="muted">{props.hint}</span>
      </span>
      <input
        inputMode="numeric"
        value={text}
        placeholder={props.placeholder}
        disabled={!!off}
        title={off}
        aria-invalid={bad ? true : undefined}
        onFocus={() => setFocus(true)}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          setFocus(false);
          commit();
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit();
          if (e.key === 'Escape') {
            setText(props.current);
            setBad(null);
          }
        }}
      />
      <Note error={save.error} pending={save.isPending} bad={bad} />
    </label>
  );
}

function Switch(props: { label: string; hint: string; on: boolean; change: (on: boolean) => SettingsChanges }) {
  const save = useWrite('settings.set');
  const off = useOff('settings.set');
  return (
    <label className="setting check" data-setting={props.label}>
      <input
        type="checkbox"
        checked={props.on}
        disabled={!!off}
        title={off}
        onChange={(e) => save.mutate({ changes: props.change(e.target.checked) })}
      />
      <span className="setting-label">
        {props.label} <span className="muted">{props.hint}</span>
      </span>
      <Note error={save.error} pending={save.isPending} bad={null} />
    </label>
  );
}

export function SettingsPage() {
  const q = useSettings();
  const { served } = useServes('settings.get');
  const v = q.data?.values;
  return (
    <section className="settings" aria-label="Settings">
      <header className="inbox-top">
        <h2>Settings</h2>
      </header>
      <p className="settings-phone muted">Open this on a computer.</p>
      <div className="settings-body">
        {!served ? (
          <p className="muted pad">This daemon does not serve settings, or they are for the founder only.</p>
        ) : (
          <View q={q} served shape="settings" what="settings">
            {v ? (
              <>
                <h3>Concurrency</h3>
                <NumberField
                  label="Default cap"
                  hint="agents working at once on an account; an account with its own cap keeps it"
                  current={v.active_cap === undefined ? '' : String(v.active_cap)}
                  parse={parseCap}
                  change={(n) => ({ active_cap: n })}
                  allowed="a whole number from 1, or empty for the default"
                />
                <h3>Closing windows</h3>
                <NumberField
                  label="Idle close"
                  hint="minutes of idle before a session closes; 0 is never"
                  current={minutesOf(v.park_timeout_s)}
                  parse={parseMinutes}
                  change={(s) => ({ park_timeout_s: s })}
                  allowed="whole minutes, 0 for never, or empty for the default"
                />
                <h3>Automation</h3>
                <Switch
                  label="Handoff note"
                  hint="agents write a note for the next session before a close or move"
                  on={!!v.handoff_note}
                  change={(on) => ({ handoff_note: on })}
                />
                <Switch
                  label="Auto-accept"
                  hint="answer the questions in the list below by itself"
                  on={!!v.auto_accept_dialogs}
                  change={(on) => ({ auto_accept_dialogs: on })}
                />
                <Switch
                  label="Ignore cache expiry"
                  hint="resume the old session even after its cache went cold"
                  on={!!v.ignore_cache_expiry}
                  change={(on) => ({ ignore_cache_expiry: on })}
                />
                <Answers />
              </>
            ) : null}
          </View>
        )}
      </div>
    </section>
  );
}
