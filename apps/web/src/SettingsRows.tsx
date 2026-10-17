// The rows of the Home settings page: a title, one grey line and the control, each saving through settings.set on its own.
import { useEffect, useState, type ReactNode } from 'react';
import { INVALID } from '@handline/app-core';
import type { SettingsChanges } from '@handline/client';
import { classify, sentence, useWrite } from '@handline/app-data';
import { Field, Icon, RowControl, RowHint, RowText, RowTitle, SettingsRow, Switch, NOTE } from './ui/index.ts';
import { useOff } from './useOff.ts';

/** The note under a row: saving (an icon and a word), or why the value is not saved (an icon, never colour alone). */
export function Note({ error, pending, bad }: { error: unknown; pending: boolean; bad: string | null }) {
  const text = bad ?? (error ? `not saved: ${sentence(classify(error))}` : null);
  return (
    <>
      {pending ? (
        <p className={NOTE} role="status">
          <Icon name="sending" size={14} />
          saving
        </p>
      ) : null}
      {text ? (
        <p className={`${NOTE} text-bad`} role="alert">
          <Icon name="failed" size={14} label="Not saved" />
          {text}
        </p>
      ) : null}
    </>
  );
}

export function Row(props: { label: string; hint: string; more?: string; children: ReactNode; note?: ReactNode }) {
  return (
    <SettingsRow data-setting={props.label}>
      <RowText title={props.more}>
        <RowTitle>{props.label}</RowTitle>
        <RowHint>{props.hint}</RowHint>
      </RowText>
      <RowControl>{props.children}</RowControl>
      {props.note}
    </SettingsRow>
  );
}

export function NumberRow(props: {
  label: string;
  hint: string;
  more?: string;
  unit: string;
  current: string;
  parse: (t: string) => number | null | typeof INVALID;
  change: (v: number | null) => SettingsChanges;
  allowed: string;
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
    <Row label={props.label} hint={props.hint} more={props.more} note={<Note error={save.error} pending={save.isPending} bad={bad} />}>
      <Field
        label={props.label}
        unit={props.unit}
        inputMode="numeric"
        value={text}
        disabled={!!off}
        title={off}
        tone={bad ? 'bad' : undefined}
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
    </Row>
  );
}

export function SwitchRow(props: { label: string; hint: string; on: boolean; change: (on: boolean) => SettingsChanges }) {
  const save = useWrite('settings.set');
  const off = useOff('settings.set');
  return (
    <Row label={props.label} hint={props.hint} note={<Note error={save.error} pending={save.isPending} bad={null} />}>
      <span title={off}>
        <Switch label={props.label} on={props.on} disabled={!!off} onChange={(on) => save.mutate({ changes: props.change(on) })} />
      </span>
    </Row>
  );
}
