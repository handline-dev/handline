// A page's tab strip (Base UI Tabs): pills, the current one on a lifted fill; the page body below is the caller's.
import { Tabs } from '@base-ui/react/tabs';
import type { ReactNode } from 'react';

export interface TabItem<V extends string> {
  value: V;
  label: ReactNode;
}

export function TabStrip<V extends string>({
  label,
  value,
  onChange,
  tabs,
  dense,
}: {
  label: string;
  value: V;
  onChange: (v: V) => void;
  tabs: TabItem<V>[];
  /** No padding round the row, so it fits a bar (the top bar's 44px). */
  dense?: boolean;
}) {
  return (
    <Tabs.Root value={value} onValueChange={(v) => onChange(v as V)}>
      <Tabs.List aria-label={label} className={`chat-tabs flex gap-1 ${dense ? '' : 'px-3 py-2'}`}>
        {tabs.map((t) => (
          <Tabs.Tab
            key={t.value}
            value={t.value}
            className={`chat-tab rounded-ctl border-0 bg-transparent px-3 ${dense ? 'py-0.5' : 'py-1'} text-ink-2 tint data-[active]:bg-raised data-[active]:font-medium data-[active]:text-ink`}
          >
            {t.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
    </Tabs.Root>
  );
}
