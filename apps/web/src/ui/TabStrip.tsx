// A page's tab strip (Base UI Tabs): underlined buttons, the current one in ink; the page body below is the caller's.
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
}: {
  label: string;
  value: V;
  onChange: (v: V) => void;
  tabs: TabItem<V>[];
}) {
  return (
    <Tabs.Root value={value} onValueChange={(v) => onChange(v as V)}>
      <Tabs.List aria-label={label} className="chat-tabs flex gap-1 border-b border-line px-3 pt-1.5">
        {tabs.map((t) => (
          <Tabs.Tab
            key={t.value}
            value={t.value}
            className="chat-tab border-0 border-b-2 border-b-transparent bg-transparent px-3 py-1 text-ink-2 data-[active]:border-b-primary data-[active]:text-ink"
          >
            {t.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
    </Tabs.Root>
  );
}
