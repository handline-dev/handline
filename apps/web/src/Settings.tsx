// The Home settings page (#/settings), founder only: the default cap, idle close and three switches through settings.set, each field saving on its
// own (authored: it shows at once and goes back with its own note when refused), and the standing answers. A section nav on the left, then one
// card per section whose rows are a title, one grey line and the control on the right (docs/design-gaps.md G80).
import { useEffect, useState } from 'react';
import { INVALID, capField, idleField, limitFields, type KV } from '@handline/app-core';
import { useServes, useSettings } from '@handline/app-data';
import { Answers } from '#src/Answers.tsx';
import { Devices } from '#src/Devices.tsx';
import { DebugEntry } from '#src/debug/DebugEntry.tsx';
import { PageHead, PageTitle, ScrollPage, Icon, RowHint, SectionHead, SettingsCard, type IconName } from '#src/ui/index.ts';
import { NumberRow, SwitchRow } from '#src/SettingsRows.tsx';
import { Appearance, BackgroundRows, Connection, Notifications } from '#src/SettingsMore.tsx';
import { SettingsTabs } from '#src/SettingsTabs.tsx';
import { View } from '#src/View.tsx';

export const parsePrompt = (t: string) => (/[\r\n]/.test(t) ? INVALID : t.trim());

const SECTIONS: { id: string; title: string; icon: IconName; group: string }[] = [
  { id: 'concurrency', title: 'Concurrency', icon: 'agent', group: 'Agents' },
  { id: 'closing', title: 'Closing windows', icon: 'waiting', group: 'Agents' },
  { id: 'notifications', title: 'Notifications', icon: 'inbox', group: 'Agents' },
  { id: 'automation', title: 'Automation', icon: 'sendNow', group: 'Automation' },
  { id: 'answers', title: 'Standing answers', icon: 'answered', group: 'Automation' },
  { id: 'devices', title: 'Paired devices', icon: 'agent', group: 'Access' },
  { id: 'appearance', title: 'Appearance', icon: 'settings', group: 'This window' },
  { id: 'connection', title: 'Connection', icon: 'accounts', group: 'This window' },
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

export function SettingsPage({ storage }: { storage: KV | null }) {
  const q = useSettings();
  const { served } = useServes('settings.get');
  const v = q.data?.values;
  return (
    <ScrollPage className="settings" aria-label="Settings">
      <PageHead className="settings-head">
        <PageTitle>Settings</PageTitle>
        <p className="settings-lede m-0 basis-full text-body text-ink-2">Defaults for every agent. Each change saves as you make it.</p>
      </PageHead>
      <SettingsTabs value="settings" />
      <p className="settings-phone muted hidden max-sm:block">Open this on a computer.</p>
      <div className="settings-layout grid grid-cols-[160px_minmax(0,1fr)] gap-6 max-[960px]:grid-cols-[minmax(0,1fr)]">
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
                        {...capField(v.active_cap)}
                        hint="Agents working at once on an account"
                        more="An account with its own cap keeps it"
                      />
                      {limitFields(v).map((f) => (
                        <NumberRow key={f.label} {...f} />
                      ))}
                    </SettingsCard>
                  </section>
                  <section id="set-closing">
                    <SectionHead>Closing windows</SectionHead>
                    <SettingsCard>
                      <NumberRow {...idleField(v.park_timeout_s)} hint="Idle time before a session closes" more="0 is never" />
                      <BackgroundRows notice={v.bg_notice_s} grace={v.bg_grace_s} check={v.bg_check_s} />
                    </SettingsCard>
                  </section>
                  <section id="set-notifications">
                    <Notifications digest={v.notice_digest_s} />
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
                      <NumberRow
                        text
                        wide
                        label="Handoff prompt"
                        hint={`One line; {path} is the note's file and {max} its size limit.${
                          v.handoff_prompt?.includes('{path}') ? '' : ' Without {path} the path is added on a line of its own.'
                        }`}
                        current={v.handoff_prompt ?? ''}
                        parse={parsePrompt}
                        change={(t) => ({ handoff_prompt: t })}
                        allowed="one line: no line break"
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
                  <section id="set-devices">
                    <Devices />
                  </section>
                  <section id="set-appearance">
                    <Appearance storage={storage} />
                  </section>
                  <section id="set-connection">
                    <Connection />
                  </section>
                </>
              ) : null}
            </View>
          )}
        </div>
      </div>
      <DebugEntry />
    </ScrollPage>
  );
}
