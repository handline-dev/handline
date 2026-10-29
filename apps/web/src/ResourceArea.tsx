// The status bar's right-hand resource area: the keep-awake choice and the cells of what the machine and the agents use (CPU, memory, disk),
// which open a read-only summary popover. The limits are set in Settings, Advanced.
import { useEffect, useState } from 'react';
import {
  bytesText,
  DEFAULT_LIMITS,
  LOW_DISK_BYTES,
  loadFull,
  memoryLow,
  resourceCells,
  useRows,
  type Limits,
  type PageSettings,
} from '@handline/app-core';
import type { Resources } from '@handline/client';
import { useSettings, useStatus, useWrite } from '@handline/app-data';
import { Coffee, Cpu, HardDrive, MemoryStick, TriangleAlert } from 'lucide-react';
import { MenuButton, MenuCheckItem, Popup, STRIP_ITEM } from '#src/ui/index.ts';

type Awake = 'off' | 'on' | 'working';
const CELL_ICON = { cpu: Cpu, memory: MemoryStick, disk: HardDrive } as const;
const AWAKE: { value: Awake; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'on', label: 'On' },
  { value: 'working', label: 'While agents work' },
];

/** The limits as saved, each falling back to its default. */
function useLimits(): Limits & { awake: Awake } {
  const v = (useSettings().data?.values ?? {}) as Partial<Limits> & { keep_awake?: Awake };
  return {
    load_per_core_pct: v.load_per_core_pct ?? DEFAULT_LIMITS.load_per_core_pct,
    ram_free_min_pct: v.ram_free_min_pct ?? DEFAULT_LIMITS.ram_free_min_pct,
    heavy_slots: v.heavy_slots ?? DEFAULT_LIMITS.heavy_slots,
    awake: v.keep_awake ?? 'off',
  };
}

/** The window event that opens the resources popover from elsewhere, such as the held card's button. */
export const OPEN_RESOURCES = 'handline:open-resources';

export function ResourceArea() {
  const data = useStatus().data;
  const r = data?.resources;
  const limits = useLimits();
  const save = useWrite('settings.set');
  const working = (data?.agents.by_state as Record<string, number> | undefined)?.running ?? 0;
  const cells = resourceCells(r, limits);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener(OPEN_RESOURCES, show);
    return () => window.removeEventListener(OPEN_RESOURCES, show);
  }, []);
  const label = AWAKE.find((a) => a.value === limits.awake)!.label;
  return (
    <>
      <MenuButton
        className={`${STRIP_ITEM} flex-none text-xs text-ink-2`}
        label="Keep the Mac awake"
        name="Keep awake"
        align="start"
        trigger={
          <>
            <Coffee width={14} height={14} strokeWidth={1.75} aria-hidden />
            {label}
            {limits.awake === 'working' ? ` (${working})` : ''}
          </>
        }
      >
        {AWAKE.map((a) => (
          <MenuCheckItem
            key={a.value}
            checked={limits.awake === a.value}
            onCheckedChange={() => save.mutate({ changes: { keep_awake: a.value } satisfies PageSettings })}
          >
            {a.label}
            {a.value === 'working' ? ` · ${working} working` : ''}
          </MenuCheckItem>
        ))}
        <p className="m-0 px-2 pb-1 text-xs text-ink-2">Closing the lid still sleeps the Mac.</p>
      </MenuButton>
      {cells && r ? (
        <span className="relative inline-flex">
          <button
            type="button"
            className="resource-cell tint -mx-1 inline-flex cursor-pointer items-center gap-3 rounded-sm border-0 bg-transparent px-1 py-0 text-xs text-ink-2"
            aria-expanded={open}
            title="What the machine and the agents use"
            onClick={() => setOpen(!open)}
          >
            {cells.map((c) => {
              const Glyph = CELL_ICON[c.kind];
              return (
                <span key={c.kind} className={`inline-flex items-center gap-1 ${c.warn ? 'text-bad' : ''}`} data-over={c.warn || undefined}>
                  <Glyph width={14} height={14} strokeWidth={1.75} aria-hidden />
                  {c.text}
                  {c.warn ? <TriangleAlert width={12} height={12} strokeWidth={1.75} aria-hidden /> : null}
                </span>
              );
            })}
          </button>
          <Popup open={open} onClose={() => setOpen(false)} title="Resources" align="right" up bare>
            <Details r={r} limits={limits} />
          </Popup>
        </span>
      ) : null}
    </>
  );
}

/** The bar's tone: calm under 60%, amber to 85%, red above (and red for what is low, whatever the share). */
const toneOf = (pct: number, low = false): 'ok' | 'wait' | 'bad' => (low || pct >= 85 ? 'bad' : pct >= 60 ? 'wait' : 'ok');
const TONE = { ok: 'bg-ok', wait: 'bg-wait', bad: 'bg-bad' } as const;

function Bar({ pct, mark, tone }: { pct: number; mark?: number; tone: keyof typeof TONE }) {
  return (
    <span className="relative h-2 flex-1 overflow-hidden rounded-sm bg-raised" aria-hidden>
      <span className={`block h-full ${TONE[tone]}`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
      {mark !== undefined ? <span className="absolute top-0 h-full w-px bg-ink" style={{ left: `${Math.min(100, mark)}%` }} /> : null}
    </span>
  );
}

/** One line: the glyph, the name, the value with what it is of, and the bar. A low one says "Low" as well as turning red. */
function Line({ icon: Glyph, label, value, of, pct, mark, tone, low }: LineProps) {
  return (
    <div className="flex items-center gap-2 py-1" data-resource={label}>
      <Glyph width={16} height={16} strokeWidth={1.75} className="flex-none text-ink-2" aria-hidden />
      <span className="w-16 flex-none">{label}</span>
      <span className="w-28 flex-none tabular-nums">
        <span className="font-medium">{value}</span>
        {of ? <span className="text-ink-3"> {of}</span> : null}
        {low ? <span className="text-bad"> · Low</span> : null}
      </span>
      <Bar pct={pct} mark={mark} tone={tone} />
    </div>
  );
}
type LineProps = {
  icon: typeof Cpu;
  label: string;
  value: string;
  of?: string;
  pct: number;
  mark?: number;
  tone: keyof typeof TONE;
  low?: boolean;
};

/** A summary only: three lines, why starts are held, and the agents by use. Limits are Settings, Advanced. */
function Details({ r, limits }: { r: Resources; limits: Limits }) {
  const { rows } = useRows(r);
  const m = r.machine;
  const full = loadFull(r, limits.load_per_core_pct);
  const low = memoryLow(r, limits.ram_free_min_pct);
  const cpu = Math.round(Math.min(100, m.load_per_core * 100));
  const memUsed = 100 - m.ram_free_pct;
  const diskLow = m.disk_free_bytes < LOW_DISK_BYTES;
  const diskUsed = m.disk_total_bytes ? Math.round((1 - m.disk_free_bytes / m.disk_total_bytes) * 100) : 0;
  return (
    <div className="resource-details flex max-h-[70vh] w-[380px] max-w-[90vw] flex-col gap-1 overflow-y-auto p-3 text-md">
      <Line
        icon={Cpu}
        label="CPU"
        value={`${cpu}%`}
        pct={cpu}
        mark={limits.load_per_core_pct ? (limits.load_per_core_pct / 300) * 100 : undefined}
        tone={full ? 'bad' : toneOf(cpu)}
      />
      <Line
        icon={MemoryStick}
        label="Memory"
        value={bytesText(m.ram_used_bytes)}
        of={`of ${bytesText(m.ram_total_bytes)}`}
        pct={memUsed}
        tone={toneOf(memUsed, low)}
        low={low}
      />
      <Line
        icon={HardDrive}
        label="Disk"
        value={`${bytesText(m.disk_free_bytes)} left`}
        of={`of ${bytesText(m.disk_total_bytes)}`}
        pct={diskUsed}
        tone={toneOf(diskUsed, diskLow)}
        low={diskLow}
      />
      {r.start_hold ? (
        <p className="resource-hold m-0 rounded-sm bg-raised px-2 py-1 text-wait" role="status">
          Starts are held: {r.start_hold}
        </p>
      ) : null}
      <ul className="m-0 mt-1 flex list-none flex-col gap-0.5 p-0 text-sm text-ink-2" aria-label="Agents by use">
        {rows.slice(0, 8).map((x) => (
          <li key={x.agent} className="flex justify-between gap-2" data-agent={x.agent}>
            <a className="truncate text-ink-2" href={`#/agents/${encodeURIComponent(x.agent)}`}>
              {x.agent}
            </a>
            <span className="tabular-nums">
              {x.cpu} · {x.memory}
            </span>
          </li>
        ))}
      </ul>
      <a className="mt-1 text-sm" href="#/machine">
        Open Machine ›
      </a>
    </div>
  );
}
