import { classify, sentence, useWrite } from '@handline/app-data';
import { Button } from './ui/index.ts';

/** The Terminal tab of an agent that has none: one button that starts its session, and the daemon's reason if it will not. */
export function StartTerminal({ agent }: { agent: string }) {
  const start = useWrite('session.start', agent);
  return (
    <div className="flex flex-col items-start gap-2 px-2.5 py-2">
      <Button variant="solid" size="sm" label="Start" disabled={start.isPending} onClick={() => start.mutate({ agent })} />
      {start.isError ? (
        <span className="text-hint text-bad" role="alert">
          {sentence(classify(start.error))}
        </span>
      ) : null}
    </div>
  );
}
