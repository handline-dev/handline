import { Button } from '#src/ui/index.ts';

/** No-drag, and as tall as the bar less 4px (--h-top follows the zoom in the shell: shell.ts), so a zoomed strip does not clip them. */
const NO_DRAG = 'shell:[-webkit-app-region:no-drag] shell:!h-[calc(var(--h-top)-4px)] shell:!w-[calc(var(--h-top)-4px)]';

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
