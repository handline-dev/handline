import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { daemonUrl } from '../src/daemon.ts';
import { Start } from '../src/Start.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const at = (protocol: string, host: string) => ({ protocol, host, hostname: host.split(':')[0]! });

let root: Root | null = null;
afterEach(() => {
  act(() => root?.unmount());
  root = null;
});

describe('daemonUrl', () => {
  it('uses the fake daemon port in development', () => {
    expect(daemonUrl('', at('http:', 'localhost:5173'))).toEqual({ url: 'ws://localhost:7777', insecure: false });
  });
  it('uses the serving gateway, same origin, in a built page', () => {
    expect(daemonUrl('', at('http:', '127.0.0.1:7443'), true)).toEqual({ url: 'ws://127.0.0.1:7443/rpc', insecure: false });
    expect(daemonUrl('', at('https:', 'box.example:443'), true).url).toBe('wss://box.example:443/rpc');
  });
  it('lets ?daemon= win and still refuses plain ws off this machine', () => {
    expect(daemonUrl('?daemon=ws://localhost:9', at('http:', 'x:1'), true)).toEqual({ url: 'ws://localhost:9', insecure: false });
    expect(daemonUrl('?daemon=ws://lan.example:9', at('http:', 'x:1'), true).insecure).toBe(true);
  });
  it('shows the pairing screen for a built page the gateway served, when no token is stored', async () => {
    const { url, insecure } = daemonUrl('', at('http:', '127.0.0.1:7443'), true);
    const box = document.createElement('div');
    document.body.append(box);
    root = createRoot(box);
    const storage = { getItem: () => null, setItem: () => undefined };
    await act(async () => root!.render(<Start url={url} insecure={insecure} storage={storage} />));
    expect(box.querySelector('.pair-screen input')).not.toBeNull();
  });
});
