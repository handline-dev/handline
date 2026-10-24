// The one place the connection is said: a bar above the title bar, from the connection state alone. It clears itself when the daemon answers.
import type { Store } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { Banner } from '#src/screens/parts.tsx';

export function ConnectionBar(props: { store: Store }) {
  const conn = useSelector((s) => s.conn);
  const unreachable = useSelector((s) => s.unreachable);
  if (conn === 'open') return null;
  if (conn === 'closed')
    return (
      <Banner
        text="Disconnected"
        action="Retry"
        onPress={() =>
          void props.store.start().then(
            () => props.store.setUnreachable(false),
            () => props.store.setUnreachable(true),
          )
        }
      />
    );
  return <Banner text={unreachable ? 'Cannot reach the daemon - retrying' : conn === 'connecting' ? 'Connecting…' : 'Reconnecting…'} />;
}
