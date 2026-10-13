// The Home settings page (#/settings), founder only: the default cap, idle close and three switches through settings.set, each field saving on its
// own (authored: it shows at once and goes back with its own note when refused), and the standing answers. A section nav on the left, then one
// card per section whose rows are a title, one grey line and the control on the right (docs/design-gaps.md G80).
import { useEffect, useState, type ReactNode } from 'react';
import { INVALID, minutesOf, parseCap, parseMinutes } from '@handline/app-core';
import type { SettingsChanges } from '@handline/client';
import { classify, sentence, useServes, useSettings, useWrite } from '@handline/app-data';
import { Answers } from './Answers.tsx';
import { DebugEntry } from './debug/DebugEntry.tsx';
import { Field, Icon, RowControl, RowHint, RowText, RowTitle, SectionHead, SettingsCard, SettingsRow, Switch, type IconName, NOTE } from './ui/index.ts';
import { useOff } from './useOff.ts';
import { View } from './View.tsx';

/** The note under a row: saving (an icon and a word), or why the value is not saved (an icon, never colour alone). */
function Note({ error, pending, bad }: { error: unknown; pending: boolean; bad: string | null }) {
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

function Row(props: { label: string; hint: string; more?: string; children: ReactNode; note?: ReactNode }) {
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

function NumberRow(props: {
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

function SwitchRow(props: { label: string; hint: string; on: boolean; change: (on: boolean) => SettingsChanges }) {
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

const SECTIONS: { id: string; title: string; icon: IconName; group: string }[] = [
  { id: 'concurrency', title: 'Concurrency', icon: 'agent', group: 'Agents' },
  { id: 'closing', title: 'Closing windows', icon: 'waiting', group: 'Agents' },
  { id: 'automation', title: 'Automation', icon: 'sendNow', group: 'Automation' },
  { id: 'answers', title: 'Standing answers', icon: 'answered', group: 'Automation' },
];

/** The nav follows the page: the first section that shows in the top part of the scrolling page is the current one. */
function SectionNav({ ready }: { ready: boolean }) {
  const [at, setAt] = useState(SECTIONS[0]!.id);
  const groups = [...new Set(SECTIONS.map((s) => s.group))];
  useEffect(() => {
    if (!ready || typeof IntersectionObserver === 'undefined') return;
    const els = SECTIONS.map((s) => document.getElementById(`set-${s.id}`)).filter((e): e is HTMLElement => !!e);
    const seen = new Set<string>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const id = e.target.id.replace(/^set-/, '');
          if (e.isIntersecting) seen.add(id);
          else seen.delete(id);
        }
        const first = SECTIONS.find((s) => seen.has(s.id));
        if (first) setAt(first.id);
      },
      { root: els[0]?.closest('.settings') ?? null, rootMargin: '0px 0px -60% 0px' },
    );
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, [ready]);
  return (
    <nav className="settings-nav sticky top-0 flex flex-col gap-3 self-start max-[960px]:hidden" aria-label="Settings sections">
      {groups.map((g) => (
        <div key={g} className="settings-nav-group flex flex-col gap-0.5">
          <RowHint className="settings-nav-title px-2 tracking-[0.04em] uppercase">{g}</RowHint>
          {SECTIONS.filter((s) => s.group === g).map((s) => (
            <button
              key={s.id}
              type="button"
              className="settings-nav-item flex h-7 items-center gap-2 rounded-ctl border-0 bg-transparent px-2 text-left text-body font-normal text-ink-2 hover:bg-raised hover:text-ink aria-[current=true]:bg-raised aria-[current=true]:font-semibold aria-[current=true]:text-ink"
              aria-current={at === s.id ? 'true' : undefined}
              onClick={() => {
                setAt(s.id);
                document.getElementById(`set-${s.id}`)?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
              }}
            >
              <Icon name={s.icon} size={14} label={s.title} aria-hidden />
              {s.title}
            </button>
          ))}
        </div>
      ))}
    </nav>
  );
}

export function SettingsPage() {
  const q = useSettings();
  const { served } = useServes('settings.get');
  const v = q.data?.values;
  return (
    <section className="settings mx-auto h-full w-full overflow-auto px-4 pt-4 pb-6" aria-label="Settings">
      <header className="settings-head mx-auto mb-4 flex max-w-[calc(var(--w-form)_+_160px_+_var(--sp-7))] flex-wrap items-center gap-x-3 gap-y-1">
        <h2 className="m-0 text-page font-semibold text-ink">Settings</h2>
        <p className="settings-lede m-0 basis-full text-body text-ink-2">Defaults for every agent. Each change saves as you make it.</p>
      </header>
      <p className="settings-phone muted hidden max-sm:block">Open this on a computer.</p>
      <div className="settings-layout grid grid-cols-[160px_minmax(0,var(--w-form))] justify-center gap-6 max-[960px]:grid-cols-[minmax(0,var(--w-form))]">
        {served ? <SectionNav ready={!!v} /> : null}
        <div className="settings-body flex min-w-0 flex-col gap-4 max-sm:hidden">
          {!served ? (
            <p className="muted pad">This daemon does not serve settings, or they are for the founder only.</p>
          ) : (
            <View q={q} served shape="settings" what="settings">
              {v ? (
                <>
                  <section id="set-concurrency">
                    <SectionHead>Concurrency</SectionHead>
                    <SettingsCard>
                      <NumberRow
                        label="Default cap"
                        hint="Agents working at once on an account"
                        more="An account with its own cap keeps it"
                        unit="agents"
                        current={v.active_cap === undefined ? '' : String(v.active_cap)}
                        parse={parseCap}
                        change={(n) => ({ active_cap: n })}
                        allowed="a whole number from 1, or empty for the default"
                      />
                    </SettingsCard>
                  </section>
                  <section id="set-closing">
                    <SectionHead>Closing windows</SectionHead>
                    <SettingsCard>
                      <NumberRow
                        label="Idle close"
                        hint="Idle time before a session closes"
                        more="0 is never"
                        unit="min"
                        current={minutesOf(v.park_timeout_s)}
                        parse={parseMinutes}
                        change={(s) => ({ park_timeout_s: s })}
                        allowed="whole minutes, 0 for never, or empty for the default"
                      />
                    </SettingsCard>
                  </section>
                  <section id="set-automation">
                    <SectionHead>Automation</SectionHead>
                    <SettingsCard>
                      <SwitchRow
                        label="Handoff note"
                        hint="A note for the next session before a close or move"
                        on={!!v.handoff_note}
                        change={(on) => ({ handoff_note: on })}
                      />
                      <SwitchRow
                        label="Auto-accept"
                        hint="Answer the dialogs under Standing answers by itself"
                        on={!!v.auto_accept_dialogs}
                        change={(on) => ({ auto_accept_dialogs: on })}
                      />
                      <SwitchRow
                        label="Ignore cache expiry"
                        hint="Resume the old session even after its cache went cold"
                        on={!!v.ignore_cache_expiry}
                        change={(on) => ({ ignore_cache_expiry: on })}
                      />
                    </SettingsCard>
                  </section>
                  <section id="set-answers">
                    <Answers />
                  </section>
                </>
              ) : null}
            </View>
          )}
        </div>
      </div>
      <DebugEntry />
    </section>
  );
}
