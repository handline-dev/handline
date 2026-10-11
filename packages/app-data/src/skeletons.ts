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
  /** The table row: number, from → to, a wide title, a state chip, an age; as tall as the real row. */
  tasks: {
    repeat: 10,
    of: { row: [bar(44), bar(150), { col: [bar('100%')] }, bar(72), bar(40)], gap: space.md, h: rowHeight.task },
  },
  taskDetail: {
    col: [
      bar('55%', 22),
      { row: [bar(72), bar(120), bar(90)], gap: space.md },
      { col: [bar('95%'), bar('88%'), bar('60%')], gap: space.sm },
      { col: [bar('90%'), bar('70%')], gap: space.sm },
      { col: [bar('40%'), bar('50%'), bar('45%')], gap: space.sm },
    ],
    gap: space.lg,
  },
  /** The Worktrees table row: repo, a wide branch, owner, status, file count. */
  worktrees: {
    repeat: 8,
    of: { row: [bar(80), { col: [bar('100%')] }, bar(140), bar(72), bar(48)], gap: space.md, h: rowHeight.task },
  },
  inbox: { repeat: 5, of: block('65%', rowHeight.inbox) },
  accounts: { repeat: 3, of: item(rowHeight.account, [bar('40%'), bar('90%', 12), bar('70%', 12)]) },
  settings: {
    repeat: 6,
    of: { row: [bar('30%', rowHeight.field - space.sm), bar('50%', rowHeight.field - space.sm)], gap: space.md, h: rowHeight.field },
  },
  team: { repeat: 5, of: item(rowHeight.agent, [bar('50%'), bar('30%', 12)]) },
  stats: { col: [bar('100%', 96), bar('100%', 96)], gap: space.md },
  terminal: { col: [bar('100%', 240)], gap: space.sm },
  /** The terminal tab's grid: lines of text, more than any pane shows, clipped by the pane. */
  terminalGrid: { repeat: 60, of: { col: [bar('70%', 14)], h: 22 } },
} satisfies Record<string, SkeletonNode>;

export type SkeletonName = keyof typeof skeletons;
