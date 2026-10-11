import { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Platform, SafeAreaView, StatusBar as RNStatusBar, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { DataProvider } from '@handline/app-data';
import { DaemonProvider } from '@handline/app-react';
import { forgetPairing, loadOperation, loadPairing, saveOperation, storeFor, type Pairing } from './session.ts';
import { PairScreen } from './screens/PairScreen.tsx';
import { Home } from './screens/Home.tsx';
import { TeamEntry } from './screens/TeamEntry.tsx';
import { usePalette } from './theme.ts';

export default function App() {
  const c = usePalette();
  const [pairing, setPairing] = useState<Pairing | null | undefined>(undefined); // undefined: still reading
  const [operation, setOperation] = useState<string | null | undefined>(undefined); // New agent's page, if the app was left on one
  useEffect(() => {
    void loadPairing().then(setPairing);
    void loadOperation().then(setOperation);
  }, []);
  const store = useMemo(() => (pairing ? storeFor(pairing) : null), [pairing]); // one store per pairing: a theme change re-renders App
  const body =
    pairing === undefined || operation === undefined ? (
      <ActivityIndicator style={{ marginTop: 80 }} />
    ) : !store ? (
      <PairScreen onPaired={setPairing} />
    ) : (
      <DaemonProvider store={store}>
        <DataProvider daemonId={pairing!.host}>
          <Home
            store={store}
            pending={{ operation, save: (op) => void saveOperation(op) }}
            onUnpair={() => {
              void forgetPairing().then(() => (setPairing(null), setOperation(null)));
            }}
          />
          <TeamEntry />
        </DataProvider>
      </DaemonProvider>
    );
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: c.bg, paddingTop: Platform.OS === 'android' ? (RNStatusBar.currentHeight ?? 0) : 0 }}>
      <StatusBar style="auto" />
      <View style={{ flex: 1 }}>{body}</View>
    </SafeAreaView>
  );
}
