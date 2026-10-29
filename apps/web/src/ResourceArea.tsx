// The status bar's right-hand resource area: the keep-awake choice and the cells of what the machine and the agents use (CPU, memory, disk), which opens a popover of the
// machine's load, CPU, memory, why starts are held and each agent's use. Everything shown is read-only; a limit's editor opens only on a click
// on its row and closes with the popover.
import { useEffect, useState, type ReactNode } from 'react';
import {
  bytesText,
  memoryFree,
  DEFAULT_LIMITS,
  LOAD_PRESETS,
  LOW_DISK_BYTES,
  limitSentence,
  loadFull,
  memoryLow,
  MEMORY_PRESETS,
  resourceCells,
  useRows,
  type Limits,
  type PageSettings,
} from '@handline/app-core';
import type { Resources } from '@handline/client';
import { sentence, classify, useSettings, useStatus, useWrite } from '@handline/app-data';
import { Coffee, Cpu, HardDrive, MemoryStick, TriangleAlert } from 'lucide-react';
import { Button, Icon, MenuButton, MenuCheckItem, Popup, Segmented, STRIP_ITEM } from '#src/ui/index.ts';

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

function Bar({ pct, mark, hot }: { pct: number; mark?: number; hot?: boolean }) {
  return (
    <span className="relative h-1.5 flex-1 overflow-hidden rounded-sm bg-raised" aria-hidden>
      <span className={`block h-full ${hot ? 'bg-wait' : 'bg-working'}`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
      {mark !== undefined ? <span className="absolute top-0 h-full w-px bg-ink" style={{ left: `${Math.min(100, mark)}%` }} /> : null}
    </span>
  );
}

function Details({ r, limits }: { r: Resources; limits: Limits }) {
  const [editing, setEditing] = useState<'load' | 'memory' | null>(null);
  const { rows } = useRows(r);
  const m = r.machine;
  const full = loadFull(r, limits.load_per_core_pct);
  const low = memoryLow(r, limits.ram_free_min_pct);
  const row =
    'flex w-full cursor-pointer items-center gap-2 rounded-sm border-0 bg-transparent px-1 py-1 -mx-1 text-left text-md text-ink tint';
  const mark = (which: 'load' | 'memory') => (
    <Icon
      name="chevronRight"
      size={12}
      className={`flex-none text-ink-3 transition-transform ${editing === which ? 'rotate-90' : ''}`}
      aria-hidden
    />
  );
  const toggle = (which: 'load' | 'memory') => setEditing(editing === which ? null : which);
  return (
    <div className="resource-details flex max-h-[70vh] w-[340px] max-w-[90vw] flex-col gap-1 overflow-y-auto p-3 text-md">
      <button type="button" className={row} aria-expanded={editing === 'load'} onClick={() => toggle('load')}>
        <span className="w-14">Load</span>
        <Bar
          pct={(m.load_per_core / 3) * 100}
          mark={limits.load_per_core_pct ? (limits.load_per_core_pct / 300) * 100 : undefined}
          hot={full}
        />
        <span className="font-mono tabular-nums">
          {m.load_per_core.toFixed(1)} / {limits.load_per_core_pct ? (limits.load_per_core_pct / 100).toFixed(1) : 'never'}
        </span>
        {mark('load')}
      </button>
      {editing === 'load' ? <LoadEditor r={r} limits={limits} /> : null}
      <div className="flex items-center gap-2 py-1">
        <span className="w-14">CPU</span>
        <Bar pct={Math.min(100, m.load_per_core * 100)} hot={full} />
        <span className="font-mono tabular-nums">{Math.round(Math.min(100, m.load_per_core * 100))}%</span>
      </div>
      <button type="button" className={row} aria-expanded={editing === 'memory'} onClick={() => toggle('memory')}>
        <span className="w-14">Memory</span>
        <Bar pct={100 - m.ram_free_pct} hot={low} />
        <span className="font-mono tabular-nums">{memoryFree(r)}</span>
        {mark('memory')}
      </button>
      {editing === 'memory' ? <MemoryEditor r={r} limits={limits} /> : null}
      {r.start_hold ? (
        <p className="resource-hold m-0 rounded-sm bg-raised px-2 py-1 text-wait" role="status">
          Starts are held: {r.start_hold}
        </p>
      ) : null}
      <ul className="m-0 flex list-none flex-col gap-0.5 p-0 text-xs text-ink-2" aria-label="Agents by use">
        {rows.slice(0, 8).map((x) => (
          <li key={x.agent} className="flex justify-between gap-2">
            <span className="truncate">{x.agent}</span>
            <span className="font-mono tabular-nums">
              {x.cpu} · {x.memory}
            </span>
          </li>
        ))}
      </ul>
      <p className={`m-0 text-xs ${m.disk_free_bytes < LOW_DISK_BYTES ? 'text-bad' : 'text-ink-2'}`}>
        Disk {bytesText(m.disk_free_bytes)} free
      </p>
      <a className="text-xs" href="#/admin">
        Admin
      </a>
    </div>
  );
}

function useChange() {
  const save = useWrite('settings.set');
  return {
    set: (changes: Partial<Limits> & PageSettings) => save.mutate({ changes }),
    error: save.error ? sentence(classify(save.error)) : null,
  };
}

function Editor({ children, note, reset, error }: { children: ReactNode; note: string; reset: () => void; error: string | null }) {
  return (
    <div className="limit-editor flex flex-col gap-1.5 rounded-sm bg-surface p-2">
      {children}
      <span className="text-xs text-ink-2">{note}</span>
      <span className="text-xs text-ink-2">Running agents are never stopped, and starting one by hand always works.</span>
      {error ? (
        <span className="text-bad" role="alert">
          {error}
        </span>
      ) : null}
      <Button variant="ghost" size="sm" label="Reset to defaults" onClick={reset} />
    </div>
  );
}

function LoadEditor({ r, limits }: { r: Resources; limits: Limits }) {
  const { set, error } = useChange();
  const value = String(limits.load_per_core_pct);
  const options: { value: string; label: string }[] = LOAD_PRESETS.map((p) => ({ value: String(p.pct), label: p.label }));
  // a value outside the presets (set by hand elsewhere) is still shown, as its own choice
  if (!options.some((o) => o.value === value)) options.push({ value, label: (limits.load_per_core_pct / 100).toFixed(2) });
  return (
    <Editor
      error={error}
      note={limitSentence(r.machine.load_per_core.toFixed(1), loadFull(r, limits.load_per_core_pct), limits.load_per_core_pct === 0)}
      reset={() => set({ load_per_core_pct: DEFAULT_LIMITS.load_per_core_pct, heavy_slots: DEFAULT_LIMITS.heavy_slots })}
    >
      <Segmented label="Load limit" value={value} options={options} onChange={(v) => set({ load_per_core_pct: Number(v) })} />
      <span className="flex items-center gap-2">
        Heavy test runs at once
        <Button
          variant="outline"
          size="sm"
          label="Fewer"
          aria-label="Fewer"
          disabled={limits.heavy_slots <= 1}
          onClick={() => set({ heavy_slots: limits.heavy_slots - 1 })}
        >
          −
        </Button>
        <span className="font-mono tabular-nums">{limits.heavy_slots}</span>
        <Button
          variant="outline"
          size="sm"
          label="More"
          aria-label="More"
          disabled={limits.heavy_slots >= r.machine.cores}
          onClick={() => set({ heavy_slots: limits.heavy_slots + 1 })}
        >
          +
        </Button>
      </span>
    </Editor>
  );
}

function MemoryEditor({ r, limits }: { r: Resources; limits: Limits }) {
  const { set, error } = useChange();
  const value = String(limits.ram_free_min_pct);
  const options: { value: string; label: string }[] = MEMORY_PRESETS.map((p) => ({ value: String(p.pct), label: p.label }));
  if (!options.some((o) => o.value === value)) options.push({ value, label: `${limits.ram_free_min_pct}%` });
  return (
    <Editor
      error={error}
      note={limitSentence(memoryFree(r), memoryLow(r, limits.ram_free_min_pct), limits.ram_free_min_pct === 0)}
      reset={() => set({ ram_free_min_pct: DEFAULT_LIMITS.ram_free_min_pct })}
    >
      <Segmented label="Free memory floor" value={value} options={options} onChange={(v) => set({ ram_free_min_pct: Number(v) })} />
    </Editor>
  );
}
