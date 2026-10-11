// An agent's settings page (the bridge's agent_settings.py): one column of fields that save one by one through agent.set, an authored write.
// A text field saves on blur or Enter, a select and a switch on change; a refusal puts the value back and the field's own note says why.
import { useState } from 'react';
import { DANGER, FIELDS, mayChange, problemWith, type Field } from '@handline/app-core';
import type { AgentDetail } from '@handline/client';
import { useStore } from '@handline/app-react';
import { classify, sentence, useAgentDetail, usePending, useServes, useSettings, useWrite } from '@handline/app-data';
import { goAgent } from './route.ts';
import { Schedules } from './Schedules.tsx';
import { Button, Field as Input, Icon, Segmented, Select, Switch, type IconName } from './ui/index.ts';
import { View } from './View.tsx';
import { SHEAD, VERB_SHEET_ACTIONS, VERB_SHEET_DANGER } from './sheetStyle.ts';

const SECTIONS: { id: Field['section']; title: string; icon: IconName }[] = [
  { id: 'model', title: 'Model', icon: 'agent' },
  { id: 'work', title: 'Work', icon: 'tasks' },
  { id: 'session', title: 'Session', icon: 'session' },
  { id: 'browser', title: 'Browser', icon: 'open' },
];

/** The unit as a suffix inside the field; what follows its comma ("min, 0 = never") goes in the tooltip. */
const unitOf = (f: Field) => f.unit?.split(',')[0]?.trim();
const moreOf = (f: Field) =>
  [f.note, f.unit?.includes(',') ? f.unit.split(',').slice(1).join(',').trim() : undefined].filter(Boolean).join('; ');

export function SettingsView({ agent }: { agent: string }) {
  const q = useAgentDetail(agent);
  const { served } = useServes('agent.set');
  const store = useStore();
  const role = store.client.hello?.role;
  const d: AgentDetail | undefined = q.data;
  const editable = served && (role === 'founder' || role === 'agent'); // agent.set is for the founder and an agent on itself
  return (
    <section className="settings" aria-label={`${agent} settings`}>
      <header className="settings-head settings-agent">
        <Button className="back" icon="chevronRight" label={`Back to ${agent}`} onClick={() => goAgent(agent)}>
          {agent}
        </Button>
        <h2>Settings</h2>
        <p className="settings-lede">Each change saves as you make it.</p>
      </header>
      {served ? null : <p className="muted pad">This daemon does not edit agent settings.</p>}
      {served && !editable ? <p className="muted pad">Only the founder edits agent settings.</p> : null}
      <View q={q} served shape="settings" what={`${agent} settings`}>
        {d ? (
          <div className="settings-body settings-agent">
            <div className="scard">
              <div className="srow">
                <div className="srow-text">
                  <span className="srow-title">Session runs on</span>
                </div>
                <div className="srow-ctl">{d.account ?? 'not chosen yet'}</div>
              </div>
            </div>
            {SECTIONS.map((s) => (
              <section key={s.id}>
                <h3 className={SHEAD}>
                  <Icon name={s.icon} size={14} label={s.title} aria-hidden /> {s.title}
                </h3>
                <div className="scard">
                  {FIELDS.filter((f) => f.section === s.id).map((f) => (
                    <FieldRow key={f.key} agent={agent} f={f} stored={d.settings?.[f.key]} editable={editable} role={role} />
                  ))}
                </div>
              </section>
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
  // Two or three options sit in one segmented control, more in a select with a chevron.
  const options = f.kind === 'select' ? (f.options!.includes(value) ? f.options! : [value, ...f.options!]) : [];
  const control = () => {
    if (f.kind === 'select' && options.length <= 3)
      return (
        <fieldset className="sctl" disabled={off}>
          <Segmented label={f.label} value={value} onChange={choose} options={options.map((o) => ({ value: o, label: o }))} />
        </fieldset>
      );
    if (f.kind === 'select')
      return (
        <Select
          label={f.label}
          value={value}
          disabled={off}
          options={options.map((o) => ({ value: o, label: o }))}
          onChange={(e) => choose(e.target.value)}
        />
      );
    if (f.kind === 'switch')
      return <Switch label={f.label} on={stored === 'on'} disabled={off} onChange={(on) => choose(on ? 'on' : 'off')} />;
    if (f.kind === 'sites') return <Sites value={stored} off={off} save={send} />;
    return (
      <>
        <Input
          label={f.label}
          list={f.kind === 'model' ? 's-models' : undefined}
          inputMode={f.kind === 'number' ? 'numeric' : undefined}
          unit={unitOf(f)}
          value={value}
          disabled={off}
          tone={note ? 'bad' : undefined}
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
      </>
    );
  };
  const danger = ask ? DANGER[f.key] : undefined;
  const follows =
    f.key === 'handoff_note' ? `default follows the global switch${global === undefined ? '' : global ? ' (on)' : ' (off)'}` : '';
  const more = [moreOf(f), follows].filter(Boolean).join('; ');
  return (
    <div className="srow" data-field={f.key}>
      <div className="srow-text" title={more || undefined}>
        <span className="srow-title">
          {f.label}
          {pending ? <Icon name="sending" size={14} label="Saving" /> : null}
          {allowed ? null : <Icon name="permission" size={14} label="Founder only" />}
          {more ? <Icon name="info" size={14} label={more} /> : null}
        </span>
      </div>
      <div className="srow-ctl">{control()}</div>
      {draft !== null && stored !== base ? (
        <p className="ui-note warn">
          <Icon name="warn" size={14} label="Changed elsewhere" />
          changed elsewhere: {stored || 'empty'} <Button label="Use" onClick={() => (setDraft(null), setNote(''))} />
        </p>
      ) : null}
      {note ? (
        <p className="ui-note bad" role="alert">
          <Icon name="failed" size={14} label="Not saved" />
          {note}
        </p>
      ) : null}
      {danger ? (
        <div className={VERB_SHEET_DANGER} role="alertdialog" aria-label={danger.title}>
          <strong>{danger.title}</strong>
          <span className="muted">{danger.text}</span>
          <span className={VERB_SHEET_ACTIONS}>
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
        <span key={s} className="site-chip">
          {s}
          <Button
            icon="close"
            size="sm"
            disabled={off}
            label={`Remove ${s}`}
            onClick={() => save(sites.filter((x) => x !== s).join(' '))}
          />
        </span>
      ))}
      {off ? null : (
        <Input
          label="Add a site"
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
