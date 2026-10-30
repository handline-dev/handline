// The Machine page (#/machine): what the machine and the agents use, and where the disk goes. Stop is the only change it makes.
import { useState, type ReactNode } from 'react';
import { bytesText, LOW_DISK_BYTES, useRows, VERBS } from '@handline/app-core';
import { useAgents, useStatus } from '@handline/app-data';
import { Activity, Cpu, HardDrive, MemoryStick, TriangleAlert } from 'lucide-react';
import { AgentUse } from '#src/AgentUse.tsx';
import { VerbSheet } from '#src/AgentVerbs.tsx';
import { AnimatedList } from '#src/AnimatedList.tsx';
import { ContentsPage, type Part } from '#src/ContentsPage.tsx';
import { Bar, toneOf } from '#src/ResourceArea.tsx';
import { PageHead, PageTitle, ScrollPage, SectionHead, SettingsCard } from '#src/ui/index.ts';

const ICON = { width: 16, height: 16, strokeWidth: 1.75, 'aria-hidden': true } as const;
const PARTS: Part[] = [
  { id: 'machine-resources', label: 'Resources', icon: <Activity {...ICON} /> },
  { id: 'machine-agents', label: 'Agents', icon: <Cpu {...ICON} /> },
  { id: 'machine-storage', label: 'Storage', icon: <HardDrive {...ICON} /> },
];

/** One line: the glyph, the name, the bar, the figure with its grey "of total" and, for a low one, the warning. */
function Row({ icon, label, pct, low, children }: { icon: ReactNode; label: string; pct: number; low?: boolean; children: ReactNode }) {
  return (
    <div className="flex items-center gap-3 px-4 py-2 text-md" data-resource={label}>
      <span className="flex-none text-ink-2">{icon}</span>
      <span className="w-20 flex-none">{label}</span>
      <Bar pct={pct} tone={toneOf(pct, low)} />
      <span className="flex w-52 flex-none items-center justify-between gap-2 tabular-nums">
        <span>{children}</span>
        {low ? (
          <span className="inline-flex items-center gap-1 text-bad">
            <TriangleAlert width={14} height={14} strokeWidth={1.75} aria-hidden />
            Low
          </span>
        ) : null}
      </span>
    </div>
  );
}

export function MachinePage() {
  const r = useStatus().data?.resources;
  const { rows, total } = useRows(r);
  const agents = useAgents().data?.agents ?? [];
  const [stopping, setStopping] = useState<string | null>(null);
  const target = agents.find((a) => a.name === stopping);
  const m = r?.machine;
  const diskUsed = m && m.disk_total_bytes ? Math.round((1 - m.disk_free_bytes / m.disk_total_bytes) * 100) : 0;
  return (
    <ScrollPage className="machine-page" aria-label="Machine" beside>
      <PageHead>
        <PageTitle>Machine</PageTitle>
      </PageHead>
      <ContentsPage parts={PARTS}>
        <section id="machine-resources">
          <SectionHead>Resources</SectionHead>
          <SettingsCard>
            {m ? (
              <>
                <Row icon={<Cpu {...ICON} />} label="CPU" pct={Math.min(100, Math.round(m.load_per_core * 100))}>
                  {Math.round(m.load_per_core * 100)}% <span className="text-ink-3">of {m.cores} cores</span>
                </Row>
                <Row icon={<MemoryStick {...ICON} />} label="Memory" pct={Math.round(100 - m.ram_free_pct)}>
                  {bytesText(m.ram_used_bytes)} <span className="text-ink-3">of {bytesText(m.ram_total_bytes)}</span>
                </Row>
                <Row icon={<HardDrive {...ICON} />} label="Disk" pct={diskUsed} low={m.disk_free_bytes < LOW_DISK_BYTES}>
                  {bytesText(m.disk_free_bytes)} left <span className="text-ink-3">of {bytesText(m.disk_total_bytes)}</span>
                </Row>
              </>
            ) : (
              <p className="muted pad">The daemon has not reported the machine yet.</p>
            )}
            {r?.start_hold ? <p className="m-0 px-4 pb-3 text-wait">Starts are held: {r.start_hold}</p> : null}
          </SettingsCard>
        </section>
        <section id="machine-agents">
          <SectionHead>Agents</SectionHead>
          <p className="muted m-0 mb-1 text-sm">{total}</p>
          <SettingsCard className="px-4 py-3">
            {m ? <AgentUse rows={rows} stop={(name) => agents.some((a) => a.name === name) && setStopping(name)} /> : null}
          </SettingsCard>
          <AnimatedList>
            {target ? <VerbSheet key="VerbSheet" a={target} verb={VERBS.close} onClose={() => setStopping(null)} /> : null}
          </AnimatedList>
        </section>
        <section id="machine-storage">
          <SectionHead>Storage</SectionHead>
          <SettingsCard>
            {m ? (
              <div className="flex flex-col gap-2 px-4 py-3 text-md">
                <span className="flex">
                  <Bar pct={diskUsed} tone={toneOf(diskUsed, m.disk_free_bytes < LOW_DISK_BYTES)} />
                </span>
                <span className="tabular-nums">
                  {bytesText(m.disk_total_bytes - m.disk_free_bytes)} used{' '}
                  <span className="text-ink-3">of {bytesText(m.disk_total_bytes)}</span>
                </span>
              </div>
            ) : (
              <p className="muted pad">The daemon has not reported the disk yet.</p>
            )}
          </SettingsCard>
        </section>
      </ContentsPage>
    </ScrollPage>
  );
}
