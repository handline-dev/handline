// The mobile scan path: ScanQr's permission and once-only scan, and PairScreen pairing from what it scans.
import { act } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { RpcError } from '@handline/client';

const cam = vi.hoisted(() => ({ permission: { granted: false } as { granted: boolean } | null, ask: vi.fn(), scan: undefined as ((r: { data: string }) => void) | undefined }));
vi.mock('expo-camera', () => ({
  useCameraPermissions: () => [cam.permission, cam.ask],
  CameraView: (p: { onBarcodeScanned?: (r: { data: string }) => void }) => {
    cam.scan = p.onBarcodeScanned;
    return <div data-testid="scan-camera" />;
  },
}));
const pairMock = vi.hoisted(() => vi.fn());
vi.mock('../src/session.ts', () => ({ pair: pairMock }));

import { ScanQr } from '../src/screens/ScanQr.tsx';
import { PairScreen } from '../src/screens/PairScreen.tsx';
import { $, box, mount, press, text } from './harness.tsx';

const render = (node: React.ReactNode) => mount(undefined, undefined, undefined, { screen: () => node });

describe('ScanQr', () => {
  it('without the camera permission offers Allow and Type', async () => {
    cam.permission = { granted: false };
    const onCancel = vi.fn();
    await render(<ScanQr onScan={() => {}} onCancel={onCancel} />);
    expect(text()).toContain('Allow the camera');
    expect($('scan-camera')).toBeNull();
    const type = [...box.querySelectorAll<HTMLElement>('[role="button"], button, div')].find((e) => e.textContent === 'Type the link instead');
    await press(type!);
    expect(onCancel).toHaveBeenCalled();
  });

  it('reports a code once even when the camera sees it twice', async () => {
    cam.permission = { granted: true };
    const onScan = vi.fn();
    await render(<ScanQr onScan={onScan} onCancel={() => {}} />);
    await act(async () => cam.scan?.({ data: 'handline://pair?x' }));
    await act(async () => cam.scan?.({ data: 'handline://pair?x' }));
    expect(onScan).toHaveBeenCalledTimes(1);
    expect(onScan).toHaveBeenCalledWith('handline://pair?x');
  });
});

describe('PairScreen scan', () => {
  it('pairs with the scanned text', async () => {
    cam.permission = { granted: true };
    pairMock.mockResolvedValue({ host: 'h' });
    const onPaired = vi.fn();
    await render(<PairScreen onPaired={onPaired} />);
    await press($('pair-scan'));
    await act(async () => cam.scan?.({ data: 'handline://pair?host=h&code=1' }));
    expect(pairMock).toHaveBeenCalledWith('handline://pair?host=h&code=1', 'phone');
    expect(onPaired).toHaveBeenCalledWith({ host: 'h' });
  });

  it('closes the scanner and shows the reason when pairing fails', async () => {
    cam.permission = { granted: true };
    pairMock.mockRejectedValue(new RpcError('transport', 'bad', { reason: 'code expired' }));
    await render(<PairScreen onPaired={() => {}} />);
    await press($('pair-scan'));
    await act(async () => cam.scan?.({ data: 'handline://pair?host=h&code=1' }));
    expect($('scan-camera')).toBeNull();
    expect(text()).toContain('code expired');
  });
});
