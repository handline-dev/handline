import { afterEach, describe, expect, it } from 'vitest';
import { createRoot } from 'react-dom/client';
import { act } from 'react';
import { Platform, StatusBar } from 'react-native';
import { SafeScreen } from '#src/screens/parts.tsx';

describe('SafeScreen', () => {
  const was = Platform.OS;
  afterEach(() => {
    Platform.OS = was;
  });

  it('pads the status bar height on Android, where SafeAreaView adds nothing', async () => {
    Platform.OS = 'android';
    Object.defineProperty(StatusBar, 'currentHeight', { value: 31, configurable: true });
    const host = document.createElement('div');
    document.body.appendChild(host);
    await act(async () =>
      createRoot(host).render(
        <SafeScreen background="#000" testID="s">
          x
        </SafeScreen>,
      ),
    );
    expect(getComputedStyle(host.querySelector('[data-testid="s"]')!).paddingTop).toBe('31px');
  });
});
