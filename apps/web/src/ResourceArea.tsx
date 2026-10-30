// The status bar's right-hand resource area: the keep-awake choice and the cells of what the machine and the agents use (CPU, memory, disk),
// which open the resource manager: each line carries its limit, changed in place; the held card and Settings open it too.
import { useEffect, useState, type ReactNode } from 'react';
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
import { classify, sentence, useSettings, useStatus, useWrite } from '@handline/app-data';
import { Coffee, Cpu, HardDrive, Layers, MemoryStick, TriangleAlert } from 'lucide-react';
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
  const [openKey, setOpenKey] = useState(0);
  const [openAt, setOpenAt] = useState<'load' | 'memory' | 'slots' | null>(null);
  useEffect(() => {
    const show = (e: Event) => {
      setOpenAt((e as CustomEvent<{ at?: 'load' | 'memory' | 'slots' }>).detail?.at ?? null);
      setOpenKey((k) => k + 1);
      setOpen(true);
    };
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
            <Details key={openKey} r={r} limits={limits} openAt={openAt} />
          </Popup>
        </span>
      ) : null}
    </>
  );
}

type Which = 'load' | 'memory' | 'slots';
const GB = 2 ** 30;

/** A limit edited in place: − and + (or a typed number), the effect in one line, and 0 for off where the daemon allows it. A typed number is saved on blur or Enter; the notice has Undo. */
function Stepper({
  value,
  min,
  step,
  unit,
  effect,
  off,
  onChange,
}: {
  value: number;
  min: number;
  step: number;
  unit: string;
  effect: string;
  /** What 0 means, or null where a limit cannot be off. */
  off: string | null;
  onChange: (n: number) => void;
}) {
  const set = (n: number) => onChange(Math.max(min, Math.round(n)));
  const commit = (typed: string) => typed !== '' && Number(typed) !== value && set(Number(typed));
  return (
    <div className="limit-editor flex flex-col gap-1.5 rounded-sm bg-surface p-2 text-md" data-limit-editor>
      <span className="flex items-center gap-2">
        <span className="inline-flex items-center overflow-hidden rounded-sm border border-line">
          <button
            type="button"
            className="tint h-7 w-7 cursor-pointer border-0 bg-transparent text-ink"
            aria-label="Lower"
            disabled={value <= min}
            onClick={() => set(value - step)}
          >
            −
          </button>
          <input
            type="number"
            aria-label="Limit"
            className="h-7 w-16 border-0 bg-transparent text-center tabular-nums text-ink"
            key={value}
            defaultValue={value}
            min={min}
            onBlur={(e) => commit(e.currentTarget.value)}
            onKeyDown={(e) => e.key === 'Enter' && commit(e.currentTarget.value)}
          />
          <button
            type="button"
            className="tint h-7 w-7 cursor-pointer border-0 bg-transparent text-ink"
            aria-label="Raise"
            onClick={() => set(value + step)}
          >
            +
          </button>
        </span>
        <span className="text-ink-2">{unit}</span>
      </span>
      <span>{value === 0 && off ? off : effect}</span>
      <span className="text-xs text-ink-3">
        {off ? '0 turns the limit off. ' : ''}Running agents are never stopped; starting one by hand always works.
      </span>
    </div>
  );
}

/** The bar's tone: calm under 60%, amber to 85%, red above (and red for what is low, whatever the share). */
const toneOf = (pct: number, low = false): 'ok' | 'wait' | 'bad' => (low || pct >= 85 ? 'bad' : pct >= 60 ? 'wait' : 'ok');
const TONE = { ok: 'bg-ok', wait: 'bg-wait', bad: 'bg-bad' } as const;

function Bar({ pct, mark, tone }: { pct: number; mark?: number; tone: keyof typeof TONE }) {
  return (
    <span className="relative h-2 flex-1 overflow-hidden rounded-sm bg-raised" aria-hidden>
      <span className={`block h-full ${TONE[tone]}`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
      {mark !== undefined ? (
        <span className="absolute top-0 h-full w-0.5 bg-ink" style={{ left: `${Math.min(100, Math.max(0, mark))}%` }} />
      ) : null}
    </span>
  );
}

type LineProps = {
  icon: typeof Cpu;
  label: string;
  value: string;
  /** "Limit 300%", "No limit", or nothing where the daemon has no limit to give. */
  limit?: string;
  pct?: number;
  mark?: number;
  tone?: keyof typeof TONE;
  hot?: boolean;
  open?: boolean;
  onToggle?: () => void;
  children?: ReactNode;
};

/** One line: the glyph, the name, the bar with its limit mark, the value over the limit. A line with a limit opens its editor on a click. */
function Line({ icon: Glyph, label, value, limit, pct, mark, tone = 'ok', hot, open, onToggle, children }: LineProps) {
  const body = (
    <>
      <Glyph width={16} height={16} strokeWidth={1.75} className="flex-none text-ink-2" aria-hidden />
      <span className="w-20 flex-none">{label}</span>
      {pct !== undefined ? <Bar pct={pct} mark={mark} tone={tone} /> : <span className="flex-1" />}
      <span className="flex w-24 flex-none flex-col items-end tabular-nums">
        <span className="font-medium">{value}</span>
        {limit ? <span className="text-xs text-ink-3">{limit}</span> : null}
      </span>
    </>
  );
  const row = `flex w-full items-center gap-2 rounded-sm px-2 py-1.5 ${hot ? 'bg-[color-mix(in_srgb,var(--s-bad)_10%,transparent)]' : ''}`;
  return (
    <div data-resource={label}>
      {onToggle ? (
        <button
          type="button"
          className={`${row} cursor-pointer border-0 bg-transparent text-left text-md text-ink tint`}
          aria-expanded={!!open}
          onClick={onToggle}
        >
          {body}
        </button>
      ) : (
        <div className={`${row} text-md`}>{body}</div>
      )}
      {open ? children : null}
    </div>
  );
}

/** The four lines with their limits, why starts are held, and the agents by use. Disk has no limit: the daemon keeps none. */
function Details({ r, limits, openAt }: { r: Resources; limits: Limits; openAt: Which | null }) {
  const { rows } = useRows(r);
  const save = useWrite('settings.set');
  const [editing, setEditing] = useState<Which | null>(openAt);
  const [said, setSaid] = useState<{ text: string; undo?: () => void; refused?: boolean } | null>(null);
  useEffect(() => {
    if (!said) return;
    const t = setTimeout(() => setSaid(null), 8000);
    return () => clearTimeout(t);
  }, [said]);
  const m = r.machine;
  const toggle = (w: Which) => setEditing(editing === w ? null : w);
  /** Says so once the daemon has taken it, with Undo to the value before; a refusal says why instead. */
  const change = (what: string, text: string, key: keyof Limits, to: number, was: number) => {
    save.mutate(
      { changes: { [key]: to } satisfies PageSettings },
      {
        onSuccess: () =>
          setSaid({ text: `${what} limit: ${text}`, undo: () => save.mutate({ changes: { [key]: was } satisfies PageSettings }) }),
        onError: (e) =>
          setSaid({ text: `${what} limit not changed: ${sentence(classify(e)) || 'no answer from the daemon'}`, refused: true }),
      },
    );
  };
  const totalGb = m.ram_total_bytes / GB;
  const floorGb = Math.round((m.ram_total_bytes * limits.ram_free_min_pct) / 100 / GB);
  const loadMax = Math.max(300, limits.load_per_core_pct * 1.2, m.load_per_core * 100);
  const loadPct = ((m.load_per_core * 100) / loadMax) * 100;
  const full = loadFull(r, limits.load_per_core_pct);
  const low = memoryLow(r, limits.ram_free_min_pct);
  const diskUsed = m.disk_total_bytes ? Math.round((1 - m.disk_free_bytes / m.disk_total_bytes) * 100) : 0;
  const diskLow = m.disk_free_bytes < LOW_DISK_BYTES;
  const held = !!r.start_hold;
  return (
    <div className="resource-details flex max-h-[70vh] w-[420px] max-w-[92vw] flex-col gap-1 overflow-y-auto p-3 text-md">
      <p className={`m-0 flex items-center gap-2 px-2 py-1 ${held ? 'resource-hold text-bad' : 'text-ink-2'}`} role="status">
        {held ? `Starts are held: ${r.start_hold}` : 'Agents start as usual'}
      </p>
      <Line
        icon={Cpu}
        label="Load"
        value={`${Math.round(m.load_per_core * 100)}%`}
        limit={limits.load_per_core_pct ? `Limit ${limits.load_per_core_pct}%` : 'No limit'}
        pct={loadPct}
        mark={limits.load_per_core_pct ? (limits.load_per_core_pct / loadMax) * 100 : undefined}
        tone={full ? 'bad' : toneOf(m.load_per_core * 100 > 100 ? 61 : 0)}
        hot={full}
        open={editing === 'load'}
        onToggle={() => toggle('load')}
      >
        <Stepper
          value={limits.load_per_core_pct}
          min={0}
          step={50}
          unit="%"
          off="No limit: new starts are never held."
          effect={`Agents wait above ${limits.load_per_core_pct}%`}
          onChange={(n) => change('Load', n ? `${n}%` : 'off', 'load_per_core_pct', n, limits.load_per_core_pct)}
        />
      </Line>
      <Line
        icon={MemoryStick}
        label="Memory"
        value={`${bytesText(m.ram_free_bytes)} free`}
        limit={limits.ram_free_min_pct ? `Limit ${floorGb} GB free` : 'No limit'}
        pct={100 - m.ram_free_pct}
        mark={limits.ram_free_min_pct ? 100 - limits.ram_free_min_pct : undefined}
        tone={toneOf(100 - m.ram_free_pct, low)}
        hot={low}
        open={editing === 'memory'}
        onToggle={() => toggle('memory')}
      >
        <Stepper
          value={floorGb}
          min={0}
          step={1}
          unit="GB free"
          off="No limit: new starts are never held."
          effect={`Agents wait when free memory is under ${floorGb} GB`}
          onChange={(gb) =>
            change(
              'Memory',
              gb ? `${gb} GB free` : 'off',
              'ram_free_min_pct',
              gb ? Math.min(90, Math.max(1, Math.round((gb / totalGb) * 100))) : 0,
              limits.ram_free_min_pct,
            )
          }
        />
      </Line>
      <Line
        icon={HardDrive}
        label="Disk"
        value={`${bytesText(m.disk_free_bytes)} left`}
        pct={diskUsed}
        tone={toneOf(diskUsed, diskLow)}
        hot={diskLow}
      />
      <Line
        icon={Layers}
        label="Heavy slots"
        value={`Limit ${limits.heavy_slots}`}
        open={editing === 'slots'}
        onToggle={() => toggle('slots')}
      >
        <Stepper
          value={limits.heavy_slots}
          min={1}
          step={1}
          unit="at once"
          off={null}
          effect={`Heavy test runs wait their turn beyond ${limits.heavy_slots} at once`}
          onChange={(n) => change('Heavy slots', `${n} at once`, 'heavy_slots', n, limits.heavy_slots)}
        />
      </Line>
      <ul className="m-0 mt-1 flex list-none flex-col gap-0.5 border-t border-line p-0 pt-2 text-sm text-ink-2" aria-label="Agents by use">
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
      {said ? (
        <div
          className="limit-said flex items-center justify-between gap-2 rounded-sm bg-raised px-2 py-1.5 text-sm"
          role={said.refused ? 'alert' : 'status'}
        >
          {said.text}
          {said.undo ? (
            <button
              type="button"
              className="cursor-pointer border-0 bg-transparent p-0 text-sm text-acc-ink"
              onClick={() => (said.undo?.(), setSaid(null))}
            >
              Undo
            </button>
          ) : null}
        </div>
      ) : null}
      <a className="mt-1 text-sm" href="#/machine">
        Open Machine ›
      </a>
    </div>
  );
}
