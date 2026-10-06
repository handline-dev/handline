import { View } from 'react-native';
import type { Store } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { AgentList } from './AgentList.tsx';
import { AgentPage } from './AgentPage.tsx';
import { Banner } from './parts.tsx';

export function Home(props: { store: Store; onUnpair: () => void }) {
  const open = useSelector((s) => s.page?.agent ?? null);
  const conn = useSelector((s) => s.conn);
  const error = useSelector((s) => s.error);
  return (
    <View style={{ flex: 1 }}>
      {conn !== 'open' && <Banner text={conn === 'closed' ? 'Disconnected' : conn === 'connecting' ? 'Connecting…' : 'Reconnecting…'} />}
      {error && <Banner text={error} error onPress={() => props.store.dismissError()} />}
      {open ? <AgentPage /> : <AgentList onUnpair={props.onUnpair} />}
    </View>
  );
}
