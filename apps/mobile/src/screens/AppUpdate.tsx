// Android only: pick a release channel and install a newer build from GitHub releases. Android asks the user to confirm the install.
import { useCallback, useEffect, useState } from 'react';
import { Text, View } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import { space } from '@handline/tokens';
import ApkUpdate from '#modules/apk-update/index.ts';
import { usePalette } from '#src/theme.ts';
import { checkForUpdate, type Channel, type Update } from '#src/update.ts';
import { Button } from '#src/screens/parts.tsx';

const KEY = 'handline.channel';

/** The channel the user chose; before a choice, the one the installed build came from. */
async function loadChannel(): Promise<Channel> {
  const saved = await SecureStore.getItemAsync(KEY).catch(() => null);
  if (saved === 'nightly' || saved === 'stable') return saved;
  return ApkUpdate?.installedVersionName().includes('nightly') ? 'nightly' : 'stable';
}

export function useAppUpdate() {
  const [channel, setChannel] = useState<Channel | null>(null);
  const [update, setUpdate] = useState<Update | null>(null);
  const [state, setState] = useState<'idle' | 'checking' | 'installing'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const check = useCallback(async (ch: Channel) => {
    if (!ApkUpdate) return;
    setState('checking');
    setError(null);
    try {
      setUpdate(await checkForUpdate(ch, ApkUpdate.installedVersionCode()));
      setChecked(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not check for updates');
    } finally {
      setState('idle');
    }
  }, []);
  useEffect(() => {
    void loadChannel().then((ch) => (setChannel(ch), void check(ch)));
  }, [check]);
  const choose = (ch: Channel) => {
    setChannel(ch);
    setUpdate(null);
    setChecked(false);
    void SecureStore.setItemAsync(KEY, ch).catch(() => {});
    void check(ch);
  };
  const install = async () => {
    if (!ApkUpdate || !update) return;
    if (!ApkUpdate.canInstall()) return ApkUpdate.openInstallSettings();
    setState('installing');
    setError(null);
    try {
      await ApkUpdate.downloadAndInstall(update.url);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not install the update');
    } finally {
      setState('idle');
    }
  };
  return { available: !!ApkUpdate, channel, choose, update, state, error, checked, check, install };
}

/** A line above the app when a newer build of the chosen channel exists. */
export function UpdateBanner() {
  const c = usePalette();
  const u = useAppUpdate();
  if (!u.update) return null;
  return (
    <View testID="update-banner" style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, padding: space.sm, backgroundColor: c.panel }}>
      <Text style={{ flex: 1, color: c.ink }}>Update {u.update.versionName} is ready</Text>
      <Button testID="update-banner-install" label={u.state === 'installing' ? 'Downloading…' : 'Install'} disabled={u.state !== 'idle'} onPress={() => void u.install()} />
    </View>
  );
}

export function AppUpdate() {
  const c = usePalette();
  const u = useAppUpdate();
  if (!u.available) return null;
  const status = u.error
    ? `Not updated: ${u.error}`
    : u.state === 'checking'
      ? 'Checking…'
      : u.update
        ? `Update ${u.update.versionName} is ready`
        : u.checked
          ? 'You have the newest build of this channel.'
          : '';
  return (
    <View testID="app-update" style={{ gap: space.sm }}>
      <Text style={{ color: c.ink, fontWeight: '600' }}>App updates</Text>
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Button testID="channel-nightly" label={u.channel === 'nightly' ? '✓ Nightly' : 'Nightly'} kind={u.channel === 'nightly' ? 'yes' : 'plain'} onPress={() => u.choose('nightly')} />
        <Button testID="channel-stable" label={u.channel === 'stable' ? '✓ Stable' : 'Stable'} kind={u.channel === 'stable' ? 'yes' : 'plain'} onPress={() => u.choose('stable')} />
      </View>
      <Text style={{ color: c.muted, fontSize: 12 }}>Nightly is built every night and may change often. Stable is cut by hand and changes less.</Text>
      {status ? <Text testID="update-status" style={{ color: u.error ? c.block : c.muted }}>{status}</Text> : null}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Button testID="update-check" label="Check now" disabled={u.state !== 'idle' || !u.channel} onPress={() => u.channel && void u.check(u.channel)} />
        {u.update ? <Button testID="update-install" label={u.state === 'installing' ? 'Downloading…' : 'Install'} kind="yes" disabled={u.state !== 'idle'} onPress={() => void u.install()} /> : null}
      </View>
    </View>
  );
}
