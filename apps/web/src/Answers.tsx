// The standing answers: what the daemon answers by itself (a trusted folder, a vendor's dialog and the option to choose). One row each with a trash
// icon and an Add row at the end (docs/design-gaps.md G82). Add and remove are authored: the row changes at once and comes back if the daemon refuses.
import { useState } from 'react';
import { useAnswers, useServes, useWrite } from '@handline/app-data';
import { Button, Field, Icon, RowTitle, SectionHead, Select, SettingsCard, SettingsRow } from './ui/index.ts';
import { useOff } from './useOff.ts';
import { View } from './View.tsx';

const KINDS = [
  { value: 'trust', label: 'Trust a folder' },
  { value: 'dialog', label: 'Answer a dialog' },
];

function AddForm({ off, onDone }: { off: string | undefined; onDone: () => void }) {
  const add = useWrite('answer.add');
  const [kind, setKind] = useState<'trust' | 'dialog'>('trust');
  const [f, setF] = useState({ folder: '', vendor: '', title: '', label: '' });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const p = kind === 'trust' ? { folder: f.folder.trim() } : { vendor: f.vendor.trim(), title: f.title.trim(), label: f.label.trim() };
    if (Object.values(p).some((x) => !x)) return; // a form with an empty field sends nothing
    add.mutate(p);
    setF({ folder: '', vendor: '', title: '', label: '' });
    onDone();
  };
  return (
    <form className="answer-form flex flex-wrap items-end gap-2" onSubmit={submit} aria-label="Add a standing answer">
      <Select label="Kind" options={KINDS} value={kind} onChange={(e) => setKind(e.target.value as 'trust' | 'dialog')} />
      {kind === 'trust' ? (
        <Field label="Folder" placeholder="/path/to/folder" value={f.folder} onChange={set('folder')} />
      ) : (
        <>
          <Field label="Vendor" placeholder="claude" value={f.vendor} onChange={set('vendor')} />
          <Field label="Prompt title" placeholder="Do you trust the files" value={f.title} onChange={set('title')} />
          <Field label="Option" placeholder="Yes, I trust" value={f.label} onChange={set('label')} />
        </>
      )}
      <span className="answer-form-actions flex gap-1.5">
        <Button variant="solid" type="submit" label="Add" disabled={!!off} title={off} />
        <Button label="Cancel" onClick={onDone} />
      </span>
    </form>
  );
}

export function Answers() {
  const q = useAnswers();
  const { served } = useServes('answers.list');
  const remove = useWrite('answer.remove');
  const off = useOff('answer.add');
  const offRemove = useOff('answer.remove');
  const [adding, setAdding] = useState(false);
  const rows = q.data?.answers ?? [];
  return (
    <>
      <SectionHead>Standing answers</SectionHead>
      <View
        q={q}
        served={served}
        shape="settings"
        what="standing answers"
        empty={!rows.length && 'none - you are asked when a dialog shows'}
      >
        <SettingsCard as="ul" className="answers">
          {rows.map((a) => (
            <SettingsRow answer as="li" key={`${a.kind}\0${a.key.join('\0')}`} data-answer={a.kind}>
              <Icon
                name={a.kind === 'trust' ? 'permission' : 'prompt'}
                size={16}
                label={a.kind === 'trust' ? 'Trusted folder' : 'Dialog'}
              />
              <RowTitle plain className="answer-text [overflow-wrap:anywhere]">
                {a.text}
              </RowTitle>
              <Button
                icon="dismiss"
                label={`Remove ${a.text}`}
                disabled={!!offRemove}
                title={offRemove}
                onClick={() => remove.mutate({ kind: a.kind, key: a.key })}
              />
            </SettingsRow>
          ))}
        </SettingsCard>
      </View>
      {served ? (
        <div className="answers-actions mt-2">
          {adding ? (
            <AddForm off={off} onDone={() => setAdding(false)} />
          ) : (
            <Button icon="plus" label="Add" disabled={!!off} title={off} onClick={() => setAdding(true)}>
              Add
            </Button>
          )}
        </div>
      ) : null}
    </>
  );
}
