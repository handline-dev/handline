// Every primitive of ui/* and every token on one page (gallery.html in dev): what a screen builder looks at before drawing anything of their own.
import { useState, type ReactNode } from 'react';
import { Button, Card, CardWhat, CodeBlock, Dialog, Empty, Field, Icon, Page, Popup, PopupRow, Segmented, Select, State, Switch, type StateKind } from './ui/index.ts';
import { bubbleClass, MSG_BODY, MSG_FAILED, MSG_FROM, MSG_META } from './msgClass.ts';

const STATES: StateKind[] = ['working', 'booting', 'needs', 'blocked', 'idle', 'done', 'noTerminal'];

function Section({ name, children }: { name: string; children: ReactNode }) {
  return (
    <section className="gallery-section">
      <h3>{name}</h3>
      <div className="gallery-row">{children}</div>
    </section>
  );
}

const computed = (token: string): string => getComputedStyle(document.documentElement).getPropertyValue(token).trim();

function Swatch({ token }: { token: string }) {
  return (
    <span className="inline-flex w-[7.5rem] flex-col gap-0.5">
      <span className="h-8 rounded-card border border-line" style={{ background: `var(${token})` }} />
      <span className="font-mono text-[11px] text-ink-2">{token}</span>
      <span className="font-mono text-[11px] text-ink-3">{computed(token)}</span>
    </span>
  );
}

const TYPE: [string, string][] = [
  ['--t-hint', 'hint: the smallest aside'],
  ['--t-label', 'label: names and values'],
  ['--t-body', 'body: the reading size of a message'],
  ['--t-name', 'name: a medium weight'],
  ['--t-title', 'title: a card or a pane'],
  ['--t-page', 'page: the one heading'],
];

export function Tokens() {
  const spaces = ['--sp-1', '--sp-2', '--sp-3', '--sp-4', '--sp-5', '--sp-6', '--sp-7', '--sp-8'];
  const radii = ['--r-chip', '--r-ctl', '--r-card', '--r-dialog', '--r-pill'];
  return (
    <>
      <Section name="Color · surfaces">
        {['--bg-0', '--bg-1', '--bg-2', '--bg-3', '--line', '--line-strong'].map((t) => (
          <Swatch key={t} token={t} />
        ))}
      </Section>
      <Section name="Color · text">
        {['--ink', '--ink-2', '--ink-3', '--link', '--focus'].map((t) => (
          <Swatch key={t} token={t} />
        ))}
      </Section>
      <Section name="Color · states (each sits beside a glyph, never alone)">
        {['--s-working', '--s-wait', '--s-bad', '--s-ok'].map((t) => (
          <Swatch key={t} token={t} />
        ))}
      </Section>
      <Section name="Type">
        <span className="grid gap-1.5">
          {TYPE.map(([t, sample]) => (
            <span key={t} style={{ font: `var(${t})` }} className="text-ink">
              {sample} <span className="text-ink-3">— {t}</span>
            </span>
          ))}
          <span style={{ font: 'var(--t-mono)' }} className="text-ink">
            mono: 12px/16px — T3053, paths, output {/* local-ref-ok: a made-up number showing the mono style */}
          </span>
        </span>
      </Section>
      <Section name="Space (the 4 px grid)">
        <span className="grid flex-1 gap-1">
          {spaces.map((t) => (
            <span key={t} className="flex items-center gap-2">
              <span className="h-2 bg-primary" style={{ width: `var(${t})` }} />
              <span className="font-mono text-[11px] text-ink-2">
                {t} = {computed(t)}
              </span>
            </span>
          ))}
        </span>
      </Section>
      <Section name="Radius">
        {radii.map((t) => (
          <span key={t} className="inline-flex flex-col items-center gap-1">
            <span className="h-10 w-14 border border-line-strong" style={{ borderRadius: `var(${t})` }} />
            <span className="font-mono text-[11px] text-ink-2">{t}</span>
          </span>
        ))}
      </Section>
    </>
  );
}

/** The stream's parts as the classes render them today; the classes come from msgClass, so a restyle moves this with it. */
function Chat() {
  const meta = (
    <div className={MSG_META}>
      <span className="mono">#3053</span>{/* local-ref-ok: a made-up number for the stamp example */}
      <Icon name="delivered" size={13} label="Confirmed by the agent" />
    </div>
  );
  return (
    <>
      <Section name="Chat · bubbles">
        <span className="grid flex-1 gap-2">
          <span className={bubbleClass('agent')}>
            <div className={MSG_FROM}>
              <span style={{ color: 'var(--s-ok)' }}>backend</span> <span className="text-ink-3">01:32</span>
            </div>
            <div className={MSG_BODY}>The worktree is rebased and the suite is green — pushing the branch now.</div>
            {meta}
          </span>
          <span className={bubbleClass('person')}>
            <div className={MSG_BODY}>push and I will take a look</div>
            {meta}
          </span>
          <span className={bubbleClass('other')}>
            <div className={MSG_FROM}>
              frontend → backend
            </div>
            <div className={MSG_BODY}>the login page is yours again, I moved my card over</div>
            {meta}
          </span>
          <span className={bubbleClass('agent', { picked: true })}>
            <div className={MSG_BODY}>which account should own the deploy?</div>
            <span className={bubbleClass('person', { nested: true })}>
              <div className={MSG_BODY}>alpha, it has the budget</div>
            </span>
            {meta}
          </span>
          <span className={bubbleClass('person', { failed: true })}>
            <div className={MSG_BODY}>run the migration</div>
            <div className={MSG_FAILED}>
              <Icon name="failed" size={13} /> refused: the daemon is in pair mode
              <button className="icon" aria-label="Retry">
                <Icon name="retry" size={14} />
              </button>
            </div>
          </span>
          <span className={bubbleClass('agent')} data-landing="withdrawn">
            <div className={MSG_BODY}>starting the deploy now</div>
          </span>
        </span>
      </Section>
      <Section name="Chat · the stream's quiet rows">
        <span className="grid flex-1 gap-1">
          <Card variant="chat">
            <Icon name="changed" label="Task 3 changed" />
            <span className="card-event">changed</span>
            <a className="mono" href="#/tasks/3">
              T3
            </a>
            <CardWhat className="muted">Fix the login redirect — queued → doing</CardWhat>
            <span className="muted card-time">01:32</span>
          </Card>
          <Card variant="chat">
            <Icon name="session" />
            <CardWhat className="muted">Handoff: auth flows to the worktree, keys stay</CardWhat>
            <span className="muted card-time">01:30</span>
          </Card>
          <span className="line tool self-start font-mono text-[12px] text-ink-2">
            <Icon name="tool" size={14} label="Bash" />
            <span className="mono"> Bash</span> <span className="muted">pnpm test</span>
          </span>
          <Card variant="chat">
            <CodeBlock scroll>{'$ pnpm test\n333 passing (11s)'}</CodeBlock>
          </Card>
        </span>
      </Section>
    </>
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
      <Tokens />
      <Chat />
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
