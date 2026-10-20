// The state of one agent setting field, for the PC and the phone: the draft, the reason a refusal gave, a danger switch waiting for its
// confirmation, and the write that saves a value (agent.set, an authored write).
import { useState } from 'react';
import { DANGER, mayChange, problemWith, type Field } from '@handline/app-core';
import { classify, sentence } from '#src/errors.ts';
import { useSettings } from '#src/read.ts';
import { usePending, useWrite } from '#src/write.ts';

export function useFieldRow(props: { agent: string; f: Field; stored: string | undefined; editable: boolean; role: string | undefined }) {
  const { agent, f, role } = props;
  const stored = props.stored ?? '';
  const write = useWrite('agent.set', agent);
  const pending = usePending('agent.set').some((v) => v.agent === agent && f.key in v.values);
  const global = useSettings().data?.values?.handoff_note;
  const [draft, setDraft] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [ask, setAsk] = useState<string | null>(null); // a danger switch waiting for its confirmation
  const allowed = mayChange(f.key, role);
  const off = !props.editable || !allowed;
  const value = draft ?? stored;
  const send = (v: string) => {
    setDraft(null);
    setNote('');
    if (v === stored) return;
    write.mutateAsync({ agent, values: { [f.key]: v } }).catch((e) => setNote(sentence(classify(e))));
  };
  const commit = (v: string) => {
    const problem = problemWith(f, v);
    if (problem) return setNote(`${f.label}: ${problem}`);
    send(f.kind === 'number' ? String(Number(v)) : v.trim());
  };
  const choose = (v: string) => {
    if (f.kind === 'switch' && DANGER[f.key]?.to === v) return setAsk(v);
    send(v);
  };
  const options = f.kind === 'select' ? (f.options!.includes(value) ? f.options! : [value, ...f.options!]) : [];
  return { stored, pending, global, draft, setDraft, note, setNote, ask, setAsk, allowed, off, value, send, commit, choose, options };
}
