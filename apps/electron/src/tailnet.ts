// The gateway can bind loopback or the Tailscale address. With Tailscale up the app uses that address, so a phone on the tailnet reaches the
// same gateway; without it, loopback only.
import type os from 'node:os';

type Ifaces = ReturnType<typeof os.networkInterfaces>;

const inTailnet = (a: string): boolean => {
  const m = /^100\.(\d{1,3})\.\d{1,3}\.\d{1,3}$/.exec(a);
  return !!m && Number(m[1]) >= 64 && Number(m[1]) <= 127;
};

/** The machine's Tailscale IPv4 address: an address in 100.64.0.0/10 on a `utun*` (macOS) or `tailscale*` interface, as the gateway counts it. */
export function tailscaleAddress(ifaces: Ifaces): string | null {
  for (const [name, list] of Object.entries(ifaces)) {
    if (!/^(utun|tailscale)/.test(name)) continue;
    for (const i of list ?? []) if (i.family === 'IPv4' && inTailnet(i.address)) return i.address;
  }
  return null;
}

/** host:port the app's gateway listens on and the window reaches: HANDLINE_GATEWAY if set, else the Tailscale address, else loopback. */
export function gatewayAddress(named: string | undefined, ifaces: Ifaces, port = 7443): string {
  return named ?? `${tailscaleAddress(ifaces) ?? '127.0.0.1'}:${port}`;
}

/** This machine's MagicDNS name (`host.tailnet.ts.net`) out of `tailscale status --json`, or null. Phones allow plain ws:// only for *.ts.net names. */
export function magicDnsName(statusJson: string): string | null {
  try {
    const name = (JSON.parse(statusJson) as { Self?: { DNSName?: unknown } }).Self?.DNSName;
    const n = typeof name === 'string' ? name.replace(/\.$/, '') : '';
    return n.endsWith('.ts.net') ? n : null;
  } catch {
    return null;
  }
}

/** Where the Tailscale command line is: on the PATH, or inside the Mac app. */
export const TAILSCALE_CLIS = ['tailscale', '/Applications/Tailscale.app/Contents/MacOS/Tailscale'];

export const onTailnet = (hostPort: string): boolean => inTailnet(hostPort.split(':')[0] ?? '');
