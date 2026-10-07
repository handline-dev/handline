// Every primitive of ui/* on one page (gallery.html in dev): what a screen builder looks at before drawing anything of their own.
import { useState, type ReactNode } from 'react';
import { Button, Dialog, Empty, Field, Icon, Page, Popup, PopupRow, Segmented, Select, State, Switch, type StateKind } from './ui/index.ts';

const STATES: StateKind[] = ['working', 'booting', 'needs', 'blocked', 'idle', 'done', 'noTerminal'];

function Section({ name, children }: { name: string; children: ReactNode }) {
  return (
    <section className="gallery-section">
      <h3>{name}</h3>
      <div className="gallery-row">{children}</div>
    </section>
  );
}

export function Gallery() {
  const [on, setOn] = useState(true);
  const [mode, setMode] = useState<'on' | 'agent' | 'off'>('agent');
  const [pop, setPop] = useState(false);
  const [dialog, setDialog] = useState(false);
  return (
    <Page>
      <h1 className="gallery-title">ui</h1>
      <Section name="State">
        {STATES.map((s) => (
          <State key={s} kind={s} word />
        ))}
      </Section>
      <Section name="Icon">
        {(['inbox', 'tasks', 'chat', 'terminal', 'team', 'stats', 'settings', 'search', 'plus', 'refresh', 'menu'] as const).map((n) => (
          <Icon key={n} name={n} />
        ))}
      </Section>
      <Section name="Button">
        <Button label="Create" variant="solid" keys="⌘↵" />
        <Button label="Cancel" variant="outline" />
        <Button label="Ghost" />
        <Button label="Refresh" icon="refresh" />
        <Button label="Add" icon="plus" variant="outline" size="sm" />
        <Button label="Off" disabled variant="outline" />
      </Section>
      <Section name="Field">
        <Field label="Name" placeholder="handline.architect" />
        <Field label="Limit" unit="min" defaultValue="30" note="Longer than a day is refused" tone="warn" />
        <Field label="Branch" defaultValue="x y" note="No spaces" tone="bad" />
      </Section>
      <Section name="Select, Switch, Segmented">
        <Select
          label="Model"
          options={[
            { value: 'opus', label: 'opus' },
            { value: 'sonnet', label: 'sonnet' },
          ]}
        />
        <Switch on={on} onChange={setOn} label="Auto-park" />
        <Switch on={false} onChange={() => undefined} label="Off" />
        <Segmented
          label="Mode"
          value={mode}
          onChange={setMode}
          options={[
            { value: 'on', label: 'On', icon: 'done' },
            { value: 'agent', label: 'Agent', icon: 'agent' },
            { value: 'off', label: 'Off', icon: 'muted' },
          ]}
        />
      </Section>
      <Section name="Popup, Dialog">
        <span className="gallery-anchor">
          <Button label="Popup" variant="outline" onClick={() => setPop(!pop)} />
          <Popup open={pop} onClose={() => setPop(false)} title="Accounts" onRefresh={() => undefined}>
            <PopupRow icon="accounts" label="alpha" hint="41%" opens />
            <PopupRow icon="accounts" label="gamma" hint="–" />
          </Popup>
        </span>
        <Button label="Dialog" variant="outline" onClick={() => setDialog(true)} />
        {dialog ? (
          <Dialog title="New task" onClose={() => setDialog(false)} action={{ label: 'Create', onClick: () => setDialog(false) }}>
            <Field label="Title" />
          </Dialog>
        ) : null}
      </Section>
      <Section name="Empty">
        <Empty icon="inbox" name="Nothing waits on you" hint="Results and questions land here" />
      </Section>
    </Page>
  );
}
