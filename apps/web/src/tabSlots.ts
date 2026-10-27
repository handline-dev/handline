// Where a dragged tab lands among the tabs of a bar: the index it takes and the x of the insert line.
import { CHAT, type Group } from '@handline/app-core';

/** The index a tab dropped at `x` takes among the tabs of `group` (all of them, the ones a switch hides included), or -1 for the end; Chat stays first. */
export function slotAt(strip: HTMLElement, group: Group, x: number): number {
  const mids = [...strip.querySelectorAll<HTMLElement>('[role=tab]')].map((el) => {
    const r = el.getBoundingClientRect();
    return r.left + r.width / 2;
  });
  const at = mids.findIndex((m) => x < m);
  return at >= 0 && group.tabs[0] === CHAT ? Math.max(at, 1) : at;
}

/** Where the insert line stands (px from `from`): before the tab at `at`, or after the last shown tab. */
export function lineAt(strip: HTMLElement, at: number, from: number): number {
  const shown = [...strip.querySelectorAll<HTMLElement>('[role=tab]')]
    .map((el) => el.getBoundingClientRect())
    .map((r, i) => ({ r, i }))
    .filter(({ r }) => r.width > 0);
  const next = shown.find(({ i }) => at >= 0 && i >= at);
  const last = shown[shown.length - 1];
  return (next ? next.r.left : last ? last.r.right : strip.getBoundingClientRect().left) - from;
}
