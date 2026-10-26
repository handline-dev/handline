// The Home settings sections that are not just a number or a switch: notices, background work, this window's appearance, and the connection.
import { useEffect, useState } from 'react';
import { parseSeconds, type KV } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { useRead, useServes, useSettings } from '@handline/app-data';
import { Button, SectionHead, Segmented, SettingsCard } from '#src/ui/index.ts';
import { applyTheme, themeOf, type Theme } from '#src/appearance.ts';
import { setting } from '#src/daemon.ts';
import { NumberRow, Row, SwitchRow } from '#src/SettingsRows.tsx';
import { shell, useShellMode } from '#src/shell.ts';

const SECONDS = 'whole seconds, or empty for the default';
const secondsOf = (v: number | undefined) => (v === undefined ? '' : String(v));

const PUSH = [
  { key: 'push_prompt_asked', label: 'A question asked', hint: 'A desktop notice when an agent asks you something' },
  { key: 'push_task_done', label: 'A task done', hint: 'A desktop notice when a task is finished' },
  { key: 'push_stall', label: 'A stall', hint: 'A desktop notice when an agent stops making progress' },
  { key: 'push_account_out', label: 'An account out', hint: 'A desktop notice when an account hits its limit' },
] as const;

export function Notifications({ digest }: { digest: number | undefined }) {
  const values = useSettings().data?.values as Record<string, unknown> | undefined;
  return (
    <>
      <SectionHead>Notifications</SectionHead>
      <SettingsCard>
        {PUSH.map((p) => (
          <SwitchRow key={p.key} label={p.label} hint={p.hint} on={values?.[p.key] !== false} change={(on) => ({ [p.key]: on })} />
        ))}
        <NumberRow
          label="Notice batch"
          hint="Wait this long after the first notice, then tell the agent all of them at once"
          more="Never in the middle of a turn; a message from a person comes sooner"
          unit="s"
          current={secondsOf(digest)}
          parse={parseSeconds}
          change={(n) => ({ notice_digest_s: n })}
          allowed={SECONDS}
        />
      </SettingsCard>
    </>
  );
}

/** The idle-background rows join the Closing windows card: when an idle agent with background work is told to finish it, and when it is stopped. */
export function BackgroundRows(props: { notice?: number; grace?: number; check?: number }) {
  return (
    <>
      <NumberRow
        label="Background notice"
        hint="Idle time before an agent with background work is told to finish or stop it"
        more="Empty follows Idle close"
        unit="s"
        current={secondsOf(props.notice)}
        parse={parseSeconds}
        change={(n) => ({ bg_notice_s: n })}
        allowed={SECONDS}
      />
      <NumberRow
        label="Background grace"
        hint="Time after that notice before the work is stopped"
        unit="s"
        current={secondsOf(props.grace)}
        parse={parseSeconds}
        change={(n) => ({ bg_grace_s: n })}
        allowed={SECONDS}
      />
      <NumberRow
        label="Background check"
        hint="How often an agent with background work is asked which to stop"
        unit="s"
        current={secondsOf(props.check)}
        parse={parseSeconds}
        change={(n) => ({ bg_check_s: n })}
        allowed={SECONDS}
      />
    </>
  );
}

/** This window's own choices, kept in the browser: the daemon has nothing to say about them. */
export function Appearance({ storage }: { storage: KV | null }) {
  const [theme, setTheme] = useState<Theme>(() => themeOf(storage));
  const [terminal, setTerminal] = useState(() => (setting(storage, 'terminal', 'auto').value === 'text' ? 'text' : 'auto'));
  return (
    <>
      <SectionHead>Appearance</SectionHead>
      <SettingsCard>
        <Row label="Theme" hint="This window only">
          <Segmented
            label="Theme"
            value={theme}
            options={[
              { value: 'system', label: 'System' },
              { value: 'dark', label: 'Dark' },
              { value: 'light', label: 'Light' },
            ]}
            onChange={(v) => {
              setTheme(v);
              setting(storage, 'theme', 'system').save(v);
              applyTheme(v);
            }}
          />
        </Row>
        <Row
          label="Terminal view"
          hint="Auto draws the live terminal; Text shows the text grid. This window only"
          more="A phone held upright always uses the text view"
        >
          <Segmented
            label="Terminal view"
            value={terminal}
            options={[
              { value: 'auto', label: 'Auto' },
              { value: 'text', label: 'Text' },
            ]}
            onChange={(v) => {
              setTerminal(v);
              setting(storage, 'terminal', 'auto').save(v);
            }}
          />
        </Row>
      </SettingsCard>
    </>
  );
}

type Follow = { following: boolean; sha: string; state: string; next: string | null };

/** The follow-main build's commit and whether main has moved, with a button to look now. Nothing in any other build. */
function useFollow(): [Follow | null, () => void] {
  const [f, setF] = useState<Follow | null>(null);
  const load = () =>
    void shell('follow')
      ?.follow?.()
      .then((r) => r.ok && setF(r));
  useEffect(load, []);
  const check = () => void shell('checkUpdate')?.checkUpdate?.().then(load);
  return [f?.following ? f : null, check];
}

/** What this window is connected to. The mode and the main this build follows can be changed here; the rest is read only. */
export function Connection() {
  const hello = useSelector((s) => s.client.hello);
  const shellMode = useShellMode();
  const mode = shellMode
    ? shellMode.mode === 'own'
      ? 'This computer runs its own daemon'
      : (shellMode.remote ?? 'Another computer')
    : null;
  const [follow, check] = useFollow();
  const rows: [string, string | undefined][] = [
    ['Daemon', mode ?? undefined],
    ['Version', hello?.daemon.slice(0, 9)],
    [
      'App',
      follow?.sha ? `main ${follow.sha.slice(0, 7)}${follow.next ? `, newer main ${follow.next.slice(0, 7)} is waiting` : ''}` : undefined,
    ],
    ['Role', hello?.role],
    ['Transport', hello ? (hello.transport === 'tls' ? 'Encrypted network connection' : 'Local socket') : undefined],
  ];
  const { served } = useServes('connection.get');
  const info = useRead('connection.get', {}).data;
  const net = info?.listener;
  if (info) {
    rows.push(
      ['Network address', net ? net.address : 'No network listener'],
      ...(net ? ([['Encryption', net.encryption === 'tls' ? 'Encrypted' : 'Tailscale']] as [string, string][]) : []),
      ...(net?.tailscale_host ? ([['Tailscale host', net.tailscale_host]] as [string, string][]) : []),
      ['Heavy slots', String(info.heavy_slots.slots)],
      ['Dev mode', info.dev_mode ? 'On' : 'Off'],
    );
  }
  const print = served ? net?.fingerprint : null;
  return (
    <>
      <SectionHead>Connection</SectionHead>
      <SettingsCard>
        {rows
          .filter((r): r is [string, string] => !!r[1])
          .map(([label, value]) => (
            <Row key={label} label={label} hint="">
              <span className="text-ink-2">{value}</span>
            </Row>
          ))}
        {follow && !follow.next ? (
          <Row label="Update" hint="Look at GitHub's main now">
            <Button label="Check for update" size="sm" onClick={check} />
          </Row>
        ) : null}
        {shellMode ? (
          <Row
            label="Mode"
            hint={shellMode.mode === 'own' ? "Use another computer's daemon instead" : 'Run a daemon on this computer instead'}
          >
            <Button
              label={shellMode.mode === 'own' ? 'Switch to Connect' : 'Switch to Own daemon'}
              size="sm"
              onClick={() => void shell('setMode')?.setMode?.(shellMode.mode === 'own' ? 'connect' : 'own')}
            />
          </Row>
        ) : null}
        {print ? (
          <Row label="Fingerprint" hint="Compare it with the one a device shows when pairing">
            <span className="text-ink-2" title={print}>
              {print.length > 24 ? `${print.slice(0, 15)}…${print.slice(-6)}` : print}
            </span>
            <Button icon="reference" label="Copy fingerprint" size="sm" onClick={() => void navigator.clipboard?.writeText(print)} />
          </Row>
        ) : null}
      </SettingsCard>
    </>
  );
}
