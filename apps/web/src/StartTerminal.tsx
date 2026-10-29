// What an agent's terminal pane shows while no terminal runs: one button that starts it. The pane attaches by itself once the daemon records the terminal.
import { useWrite } from '@handline/app-data';
import { Button } from '#src/ui/index.ts';

export function StartTerminal({ agent }: { agent: string }) {
  const start = useWrite('session.start', agent);
  return (
    <div className="start-terminal absolute inset-0 z-10 flex flex-col items-center justify-center gap-2">
      <Button
        variant="solid"
        label={start.isPending ? 'Starting…' : 'Start terminal'}
        disabled={start.isPending}
        onClick={() => start.mutate({ agent })}
      >
        {start.isPending ? 'Starting…' : 'Start terminal'}
      </Button>
      {start.error ? <span className="text-xs text-term-ink">Could not start it: try again</span> : null}
    </div>
  );
}
