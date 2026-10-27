// What a pane reads from the data layer, in one memoized object so the pane's view draws again only when one of these changes.
import { useMemo } from 'react';
import { useHello, useMe } from '@handline/app-react';
import { useWrite } from '@handline/app-data';

export interface PaneData {
  me: string;
  hello: ReturnType<typeof useHello>;
  openShell: (p: { agent: string }) => Promise<{ terminal: string }>;
  closeShell: (p: { terminal: string }) => void;
}

export function usePaneData(agent: string): PaneData {
  const me = useMe();
  const hello = useHello();
  const { mutateAsync: openShell } = useWrite('shell.open', agent);
  const { mutate: closeShell } = useWrite('shell.close', agent);
  return useMemo(() => ({ me, hello, openShell, closeShell }), [me, hello, openShell, closeShell]);
}
