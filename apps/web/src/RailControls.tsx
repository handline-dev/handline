import { createContext, useContext } from 'react';
import { Button } from '#src/ui/index.ts';

/** Set while the sidebar is folded: the buttons the first tab strip carries, so the content keeps the room the rail's handle took. */
export const FoldedControls = createContext<{ show: () => void } | null>(null);
export const useFoldedControls = () => useContext(FoldedControls);

/** No-drag, and as tall as the strip less 4px (--h-tabs follows the zoom in the shell: shell.ts), so a zoomed strip does not clip them. */
const NO_DRAG = 'shell:[-webkit-app-region:no-drag] shell:!h-[calc(var(--h-tabs)-4px)] shell:!w-[calc(var(--h-tabs)-4px)]';

/** Show the sidebar, back and forward: the sidebar's own top row, folded away with it. */
export function RailControls({ show, row }: { show: () => void; row?: boolean }) {
  return (
    <div className={`flex ${row ? 'items-center gap-0.5 self-center' : 'flex-col gap-0.5'}`}>
      <Button className={NO_DRAG} icon="sidebar" label="Show the agent list (⌘B)" onClick={show} />
      <Button className={NO_DRAG} icon="back" label="Back (⌘[)" onClick={() => history.back()} />
      <Button className={NO_DRAG} icon="forward" label="Forward (⌘])" onClick={() => history.forward()} />
    </div>
  );
}
