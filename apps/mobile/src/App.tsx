import { UpdateBanner } from '#src/screens/AppUpdate.tsx';
import { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Platform, SafeAreaView, StatusBar as RNStatusBar, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { KeyboardProvider } from 'react-native-keyboard-controller';
import { DataProvider } from '@handline/app-data';
import { DaemonProvider } from '@handline/app-react';
import {
  forgetPairing,
  loadFirstTask,
  loadOperation,
  loadPairing,
  saveFirstTask,
  saveOperation,
  type FirstTask,
  storeFor,
  type Pairing,
} from '#src/session.ts';
import { PairScreen } from '#src/screens/PairScreen.tsx';
import { Home } from '#src/screens/Home.tsx';
import { usePalette } from '#src/theme.ts';

export default function App() {
  const c = usePalette();
  const [pairing, setPairing] = useState<Pairing | null | undefined>(undefined); // undefined: still reading
  const [operation, setOperation] = useState<string | null | undefined>(undefined); // New agent's page, if the app was left on one
  const [first, setFirst] = useState<FirstTask | null>(null);
  useEffect(() => {
    void loadPairing().then(setPairing);
    void Promise.all([loadFirstTask(), loadOperation()]).then(([t, op]) => (setFirst(t), setOperation(op)));
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
            pending={{
              operation,
              first,
              save: (op, t = null) => (setFirst(t), void saveFirstTask(t), void saveOperation(op)),
            }}
            onUnpair={() => {
              void forgetPairing().then(() => (setPairing(null), setOperation(null)));
            }}
          />
        </DataProvider>
      </DaemonProvider>
    );
  return (
    <KeyboardProvider statusBarTranslucent navigationBarTranslucent>
      <SafeAreaView style={{ flex: 1, backgroundColor: c.bg, paddingTop: Platform.OS === 'android' ? (RNStatusBar.currentHeight ?? 0) : 0 }}>
        <StatusBar style="auto" />
        <UpdateBanner />
        <View style={{ flex: 1 }}>{body}</View>
      </SafeAreaView>
    </KeyboardProvider>
  );
}
