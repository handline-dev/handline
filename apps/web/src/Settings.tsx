// The Home settings page (#/settings), founder only: the default cap, idle close and three switches through settings.set, each field saving on its
// own (authored: it shows at once and goes back with its own note when refused), and the standing answers. One
// card per section whose rows are a title, one grey line and the control on the right (docs/design-gaps.md G80).
import { capField, idleField, limitFields, paceFields, type KV } from '@handline/app-core';
import { useServes, useSettings } from '@handline/app-data';
import { Answers } from '#src/Answers.tsx';
import { Devices } from '#src/Devices.tsx';
import { DebugEntry } from '#src/debug/DebugEntry.tsx';
import { SectionHead, SettingsCard } from '#src/ui/index.ts';
import { HandoffPrompt } from '#src/HandoffPrompt.tsx';
import { NumberRow, SwitchRow } from '#src/SettingsRows.tsx';
import { Appearance, BackgroundRows, Connection, NoticeBatch, Notifications, TerminalRow, WeekAlertRow } from '#src/SettingsMore.tsx';
import { TeamSeal } from '#src/TeamSeal.tsx';
import { SettingsFold, SettingsShell } from '#src/SettingsShell.tsx';
import { View } from '#src/View.tsx';

/** The machine-wide cap stays in General; the other load limits are Advanced. */
const PARTS = [
  { id: 'set-concurrency', label: 'Concurrency' },
  { id: 'set-closing', label: 'Closing windows' },
  { id: 'set-handoff', label: 'Handoff' },
  { id: 'set-notifications', label: 'Notifications' },
  { id: 'set-answers', label: 'Always answer' },
  { id: 'set-devices', label: 'Devices' },
  { id: 'set-appearance', label: 'Appearance' },
  { id: 'set-advanced', label: 'Advanced' },
];

const isHostCap = (f: { setting: string }) => f.setting === 'host_active_cap';

export function SettingsPage({ storage }: { storage: KV | null }) {
  const q = useSettings();
  const { served } = useServes('settings.get');
  const v = q.data?.values;
  const limits = v ? limitFields(v) : [];
  return (
    <SettingsShell tab="settings" label="Settings" parts={PARTS}>
      <p className="settings-phone muted hidden max-sm:block">Open this on a computer.</p>
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
                    {limits
                      .filter((f) => isHostCap(f))
                      .map((f) => (
                        <NumberRow key={f.label} {...f} />
                      ))}
                    {v.pacing === undefined ? null : (
                      <SwitchRow
                        label="Pacing"
                        hint="An account ahead of its weekly pace line starts fewer turns"
                        on={!!v.pacing}
                        change={(on) => ({ pacing: on })}
                      />
                    )}
                  </SettingsCard>
                </section>
                <section id="set-closing">
                  <SectionHead>Closing windows</SectionHead>
                  <SettingsCard>
                    <NumberRow {...idleField(v.park_timeout_s)} hint="Idle time before a session closes" more="0 is never" />
                  </SettingsCard>
                </section>
                <section id="set-handoff">
                  <SectionHead>Handoff</SectionHead>
                  <SettingsCard>
                    <SwitchRow
                      label="Handoff note"
                      hint="Before a session closes, the agent writes a note for the next one"
                      on={!!v.handoff_note}
                      change={(on) => ({ handoff_note: on })}
                    />
                    <HandoffPrompt settings={q.data!} />
                  </SettingsCard>
                </section>
                <section id="set-notifications">
                  <Notifications />
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
                <SettingsFold id="set-advanced" title="Advanced" hint="Limits, pacing, background work, terminal view">
                  <SettingsCard>
                    {limits
                      .filter((f) => !isHostCap(f))
                      .map((f) => (
                        <NumberRow key={f.label} {...f} />
                      ))}
                    {paceFields(v).map((f) => (
                      <NumberRow key={f.label} {...f} />
                    ))}
                    <BackgroundRows notice={v.bg_notice_s} grace={v.bg_grace_s} check={v.bg_check_s} />
                    <NoticeBatch digest={v.notice_digest_s} />
                    <SwitchRow
                      label="Ignore cache expiry"
                      hint="Resume the old session even after its cache went cold"
                      on={!!v.ignore_cache_expiry}
                      change={(on) => ({ ignore_cache_expiry: on })}
                    />
                    <TerminalRow storage={storage} />
                    <WeekAlertRow />
                  </SettingsCard>
                </SettingsFold>
                <SettingsFold title="About" hint="Connection, version, Admin, Debug, seal the team files">
                  <Connection />
                  <DebugEntry />
                  <TeamSeal />
                </SettingsFold>
              </>
            ) : null}
          </View>
        )}
      </div>
    </SettingsShell>
  );
}
