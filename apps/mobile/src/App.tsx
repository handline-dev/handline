import { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Platform, SafeAreaView, StatusBar as RNStatusBar, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import type { Store } from '@handline/app-core';
import { DaemonProvider, useSelector } from '@handline/app-react';
import { forgetPairing, loadPairing, storeFor, type Pairing } from './session.ts';
import { PairScreen } from './screens/PairScreen.tsx';
import { AgentList } from './screens/AgentList.tsx';
import { AgentPage } from './screens/AgentPage.tsx';
import { Banner } from './screens/parts.tsx';
import { usePalette } from './theme.ts';

export default function App() {
  const c = usePalette();
  const [pairing, setPairing] = useState<Pairing | null | undefined>(undefined); // undefined: still reading
  useEffect(() => { void loadPairing().then(setPairing); }, []);
  const store = useMemo(() => (pairing ? storeFor(pairing) : null), [pairing]); // one store per pairing: a theme change re-renders App
  const body = pairing === undefined ? <ActivityIndicator style={{ marginTop: 80 }} />
    : !store ? <PairScreen onPaired={setPairing} />
    : <DaemonProvider store={store}><Home store={store} onUnpair={() => { void forgetPairing().then(() => setPairing(null)); }} /></DaemonProvider>;
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: c.bg, paddingTop: Platform.OS === 'android' ? RNStatusBar.currentHeight ?? 0 : 0 }}>
      <StatusBar style="auto" />
      <View style={{ flex: 1 }}>{body}</View>
    </SafeAreaView>
  );
}

function Home(props: { store: Store; onUnpair: () => void }) {
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
