// What the machine and each agent use (daemon.status resources): the status bar's one line and the admin view's rows, heaviest first.
import type { Resources } from '@handline/client';

const GB = 2 ** 30;
const MB = 2 ** 20;

/** A cell turns red at this point: memory with under 15% free, the load at 90% of the cores, a disk under the 10 GB where the daemon raises a card. */
export const LOW_MEMORY_PCT = 15;
export const HIGH_LOAD = 0.9;
export const LOW_DISK_BYTES = 10 * GB;

export type Cell = { text: string; over: boolean };

/** Bytes as the shortest honest figure: 3.6GB, 420MB. */
export function bytesText(n: number): string {
  if (n >= GB) return `${(n / GB).toFixed(n >= 10 * GB ? 0 : 1)}GB`;
  return `${Math.round(n / MB)}MB`;
}

/** The status bar's cells, in order: memory left, CPU, disk left. Null until the daemon has measured. */
export function machineCells(r: Resources | null | undefined): Cell[] | null {
  if (!r) return null;
  const m = r.machine;
  const left = m.ram_total_bytes ? Math.round((m.ram_free_bytes / m.ram_total_bytes) * 100) : 100;
  const load = m.cores ? m.load_1m / m.cores : 0;
  return [
    { text: `memory ${left}% left`, over: left < LOW_MEMORY_PCT },
    { text: `CPU ${load >= HIGH_LOAD ? 'high' : 'ok'}`, over: load >= HIGH_LOAD },
    { text: `disk ${bytesText(m.disk_free_bytes)} left`, over: m.disk_free_bytes < LOW_DISK_BYTES },
  ];
}

export type UseRow = { agent: string; cpu: string; memory: string; cpuPct: number; rss: number };

/** One row per agent, the heaviest first (CPU, then memory, then name), and the totals line over them. */
export function useRows(r: Resources | null | undefined): { rows: UseRow[]; total: string } {
  const rows = Object.entries(r?.agents ?? {})
    .map(([agent, u]) => ({ agent, cpu: `${Math.round(u.cpu_pct)}%`, memory: bytesText(u.rss_bytes), cpuPct: u.cpu_pct, rss: u.rss_bytes }))
    .sort((a, b) => b.cpuPct - a.cpuPct || b.rss - a.rss || a.agent.localeCompare(b.agent));
  const cpu = rows.reduce((n, x) => n + x.cpuPct, 0);
  const rss = rows.reduce((n, x) => n + x.rss, 0);
  const n = rows.length;
  return { rows, total: `${n} agent${n === 1 ? '' : 's'} · CPU ${Math.round(cpu)}% · memory ${bytesText(rss)}` };
}
