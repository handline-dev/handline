// An agent's settings page (the bridge's agent_settings.py): one column of fields that save one by one through agent.set, an authored write.
// A text field saves on blur or Enter, a select and a switch on change; a refusal puts the value back and the field's own note says why.
import { useState } from 'react';
import { DANGER, FIELDS, mayChange, problemWith, type Field } from '@handline/app-core';
import type { AgentDetail } from '@handline/client';
import { useStore } from '@handline/app-react';
import { classify, sentence, useAgentDetail, usePending, useServes, useSettings, useWrite } from '@handline/app-data';
import { goAgent } from './route.ts';
import { Schedules } from './Schedules.tsx';
import { View } from './View.tsx';

const SECTIONS: { id: Field['section']; title: string }[] = [
  { id: 'model', title: 'model' },
  { id: 'work', title: 'work' },
  { id: 'session', title: 'session' },
  { id: 'browser', title: 'browser' },
];

export function SettingsView({ agent }: { agent: string }) {
  const q = useAgentDetail(agent);
  const { served } = useServes('agent.set');
  const store = useStore();
  const role = store.client.hello?.role;
  const d: AgentDetail | undefined = q.data;
  const editable = served && (role === 'founder' || role === 'agent'); // agent.set is for the founder and an agent on itself
  return (
    <section className="settings" aria-label={`${agent} settings`}>
      <header className="settings-head">
        <button className="btn" onClick={() => goAgent(agent)}>
          ← {agent}
        </button>
        <h2>Settings</h2>
      </header>
      {served ? null : <p className="muted pad">This daemon does not edit agent settings.</p>}
      {served && !editable ? <p className="muted pad">Only the founder edits agent settings.</p> : null}
      <View q={q} served shape="settings" what={`${agent} settings`}>
        {d ? (
          <div className="settings-body">
            <h3 className="shead">agent</h3>
            <div className="frow">
              <span className="flabel">session runs on</span>
              <span>{d.account ?? 'not chosen yet'}</span>
            </div>
            {SECTIONS.map((s) => (
              <div key={s.id}>
                <h3 className="shead">{s.title}</h3>
                {FIELDS.filter((f) => f.section === s.id).map((f) => (
                  <FieldRow key={f.key} agent={agent} f={f} stored={d.settings?.[f.key]} editable={editable} role={role} />
                ))}
              </div>
            ))}
            <Schedules agent={agent} role={role} />
          </div>
        ) : null}
      </View>
    </section>
  );
}

function FieldRow(props: { agent: string; f: Field; stored: string | undefined; editable: boolean; role: string | undefined }) {
  const { agent, f, role } = props;
  const stored = props.stored ?? '';
  const write = useWrite('agent.set', agent);
  const pending = usePending('agent.set').some((v) => v.agent === agent && f.key in v.values);
  const global = useSettings().data?.values?.handoff_note;
  const [draft, setDraft] = useState<string | null>(null);
  const [base, setBase] = useState('');
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
  const edit = (v: string) => {
    if (draft === null) setBase(stored);
    setDraft(v);
    setNote('');
  };
  const choose = (v: string) => {
    if (f.kind === 'switch' && DANGER[f.key]?.to === v) return setAsk(v);
    send(v);
  };
  const label = (o: string) =>
    f.key === 'handoff_note' && o === 'default' ? `follow the global switch${global === undefined ? '' : global ? ' (on)' : ' (off)'}` : o;
  const control = () => {
    if (f.kind === 'select')
      return (
        <select id={`s-${f.key}`} value={value} disabled={off} onChange={(e) => choose(e.target.value)}>
          {(f.options!.includes(value) ? f.options! : [value, ...f.options!]).map((o) => (
            <option key={o} value={o}>
              {label(o)}
            </option>
          ))}
        </select>
      );
    if (f.kind === 'switch')
      return (
        <input
          id={`s-${f.key}`}
          type="checkbox"
          role="switch"
          checked={stored === 'on'}
          disabled={off}
          onChange={(e) => choose(e.target.checked ? 'on' : 'off')}
        />
      );
    if (f.kind === 'sites') return <Sites value={stored} off={off} save={send} />;
    return (
      <>
        <input
          id={`s-${f.key}`}
          type="text"
          list={f.kind === 'model' ? 's-models' : undefined}
          inputMode={f.kind === 'number' ? 'numeric' : undefined}
          value={value}
          disabled={off}
          onFocus={() => setBase(stored)}
          onChange={(e) => edit(e.target.value)}
          onBlur={() => draft !== null && commit(draft)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit(value);
            if (e.key === 'Escape') {
              setDraft(null);
              setNote('');
            }
          }}
        />
        {f.kind === 'model' ? (
          <datalist id="s-models">
            {f.options!.map((o) => (
              <option key={o} value={o} />
            ))}
          </datalist>
        ) : null}
        {f.unit ? <span className="muted"> {f.unit}</span> : null}
      </>
    );
  };
  const danger = ask ? DANGER[f.key] : undefined;
  return (
    <div className="frow" data-field={f.key}>
      <label className="flabel" htmlFor={`s-${f.key}`}>
        {pending ? <span title="saving">○ </span> : null}
        {f.label}
        {allowed ? null : <span title="founder only"> 🔒</span>}
      </label>
      <span className="fcontrol">{control()}</span>
      {f.note && !note ? <span className="muted fnote">{f.note}</span> : null}
      {draft !== null && stored !== base ? (
        <span className="fnote warn">
          changed elsewhere: {stored || 'empty'}{' '}
          <button className="btn" onClick={() => (setDraft(null), setNote(''))}>
            Use
          </button>
        </span>
      ) : null}
      {note ? (
        <span className="fnote bad" role="alert">
          {note}
        </span>
      ) : null}
      {danger ? (
        <div className="verb-sheet danger" role="alertdialog" aria-label={danger.title}>
          <strong>{danger.title}</strong>
          <span className="muted">{danger.text}</span>
          <span className="verb-sheet-actions">
            <button
              className="btn primary"
              onClick={() => {
                const v = ask!;
                setAsk(null);
                send(v);
              }}
            >
              {danger.go}
            </button>
            <button className="btn" onClick={() => setAsk(null)}>
              Cancel
            </button>
          </span>
        </div>
      ) : null}
    </div>
  );
}

/** The allowed sites: a space-separated string in the contract, a list with add and remove here. */
function Sites({ value, off, save }: { value: string; off: boolean; save: (v: string) => void }) {
  const sites = value.split(/\s+/).filter(Boolean);
  const [text, setText] = useState('');
  const add = () => {
    const site = text.trim();
    if (!site || sites.includes(site)) return setText('');
    setText('');
    save([...sites, site].join(' '));
  };
  return (
    <span className="sites">
      {sites.map((s) => (
        <span key={s} className="chip">
          {s}{' '}
          <button className="btn" disabled={off} aria-label={`Remove ${s}`} onClick={() => save(sites.filter((x) => x !== s).join(' '))}>
            ✕
          </button>
        </span>
      ))}
      {off ? null : (
        <input
          type="text"
          aria-label="Add a site"
          placeholder="https://…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && add()}
          onBlur={add}
        />
      )}
    </span>
  );
}
