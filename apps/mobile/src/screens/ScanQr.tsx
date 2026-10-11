import { useState } from 'react';
import { Text, View } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { space } from '@handline/tokens';
import { Button } from './parts.tsx';
import { usePalette } from '../theme.ts';

/** The camera, until it reads a QR; `onScan` gets the text once. */
export function ScanQr(props: { onScan: (text: string) => void; onCancel: () => void }) {
  const c = usePalette();
  const [permission, ask] = useCameraPermissions();
  const [done, setDone] = useState(false);
  if (!permission) return null;
  if (!permission.granted)
    return (
      <View style={{ gap: space.md }}>
        <Text style={{ color: c.muted }}>Handline needs the camera to read the code on your computer.</Text>
        <Button label="Allow the camera" onPress={() => void ask()} kind="yes" />
        <Button label="Type the link instead" onPress={props.onCancel} />
      </View>
    );
  return (
    <View style={{ gap: space.md }}>
      <CameraView
        testID="scan-camera"
        style={{ aspectRatio: 1, width: '100%' }}
        barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
        onBarcodeScanned={
          done
            ? undefined
            : (r) => {
                setDone(true);
                props.onScan(r.data);
              }
        }
      />
      <Button label="Type the link instead" onPress={props.onCancel} />
    </View>
  );
}
