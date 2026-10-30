// The Machine page (#/machine): what the machine and the agents use, and where the disk goes. Read-only.
import { useState } from 'react';
import { bytesText, useRows } from '@handline/app-core';
import { useStatus } from '@handline/app-data';
import { ContentsPage, type Part } from '#src/ContentsPage.tsx';
import { PageHead, PageTitle, ScrollPage, SectionHead, SettingsCard } from '#src/ui/index.ts';

const PARTS: Part[] = [
  { id: 'machine-resources', label: 'Resources' },
  { id: 'machine-agents', label: 'Agents' },
  { id: 'machine-storage', label: 'Storage' },
];

type Sort = 'cpu' | 'memory' | 'name';

export function MachinePage() {
  const r = useStatus().data?.resources;
  const { rows, total } = useRows(r);
  const [sort, setSort] = useState<Sort>('cpu');
  const m = r?.machine;
  const sorted = [...rows].sort((a, b) =>
    sort === 'name' ? a.agent.localeCompare(b.agent) : sort === 'memory' ? b.rss - a.rss : b.cpuPct - a.cpuPct,
  );
  const used = m && m.disk_total_bytes ? Math.round((1 - m.disk_free_bytes / m.disk_total_bytes) * 100) : 0;
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
              <dl className="m-0 grid grid-cols-[8rem_1fr] gap-y-1 px-4 py-3 text-md">
                <dt className="text-ink-2">CPU</dt>
                <dd className="m-0 tabular-nums">{Math.round(m.load_per_core * 100)}%</dd>
                <dt className="text-ink-2">Memory</dt>
                <dd className="m-0 tabular-nums">
                  {bytesText(m.ram_used_bytes)} <span className="text-ink-3">of {bytesText(m.ram_total_bytes)}</span>
                </dd>
                <dt className="text-ink-2">Disk</dt>
                <dd className="m-0 tabular-nums">
                  {bytesText(m.disk_free_bytes)} left <span className="text-ink-3">of {bytesText(m.disk_total_bytes)}</span>
                </dd>
              </dl>
            ) : (
              <p className="muted pad">The daemon has not reported the machine yet.</p>
            )}
            {r?.start_hold ? <p className="m-0 px-4 pb-3 text-wait">Starts are held: {r.start_hold}</p> : null}
          </SettingsCard>
        </section>
        <section id="machine-agents">
          <SectionHead>Agents</SectionHead>
          <p className="muted m-0 mb-1 text-sm">{total}</p>
          <SettingsCard>
            <table className="w-full border-collapse text-md">
              <thead>
                <tr className="text-left text-sm text-ink-3">
                  {(['name', 'cpu', 'memory'] as const).map((k) => (
                    <th key={k} className="px-4 py-1 font-normal" aria-sort={sort === k ? 'descending' : undefined}>
                      <button
                        type="button"
                        className={`cursor-pointer border-0 bg-transparent p-0 text-sm ${sort === k ? 'font-medium text-ink' : 'text-ink-3'}`}
                        onClick={() => setSort(k)}
                      >
                        {k === 'name' ? 'Agent' : k === 'cpu' ? 'CPU' : 'Memory'}
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map((x) => (
                  <tr key={x.agent} data-agent={x.agent}>
                    <td className="px-4 py-1">
                      <a href={`#/agents/${encodeURIComponent(x.agent)}`}>{x.agent}</a>
                    </td>
                    <td className="px-4 py-1 tabular-nums">{x.cpu}</td>
                    <td className="px-4 py-1 tabular-nums">{x.memory}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </SettingsCard>
        </section>
        <section id="machine-storage">
          <SectionHead>Storage</SectionHead>
          <SettingsCard>
            {m ? (
              <div className="flex flex-col gap-2 px-4 py-3 text-md">
                <span className="relative h-2 overflow-hidden rounded-sm bg-raised" aria-hidden>
                  <span className="block h-full bg-acc" style={{ width: `${used}%` }} />
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
