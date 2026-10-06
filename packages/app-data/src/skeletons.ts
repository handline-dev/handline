// What each view looks like while it loads, as a small tree the shared `Skeleton` draws: the phone and the PC agree on shape.
// A list row is exactly as tall as the real row (`rowHeight` in `@handline/tokens`), so nothing shifts when data arrives.
import { radius, rowHeight, space } from '@handline/tokens';

export type SkeletonNode =
  | { box: { w: number | string; h: number; r?: number } }
  | { row: SkeletonNode[]; gap?: number; h?: number }
  | { col: SkeletonNode[]; gap?: number; h?: number }
  | { repeat: number; of: SkeletonNode };

const bar = (w: number | string, h: number = rowHeight.line): SkeletonNode => ({ box: { w, h, r: radius.sm } });
const dot = (size: number): SkeletonNode => ({ box: { w: size, h: size, r: radius.pill } });
/** One list row of height `h`: a mark, then lines of text. */
const item = (h: number, lines: SkeletonNode[]): SkeletonNode => ({
  row: [dot(h - space.lg), { col: lines, gap: space.xs }],
  gap: space.md,
  h,
});
/** One list row of a single block, as tall as the real row. */
const block = (w: string, h: number): SkeletonNode => ({ col: [bar(w, h - space.sm)], h });

export const skeletons = {
  agents: { repeat: 6, of: item(rowHeight.agent, [bar('60%'), bar('35%', 12)]) },
  agentDetail: { col: [bar('50%', 22), bar('90%'), bar('80%'), bar('70%')], gap: space.md },
  chat: { repeat: 4, of: block('70%', rowHeight.message) },
  tasks: { repeat: 6, of: block('75%', rowHeight.task) },
  inbox: { repeat: 5, of: block('65%', rowHeight.inbox) },
  accounts: { repeat: 3, of: item(rowHeight.account, [bar('40%'), bar('90%', 12), bar('70%', 12)]) },
  settings: {
    repeat: 6,
    of: { row: [bar('30%', rowHeight.field - space.sm), bar('50%', rowHeight.field - space.sm)], gap: space.md, h: rowHeight.field },
  },
  team: { repeat: 5, of: item(rowHeight.agent, [bar('50%'), bar('30%', 12)]) },
  stats: { col: [bar('100%', 96), bar('100%', 96)], gap: space.md },
  terminal: { col: [bar('100%', 240)], gap: space.sm },
} satisfies Record<string, SkeletonNode>;

export type SkeletonName = keyof typeof skeletons;
