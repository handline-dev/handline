// This computer on the Usage tab: the load, the memory free, the disk left and the heavy slots in use.
import { Pressable, Text, View } from 'react-native';
import { DEFAULT_LIMITS, LOW_DISK_BYTES, bytesText, loadFull, memoryLow } from '@handline/app-core';
import { useSettings, useStatus } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';

/** This computer: the load, the memory free, the disk left and the heavy slots in use; Open Machine goes where the limits are. */
export function Machine({ onOpen }: { onOpen?: () => void }) {
  const c = usePalette();
  const r = useStatus().data?.resources;
  const v = (useSettings().data?.values ?? {}) as Partial<typeof DEFAULT_LIMITS>;
  const limits = { ...DEFAULT_LIMITS, ...v };
  if (!r) return null;
  const m = r.machine;
  const diskUsed = m.disk_total_bytes ? Math.round((1 - m.disk_free_bytes / m.disk_total_bytes) * 100) : 0;
  const rows: { key: string; icon: 'load' | 'memory' | 'disk' | 'slots'; label: string; value: string; warn: boolean; pct: number }[] = [
    {
      key: 'load',
      icon: 'load',
      label: 'Load',
      value: `${Math.round(m.load_per_core * 100)}%`,
      warn: loadFull(r, limits.load_per_core_pct),
      pct: (m.load_per_core * 100 / Math.max(300, limits.load_per_core_pct * 1.2, m.load_per_core * 100)) * 100,
    },
    {
      key: 'memory',
      icon: 'memory',
      label: 'Memory',
      value: `${bytesText(m.ram_free_bytes)} free`,
      warn: memoryLow(r, limits.ram_free_min_pct),
      pct: 100 - m.ram_free_pct,
    },
    {
      key: 'disk',
      icon: 'disk',
      label: 'Disk',
      value: `${bytesText(m.disk_free_bytes)} left`,
      warn: m.disk_free_bytes < LOW_DISK_BYTES,
      pct: diskUsed,
    },
    {
      key: 'slots',
      icon: 'slots',
      label: 'Heavy slots',
      value: `${m.heavy_running} of ${limits.heavy_slots}`,
      warn: m.heavy_running >= limits.heavy_slots,
      pct: (m.heavy_running / limits.heavy_slots) * 100,
    },
  ];
  return (
    <View testID="machine" style={{ padding: space.lg, gap: space.sm, borderTopWidth: 1, borderTopColor: c.line }}>
      <Text style={{ color: c.ink, fontWeight: '600' }}>This computer</Text>
      {rows.map((x) => (
        <View key={x.key} testID={`machine-${x.key}`} style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, minHeight: 32 }}>
          <Icon name={x.icon} decorative size={16} color={c.muted} />
          <Text numberOfLines={1} style={{ color: c.ink, width: 96 }}>{x.label}</Text>
          <View style={{ flex: 1, height: 6, borderRadius: 3, backgroundColor: c.line }}>
            <View
              testID={`machine-bar-${x.key}`}
              style={{ width: `${Math.min(100, Math.max(0, x.pct))}%`, height: 6, borderRadius: 3, backgroundColor: x.warn || x.pct >= 85 ? c.block : x.pct >= 60 ? c.wait : c.ok }}
            />
          </View>
          {x.warn ? <Icon name="warn" decorative size={14} color={c.block} /> : null}
          <Text style={{ color: x.warn ? c.block : c.ink }}>{x.value}</Text>
        </View>
      ))}
      {onOpen ? (
        <Pressable testID="machine-open" accessibilityRole="button" onPress={onOpen} style={{ minHeight: 44, justifyContent: 'center' }}>
          <Text style={{ color: c.accent }}>Open Machine ›</Text>
        </Pressable>
      ) : null}
    </View>
  );
}
