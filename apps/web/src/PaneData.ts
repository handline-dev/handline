// What a pane reads from the data layer, in one place, so the pane's view holds no data hook.
import { useHello, useMe } from '@handline/app-react';
import { useWrite } from '@handline/app-data';

export interface PaneData {
  me: string;
  hello: ReturnType<typeof useHello>;
  openShell: ReturnType<typeof useWrite<'shell.open'>>['mutateAsync'];
  closeShell: ReturnType<typeof useWrite<'shell.close'>>['mutate'];
}

export function usePaneData(agent: string): PaneData {
  const me = useMe();
  const hello = useHello();
  const { mutateAsync: openShell } = useWrite('shell.open', agent);
  const { mutate: closeShell } = useWrite('shell.close', agent);
  return { me, hello, openShell, closeShell };
}
