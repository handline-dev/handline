// The quiet admin page (#/admin, linked from the foot of Settings): what the daemon is doing, why agents wake, how full their memory is, and what just happened.
// Read-only: it shows what `hl` printed for a person, in words.
import { useState } from 'react';
import { ago, useRows, VERBS } from '@handline/app-core';
import { useAgents, useMemory, useRecentEvents, useServes, useStatus, useTriggers } from '@handline/app-data';
import { MenuButton, MenuItem, PageHead, PageTitle, RowTitle, ScrollPage, SectionHead, SettingsCard, SettingsRow } from '#src/ui/index.ts';
import { VerbSheet } from '#src/AgentVerbs.tsx';
import { View } from '#src/View.tsx';
import { AnimatedList } from '#src/AnimatedList.tsx';

const HEALTH: Record<string, string> = { ok: 'working', degraded: 'degraded', down: 'down' };

function Daemon() {
  const q = useStatus();
  const { served } = useServes('daemon.status');
  const s = q.data;
  return (
    <>
      <SectionHead>Daemon</SectionHead>
      <View q={q} served={served} shape="settings" what="the daemon status">
        {s ? (
          <SettingsCard className="admin-daemon">
            <SettingsRow>
              <RowTitle plain>
                Running since {ago(s.started_at)} ago, version <code>{s.version.slice(0, 8)}</code>
              </RowTitle>
            </SettingsRow>
            <SettingsRow>
              <RowTitle plain>
                Terminals on {s.terminal_host.name}: {HEALTH[s.terminal_host.health] ?? s.terminal_host.health}
                {s.terminal_host.detail ? <span className="muted"> · {s.terminal_host.detail}</span> : null}
              </RowTitle>
            </SettingsRow>
            <SettingsRow>
              <RowTitle plain>
                {s.agents.total} agents:{' '}
                {Object.entries(s.agents.by_state)
                  .map(([k, n]) => `${n} ${stateWord(k)}`)
                  .join(', ') || 'none'}
              </RowTitle>
            </SettingsRow>
          </SettingsCard>
        ) : null}
      </View>
    </>
  );
}

function Usage() {
  const q = useStatus();
  const agents = useAgents().data?.agents ?? [];
  const [stopping, setStopping] = useState<string | null>(null);
  const { rows, total } = useRows(q.data?.resources);
  const close = VERBS.close;
  const target = agents.find((a) => a.name === stopping);
  return (
    <>
      <SectionHead>What agents use</SectionHead>
      {!q.data?.resources ? (
        <p className="muted">Not measured yet.</p>
      ) : (
        <SettingsCard as="ul" className="admin-usage">
          {rows.map((r) => {
            const a = agents.find((x) => x.name === r.agent);
            return (
              <SettingsRow as="li" key={r.agent} data-agent={r.agent}>
                <RowTitle plain>
                  {r.agent}{' '}
                  <span className="muted">
                    CPU {r.cpu} · memory {r.memory} · {a ? stateWord(a.state) : 'unknown'}
                  </span>
                </RowTitle>
                {a ? (
                  <MenuButton size="sm" icon="ellipsis" label={`More for ${r.agent}`}>
                    <MenuItem onSelect={() => setStopping(r.agent)}>Stop</MenuItem>
                  </MenuButton>
                ) : null}
              </SettingsRow>
            );
          })}
          <SettingsRow className="admin-usage-total">
            <RowTitle plain>{total}</RowTitle>
          </SettingsRow>
        </SettingsCard>
      )}
      <AnimatedList>{target ? <VerbSheet key="VerbSheet" a={target} verb={close} onClose={() => setStopping(null)} /> : null}</AnimatedList>
    </>
  );
}

function Wakes() {
  const q = useTriggers();
  const { served } = useServes('agent.triggers');
  const rows = q.data?.agents ?? [];
  return (
    <>
      <SectionHead>Why agents wake</SectionHead>
      <View q={q} served={served} shape="settings" what="the triggers" empty={!rows.length && 'No agents.'}>
        <SettingsCard as="ul" className="admin-wakes">
          {rows.map((a) => (
            <SettingsRow as="li" key={a.agent} data-agent={a.agent}>
              <RowTitle plain>
                {a.agent} <span className="muted">{a.fired ? `wakes: ${a.reason}` : a.reason}</span>
              </RowTitle>
            </SettingsRow>
          ))}
        </SettingsCard>
      </View>
    </>
  );
}

function Memory() {
  const q = useMemory();
  const { served } = useServes('memory.status');
  const rows = q.data?.agents ?? [];
  return (
    <>
      <SectionHead>Agent memory</SectionHead>
      <View q={q} served={served} shape="settings" what="the memory status" empty={!rows.length && 'No agents.'}>
        <SettingsCard as="ul" className="admin-memory">
          {rows.map((a) => {
            const pct = a.budget ? Math.round((a.bytes / a.budget) * 100) : 0;
            return (
              <SettingsRow as="li" key={a.agent} data-agent={a.agent}>
                <RowTitle plain>
                  {a.agent}{' '}
                  <span className="muted">
                    {pct}% full, {a.entries} notes{a.unused.length ? `, unused: ${a.unused.join(', ')}` : ''}
                  </span>
                </RowTitle>
              </SettingsRow>
            );
          })}
        </SettingsCard>
      </View>
    </>
  );
}

function Recent() {
  const { served } = useServes('events.feed');
  const q = useRecentEvents(30);
  const events = q.data ?? (q.isError ? [] : null);
  return (
    <>
      <SectionHead>Just happened</SectionHead>
      {events === null ? (
        <p className="muted">{served ? 'Loading…' : 'This daemon does not serve its event feed.'}</p>
      ) : (
        <SettingsCard as="ul" className="admin-events">
          {events.map((e) => (
            <SettingsRow as="li" key={e.cursor}>
              <RowTitle plain>
                {e.title ?? e.type}
                {e.agent ? <span className="muted"> · {e.agent}</span> : null} <span className="muted">{ago(e.at)} ago</span>
              </RowTitle>
            </SettingsRow>
          ))}
          {!events.length ? <SettingsRow>Nothing yet.</SettingsRow> : null}
        </SettingsCard>
      )}
    </>
  );
}

/** The daemon's agent state as the app words it: a parked agent waits for a time (docs/copy.md 7). */
const stateWord = (k: string): string => (k === 'parked' ? 'waiting' : k);

export function AdminPage() {
  return (
    <ScrollPage className="admin" aria-label="Admin">
      <PageHead>
        <PageTitle>Admin</PageTitle>
      </PageHead>
      <div className="flex w-full flex-col gap-4">
        <Daemon />
        <Usage />
        <Wakes />
        <Memory />
        <Recent />
      </div>
    </ScrollPage>
  );
}
