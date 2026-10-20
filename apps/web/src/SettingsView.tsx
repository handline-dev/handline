// An agent's settings page (the bridge's agent_settings.py): one column of fields that save one by one through agent.set, an authored write.
// A text field saves on blur or Enter, a select and a switch on change; a refusal puts the value back and the field's own note says why.
import { useState } from 'react';
import { DANGER, type Field, FIELDS, fieldUnit } from '@handline/app-core';
import type { AgentDetail } from '@handline/client';
import { useStore } from '@handline/app-react';
import { useAgentDetail, useFieldRow, useServes } from '@handline/app-data';
import { goAgent } from '#src/route.ts';
import { Schedules } from '#src/Schedules.tsx';
import {
  Button,
  Field as Input,
  Icon,
  PageHead,
  PageTitle,
  RowControl,
  RowText,
  RowTitle,
  ScrollPage,
  SectionHead,
  Segmented,
  Select,
  SettingsCard,
  SettingsRow,
  Sheet,
  SheetActions,
  Switch,
  type IconName,
  NOTE,
} from '#src/ui/index.ts';
import { View } from '#src/View.tsx';

const SECTIONS: { id: Field['section']; title: string; icon: IconName }[] = [
  { id: 'model', title: 'Model', icon: 'agent' },
  { id: 'work', title: 'Work', icon: 'tasks' },
  { id: 'session', title: 'Session', icon: 'session' },
  { id: 'browser', title: 'Browser', icon: 'open' },
];

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
    <ScrollPage className="settings" aria-label={`${agent} settings`}>
      <PageHead className="settings-head settings-agent">
        <Button className="back self-end mb-4 [&_svg]:rotate-180" icon="chevronRight" label={`Back to ${agent}`} onClick={() => goAgent(agent)}>
          {agent}
        </Button>
        <PageTitle>Settings</PageTitle>
        <p className="settings-lede m-0 basis-full text-body text-ink-2">Each change saves as you make it.</p>
      </PageHead>
      {served ? null : <p className="muted pad">This daemon does not edit agent settings.</p>}
      {served && !editable ? <p className="muted pad">Only the founder edits agent settings.</p> : null}
      <View q={q} served shape="settings" what={`${agent} settings`}>
        {d ? (
          <div className="settings-body settings-agent flex min-w-0 flex-col gap-4 max-sm:hidden">
            <SettingsCard>
              <SettingsRow>
                <RowText>
                  <RowTitle>Session runs on</RowTitle>
                </RowText>
                <RowControl>{d.account ?? 'not chosen yet'}</RowControl>
              </SettingsRow>
            </SettingsCard>
            {SECTIONS.map((s) => (
              <section key={s.id}>
                <SectionHead>
                  <Icon name={s.icon} size={14} label={s.title} aria-hidden /> {s.title}
                </SectionHead>
                <SettingsCard>
                  {FIELDS.filter((f) => f.section === s.id).map((f) => (
                    <FieldRow key={f.key} agent={agent} f={f} stored={d.settings?.[f.key]} editable={editable} role={role} />
                  ))}
                </SettingsCard>
              </section>
            ))}
            <Schedules agent={agent} role={role} />
          </div>
        ) : null}
      </View>
    </ScrollPage>
  );
}

function FieldRow(props: { agent: string; f: Field; stored: string | undefined; editable: boolean; role: string | undefined }) {
  const { f } = props;
  const { stored, pending, global, draft, setDraft, note, setNote, ask, setAsk, allowed, off, value, send, commit, choose, options } = useFieldRow(props);
  const [base, setBase] = useState('');
  const edit = (v: string) => {
    if (draft === null) setBase(stored);
    setDraft(v);
    setNote('');
  };
  // Two or three options sit in one segmented control, more in a select with a chevron.
  const control = () => {
    if (f.kind === 'select' && options.length <= 3)
      return (
        <fieldset className="sctl m-0 min-w-0 border-0 p-0 disabled:opacity-60" disabled={off}>
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
          unit={fieldUnit(f)}
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
    <SettingsRow data-field={f.key}>
      <RowText title={more || undefined}>
        <RowTitle>
          {f.label}
          {pending ? <Icon name="sending" size={14} label="Saving" /> : null}
          {allowed ? null : <Icon name="permission" size={14} label="Founder only" />}
          {more ? <Icon name="info" size={14} label={more} /> : null}
        </RowTitle>
      </RowText>
      <RowControl>{control()}</RowControl>
      {draft !== null && stored !== base ? (
        <p className={`${NOTE} text-wait`}>
          <Icon name="warn" size={14} label="Changed elsewhere" />
          changed elsewhere: {stored || 'empty'} <Button label="Use" onClick={() => (setDraft(null), setNote(''))} />
        </p>
      ) : null}
      {note ? (
        <p className={`${NOTE} text-bad`} role="alert">
          <Icon name="failed" size={14} label="Not saved" />
          {note}
        </p>
      ) : null}
      {danger ? (
        <Sheet danger role="alertdialog" aria-label={danger.title}>
          <strong>{danger.title}</strong>
          <span className="muted">{danger.text}</span>
          <SheetActions>
            <Button
              variant="solid"
              label={danger.go}
              className="max-sm:min-h-12 max-sm:flex-1"
              onClick={() => {
                const v = ask!;
                setAsk(null);
                send(v);
              }}
            >
              {danger.go}
            </Button>
            <Button variant="outline" label="Cancel" className="max-sm:min-h-12 max-sm:flex-1" onClick={() => setAsk(null)} />
          </SheetActions>
        </Sheet>
      ) : null}
    </SettingsRow>
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
    <span className="sites flex flex-wrap items-center justify-end gap-1.5">
      {sites.map((s) => (
        <span
          key={s}
          className="site-chip inline-flex items-center gap-1 rounded-chip border border-line-strong pl-2 font-mono text-[12px]/4 font-normal"
        >
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
