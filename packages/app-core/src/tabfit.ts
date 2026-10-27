/** The tabs a bar with room for `room` of them shows, in their own order, and the rest for its "more" menu. The active tab is always shown: it takes the place of the last one that fits. */
export function fitTabs(ids: string[], active: string, room: number): { shown: string[]; more: string[] } {
  const n = Math.max(1, Math.floor(room));
  if (ids.length <= n) return { shown: ids, more: [] };
  const keep = new Set(ids.slice(0, n));
  if (ids.includes(active) && !keep.has(active)) {
    keep.delete(ids[n - 1]!);
    keep.add(active);
  }
  return { shown: ids.filter((id) => keep.has(id)), more: ids.filter((id) => !keep.has(id)) };
}
