import { useConnection } from '@handline/app-react';
import { useServes } from '@handline/app-data';

/** Why a control is off (daemon not connected, or the method is not served), or undefined when it works. */
export function useOff(method: string): string | undefined {
  const { served } = useServes(method);
  const open = useConnection() === 'open';
  return !open ? 'daemon not connected' : served ? undefined : `this daemon does not serve ${method}`;
}
